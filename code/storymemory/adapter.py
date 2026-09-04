"""StoryMemory Adapter：Chatbot 侧唯一入口（接口冻结，勿直接读底层表）。

读取路径：
    source of truth : data/<work_id>/03_extracted/units_extracted.jsonl（回原文）
    检索索引        : data/<work_id>/05_index/story.db（SQLite FTS5, BM25）
    向量索引        : data/<work_id>/05_index/vectors.lance（LanceDB 本地表）
默认优先向量检索；索引/依赖缺失时自动退化到 FTS5，再退化为 Python 扫描，
保证 pipeline 未建索引也能用；接口行为不变。

用法：
    sm = StoryMemory()                       # data_root 默认仓库 data/
    sm.list_works()
    sm.search("wandering_earth", "为什么建造地球发动机", max_order=80)
    sm.get_unit("wandering_earth", "we-0001")
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from storypipe.config import DATA_ROOT_DEFAULT, WORK_META, WorkPaths
from storypipe.model import StoryUnit, load_json, load_units
from storypipe.textutil import content_chars, fts_expr, unique_bigrams
from storypipe.vector_index import (
    SentenceTransformerEmbedder,
    read_vector_meta,
    search_vector_index,
    source_file,
    source_sha256,
)

from .evidence import StoryEvidence


class StoryMemory:
    def __init__(self, data_root: Path = DATA_ROOT_DEFAULT, retrieval: str = "auto") -> None:
        if retrieval not in {"auto", "vector", "fts"}:
            raise ValueError("retrieval 必须是 auto、vector 或 fts")
        self.data_root = Path(data_root)
        self.retrieval = retrieval
        self._units_cache: dict[str, list[StoryUnit]] = {}
        self._embedders: dict[str, SentenceTransformerEmbedder] = {}

    # ---------- 数据读取 ----------

    def _units_file(self, work_id: str) -> Path | None:
        wdir = self.data_root / work_id
        for rel in ("03_extracted/units_extracted.jsonl", "02_segmented/units.jsonl"):
            p = wdir / rel
            if p.exists():
                return p
        return None

    def _load_units(self, work_id: str) -> list[StoryUnit]:
        if work_id not in self._units_cache:
            f = self._units_file(work_id)
            self._units_cache[work_id] = load_units(f) if f else []
        return self._units_cache[work_id]

    # ---------- 冻结接口 ----------

    def list_works(self) -> list[dict]:
        """返回已处理作品清单。"""
        out = []
        for wdir in sorted(self.data_root.iterdir()):
            if not wdir.is_dir():
                continue
            f = self._units_file(wdir.name)
            if not f:
                continue
            units = self._load_units(wdir.name)
            meta = load_json(wdir / "01_normalized" / "meta.json", {})
            title = WORK_META.get(wdir.name, {}).get("title") or meta.get("title", "")
            out.append({
                "work_id": wdir.name,
                "title": title,
                "unit_count": len(units),
                "has_index": (wdir / "05_index" / "story.db").exists(),
                "has_vector_index": (wdir / "05_index" / "vectors.lance").exists(),
            })
        return out

    def search(
        self,
        work_id: str,
        query: str,
        *,
        max_order: Optional[int] = None,
        top_k: int = 8,
        filters: Optional[dict] = None,
    ) -> list[dict]:
        """在给定作品内检索，返回统一 Evidence 列表。

        max_order : 只返回 order <= max_order 的单元（防剧透在检索层完成）；
        filters   : V0 支持 {"chapter": str}（按章节名包含过滤），其余键忽略。
        """
        filters = filters or {}
        chapter_filter = str(filters.get("chapter", "")).strip()
        units = self._load_units(work_id)
        if not units:
            return []

        by_id = {u.unit_id: u for u in units}
        rows = None
        if self.retrieval in {"auto", "vector"}:
            rows = self._vector_rank(work_id, query, max_order, chapter_filter)
            if rows is None and self.retrieval == "vector":
                raise RuntimeError(
                    "向量索引不可用、已陈旧或本地 embedding 模型无法加载；"
                    "请重新运行 vector-index，或改用 retrieval='auto'"
                )
        if rows is None and self.retrieval in {"auto", "fts"}:
            rows = self._fts_rank(work_id, query, max_order, chapter_filter)
        if rows is not None:
            scored = [(by_id[uid], float(score)) for uid, score in rows if uid in by_id]
        else:
            scored = self._scan_rank(units, query, max_order, chapter_filter)

        return [self._to_evidence(u, s).to_dict() for u, s in scored[: max(0, top_k)]]

    def get_unit(self, work_id: str, unit_id: str) -> Optional[dict]:
        """按稳定 unit_id 取回单个单元（Evidence 形状，score=1.0 占位）。"""
        for u in self._load_units(work_id):
            if u.unit_id == unit_id:
                return self._to_evidence(u, 1.0).to_dict()
        return None

    # ---------- 内部实现 ----------

    def _vector_rank(
        self,
        work_id: str,
        query: str,
        max_order: Optional[int],
        chapter_filter: str,
    ) -> Optional[list[tuple[str, float]]]:
        db = self.data_root / work_id / "05_index" / "vectors.lance"
        meta = read_vector_meta(db)
        if not meta:
            return None
        paths = WorkPaths(self.data_root, work_id)
        src = source_file(paths)
        if not src.exists() or meta.get("source_sha256") != source_sha256(src):
            return None  # source of truth 已变化，拒绝使用陈旧向量
        model = str(meta.get("model", ""))
        if not model:
            return None
        try:
            if model not in self._embedders:
                self._embedders[model] = SentenceTransformerEmbedder(model)
            query_vectors = self._embedders[model].encode([query], is_query=True)
            if not query_vectors:
                return None
            return search_vector_index(
                db,
                query_vectors[0],
                max_order=max_order,
                chapter=chapter_filter,
            )
        except (RuntimeError, OSError, ValueError, sqlite3.Error):
            return None

    def _to_evidence(self, u: StoryUnit, score: float) -> StoryEvidence:
        return StoryEvidence(
            work_id=u.work_id,
            unit_id=u.unit_id,
            order=u.order,
            raw_text=u.text,
            summary=u.summary,
            score=round(float(score), 4),
            metadata={
                "chapter": u.chapter_name,
                "chapter_idx": u.chapter_idx,
                "start_line": u.start_line,
                "end_line": u.end_line,
                "characters": u.characters,
                "locations": u.locations,
                "key_terms": u.key_terms,
            },
        )

    def _fts_rank(
        self,
        work_id: str,
        query: str,
        max_order: Optional[int],
        chapter_filter: str,
    ) -> Optional[list[tuple[str, float]]]:
        """FTS5 BM25 打分；任何不可用（缺库/缺表/表达式空/查询失败）返回 None。"""
        db = self.data_root / work_id / "05_index" / "story.db"
        if not db.exists():
            return None
        expr = fts_expr(query)
        if not expr:
            return None
        try:
            conn = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
        except sqlite3.Error:
            return None
        try:
            cur = conn.cursor()
            has_fts = cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='units_fts'"
            ).fetchone()
            if not has_fts:
                return None
            sql = "SELECT unit_id, -bm25(units_fts) AS score FROM units_fts WHERE units_fts MATCH ?"
            params: list = [expr]
            if max_order is not None:
                sql += " AND ord <= ?"
                params.append(max_order)
            if chapter_filter:
                sql += " AND chapter LIKE ?"
                params.append(f"%{chapter_filter}%")
            sql += " ORDER BY score DESC LIMIT 200"
            rows = cur.execute(sql, params).fetchall()
        except sqlite3.Error:
            return None
        finally:
            conn.close()
        if not rows:
            return None
        return [(r[0], r[1]) for r in rows]

    def _scan_rank(
        self,
        units: list[StoryUnit],
        query: str,
        max_order: Optional[int],
        chapter_filter: str,
    ) -> list[tuple[StoryUnit, float]]:
        """纯扫描降级：按 query 二元组在 text/summary 中出现次数打分（与 FTS 同策略）。"""
        chars = content_chars(query)
        bigrams = unique_bigrams(chars)
        scored: list[tuple[StoryUnit, float]] = []
        for u in units:
            if max_order is not None and u.order > max_order:
                continue
            if chapter_filter and chapter_filter not in u.chapter_name:
                continue
            s = 0.0
            if bigrams:
                for b in bigrams:
                    s += u.text.count(b) * 2.0 + u.summary.count(b)
            else:
                for c in set(chars):
                    if c in u.text:
                        s += 1.0
            if s > 0:
                scored.append((u, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:200]
