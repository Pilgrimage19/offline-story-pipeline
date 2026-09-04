"""文本向量索引：本地 embedding + SQLite 持久化。

向量库只是 source of truth 的可重建 side index。默认使用适合中文检索的
BAAI/bge-small-zh-v1.5；模型首次使用时由 sentence-transformers 下载，之后可离线运行。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from array import array
from pathlib import Path
from typing import Protocol, Sequence

from .config import WorkPaths
from .model import StoryUnit, load_units

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
VECTOR_DB_NAME = "vectors.db"
VECTOR_INDEX_VERSION = "story-vector@0.1"
_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："


class Embedder(Protocol):
    model_name: str

    def encode(self, texts: Sequence[str], *, is_query: bool = False) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    """sentence-transformers 的延迟加载包装，避免非向量流程依赖重型包。"""

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
        self._model = SentenceTransformer(
            self.model_name,
            local_files_only=model_path.exists(),
        )

    def encode(self, texts: Sequence[str], *, is_query: bool = False) -> list[list[float]]:
        # bge-m3 本身不要求检索指令；bge v1.5 系列查询使用官方推荐中文前缀。
        use_prefix = "bge-m3" not in self.model_name.lower()
        inputs = [(_QUERY_PREFIX + t) if is_query and use_prefix else t for t in texts]
        vectors = self._model.encode(
            inputs,
            normalize_embeddings=True,
            show_progress_bar=len(inputs) > 16,
        )
        return [list(map(float, row)) for row in vectors]


def source_file(paths: WorkPaths) -> Path:
    extracted = paths.extracted_dir / "units_extracted.jsonl"
    return extracted if extracted.exists() else paths.segmented_dir / "units.jsonl"


def source_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def retrieval_text(unit: StoryUnit) -> str:
    """组合用于 embedding 的文本；原文仍由 JSONL 回表，不复制为事实源。"""
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


def _pack(vector: Sequence[float]) -> bytes:
    return array("f", vector).tobytes()


def _unpack(blob: bytes) -> array:
    values = array("f")
    values.frombytes(blob)
    return values


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    an = math.sqrt(sum(x * x for x in a))
    bn = math.sqrt(sum(y * y for y in b))
    return dot / (an * bn) if an and bn else -1.0


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

    db_path = paths.index_dir / VECTOR_DB_NAME
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            DROP TABLE IF EXISTS vector_units;
            DROP TABLE IF EXISTS vector_meta;
            CREATE TABLE vector_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE vector_units (
                unit_id TEXT PRIMARY KEY,
                ord INTEGER NOT NULL,
                chapter TEXT NOT NULL,
                embedding BLOB NOT NULL
            );
            CREATE INDEX idx_vector_units_order ON vector_units(ord);
            """
        )
        meta = {
            "index_version": VECTOR_INDEX_VERSION,
            "model": encoder.model_name,
            "dimension": dim,
            "source_file": src.name,
            "source_sha256": source_sha256(src),
            "unit_count": len(units),
        }
        conn.executemany(
            "INSERT INTO vector_meta(key, value) VALUES (?, ?)",
            [(k, json.dumps(v, ensure_ascii=False)) for k, v in meta.items()],
        )
        conn.executemany(
            "INSERT INTO vector_units(unit_id, ord, chapter, embedding) VALUES (?, ?, ?, ?)",
            [(u.unit_id, u.order, u.chapter_name, _pack(v)) for u, v in zip(units, vectors)],
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "work_id": work_id,
        "units": len(units),
        "model": encoder.model_name,
        "dimension": dim,
        "db_path": str(db_path),
        "source": src.name,
    }


def read_vector_meta(db_path: Path) -> dict:
    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT key, value FROM vector_meta").fetchall()
        finally:
            conn.close()
        return {key: json.loads(value) for key, value in rows}
    except (sqlite3.Error, json.JSONDecodeError):
        return {}


def search_vector_index(
    db_path: Path,
    query_vector: Sequence[float],
    *,
    max_order: int | None = None,
    chapter: str = "",
    limit: int = 200,
) -> list[tuple[str, float]]:
    """小规模语料直接精确余弦扫描；数百单元无需引入 ANN 服务。"""
    conn = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
    try:
        sql = "SELECT unit_id, embedding FROM vector_units WHERE 1=1"
        params: list[object] = []
        if max_order is not None:
            sql += " AND ord <= ?"
            params.append(max_order)
        if chapter:
            sql += " AND chapter LIKE ?"
            params.append(f"%{chapter}%")
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    scored = [(unit_id, _cosine(query_vector, _unpack(blob))) for unit_id, blob in rows]
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[: max(0, limit)]
