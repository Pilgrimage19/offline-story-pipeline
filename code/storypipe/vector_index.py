"""LanceDB 文本向量索引。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Protocol, Sequence

from .config import WorkPaths
from .model import StoryUnit, load_units

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
VECTOR_DB_NAME = "vectors.lance"
VECTOR_TABLE_NAME = "story_units"
VECTOR_INDEX_VERSION = "story-vector@0.2-lancedb"
_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："


class Embedder(Protocol):
    model_name: str

    def encode(self, texts: Sequence[str], *, is_query: bool = False) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    """sentence-transformers 的延迟加载包装，支持模型 ID 或本地目录。"""

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or os.environ.get(
            "STORYPIPE_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL
        )
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise RuntimeError(
                "sentence-transformers 无法导入（未安装或依赖损坏）；请运行 "
                f"pip install -r requirements-vector.txt。原始错误: {e}"
            ) from e
        model_path = Path(self.model_name)
        self._model = SentenceTransformer(self.model_name, local_files_only=model_path.exists())

    def encode(self, texts: Sequence[str], *, is_query: bool = False) -> list[list[float]]:
        use_prefix = "bge-m3" not in self.model_name.lower()
        inputs = [(_QUERY_PREFIX + t) if is_query and use_prefix else t for t in texts]
        vectors = self._model.encode(
            inputs, normalize_embeddings=True, show_progress_bar=len(inputs) > 16
        )
        return [list(map(float, row)) for row in vectors]


def source_file(paths: WorkPaths) -> Path:
    extracted = paths.extracted_dir / "units_extracted.jsonl"
    return extracted if extracted.exists() else paths.segmented_dir / "units.jsonl"


def source_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def retrieval_text(unit: StoryUnit) -> str:
    parts = [f"章节：{unit.chapter_name}"]
    if unit.summary:
        parts.append(f"摘要：{unit.summary}")
    if unit.characters:
        parts.append("人物：" + "、".join(unit.characters))
    if unit.locations:
        parts.append("地点：" + "、".join(unit.locations))
    if unit.key_terms:
        parts.append("关键词：" + "、".join(unit.key_terms))
    parts.append("原文：" + unit.text)
    return "\n".join(parts)


def _meta_path(db_path: Path) -> Path:
    return db_path / "_meta.json"


def build_vector_index(
    work_id: str,
    data_root: Path,
    *,
    embedder: Embedder | None = None,
    model_name: str | None = None,
) -> dict:
    paths = WorkPaths(data_root, work_id)
    paths.ensure()
    src = source_file(paths)
    if not src.exists():
        raise FileNotFoundError(f"{work_id}: 缺少分段/抽取产物，请先运行 segment 或 extract")
    units = load_units(src)
    if not units:
        raise ValueError(f"{work_id}: 无单元可建立向量索引")
    encoder = embedder or SentenceTransformerEmbedder(model_name)
    vectors = encoder.encode([retrieval_text(u) for u in units])
    if len(vectors) != len(units) or not vectors or not vectors[0]:
        raise ValueError("embedding 返回数量或维度不正确")
    dim = len(vectors[0])
    if any(len(v) != dim for v in vectors):
        raise ValueError("embedding 维度不一致")
    try:
        import lancedb
    except ImportError as e:
        raise RuntimeError("LanceDB 未安装；请运行 pip install -r requirements-vector.txt") from e
    db_path = paths.index_dir / VECTOR_DB_NAME
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    rows = [
        {"unit_id": u.unit_id, "ord": int(u.order), "chapter": u.chapter_name,
         "vector": list(map(float, v))}
        for u, v in zip(units, vectors)
    ]
    db.create_table(VECTOR_TABLE_NAME, data=rows, mode="overwrite")
    meta = {
        "index_version": VECTOR_INDEX_VERSION, "model": encoder.model_name,
        "dimension": dim, "source_file": src.name,
        "source_sha256": source_sha256(src), "unit_count": len(units),
        "table": VECTOR_TABLE_NAME,
    }
    _meta_path(db_path).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"work_id": work_id, "units": len(units), "model": encoder.model_name,
            "dimension": dim, "db_path": str(db_path), "source": src.name}


def read_vector_meta(db_path: Path) -> dict:
    try:
        return json.loads(_meta_path(db_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def search_vector_index(
    db_path: Path,
    query_vector: Sequence[float],
    *,
    max_order: int | None = None,
    chapter: str = "",
    limit: int = 200,
) -> list[tuple[str, float]]:
    """使用 LanceDB 向量查询；where 条件在向量查询阶段执行。"""
    try:
        import lancedb
    except ImportError as e:
        raise RuntimeError("LanceDB 未安装；请运行 pip install -r requirements-vector.txt") from e
    db = lancedb.connect(str(db_path))
    table = db.open_table(VECTOR_TABLE_NAME)
    query = table.search(list(map(float, query_vector)), query_type="vector").metric("cosine")
    predicates = []
    if max_order is not None:
        predicates.append(f"ord <= {int(max_order)}")
    if chapter:
        safe_chapter = chapter.replace("'", "''")
        predicates.append(f"chapter LIKE '%{safe_chapter}%'")
    if predicates:
        query = query.where(" AND ".join(predicates))
    result = query.limit(max(0, limit)).to_list()
    # LanceDB 返回 cosine distance（越小越相似）；Adapter 对外统一暴露 similarity score。
    return [(str(row["unit_id"]), 1.0 - float(row.get("_distance", 1.0))) for row in result]
