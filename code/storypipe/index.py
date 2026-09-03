"""Stage 03/02 -> 05_index：本地检索索引（SQLite + FTS5，BM25）。

设计：索引表只存 unit 标识 + 可检索文本（text/summary）；
原文以 03_extracted/units_extracted.jsonl 为 source of truth，
检索时由 StoryMemory Adapter 回表取原文（provenance 在 JSONL 侧）。

FTS5 不可用（罕见）时自动降级：只建普通 units 表，
Adapter 会退化为纯 Python 扫描打分，全链路仍可用。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import WorkPaths
from .model import load_units

_TABLE_SQL = """
DROP TABLE IF EXISTS units_fts;
DROP TABLE IF EXISTS units;
CREATE TABLE units (
    unit_id TEXT PRIMARY KEY,
    ord     INTEGER NOT NULL,
    chapter TEXT,
    text    TEXT NOT NULL,
    summary TEXT DEFAULT ''
);
"""

_FTS_SQL = """
CREATE VIRTUAL TABLE units_fts USING fts5(
    unit_id UNINDEXED,
    ord UNINDEXED,
    chapter UNINDEXED,
    text,
    summary,
    tokenize = 'unicode61'
);
"""


def build_index(work_id: str, data_root: Path) -> dict:
    paths = WorkPaths(data_root, work_id)
    paths.ensure()

    # 抽取产物优先；未跑 extract 时退化为用 02 的纯文本单元
    extracted = paths.extracted_dir / "units_extracted.jsonl"
    units_src = extracted if extracted.exists() else paths.segmented_dir / "units.jsonl"
    if not units_src.exists():
        raise FileNotFoundError(f"{work_id}: 缺少 {units_src}，请先跑 segment/extract 阶段")
    units = load_units(units_src)
    if not units:
        raise ValueError(f"{work_id}: 无单元可索引")

    db_path = paths.index_dir / "story.db"
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.executescript(_TABLE_SQL)

        fts_ok = True
        try:
            cur.execute(_FTS_SQL)
        except sqlite3.OperationalError as e:
            if "no such module" in str(e).lower():
                fts_ok = False  # 仅当 FTS5 模块缺失时降级为纯扫描
                print(f"[index] FTS5 不可用（{e}），降级为纯扫描模式")
            else:
                raise

        rows = [(u.unit_id, u.order, u.chapter_name, u.text, u.summary) for u in units]
        cur.executemany("INSERT INTO units (unit_id, ord, chapter, text, summary) VALUES (?,?,?,?,?)", rows)
        if fts_ok:
            cur.executemany("INSERT INTO units_fts (unit_id, ord, chapter, text, summary) VALUES (?,?,?,?,?)", rows)
        conn.commit()
    finally:
        conn.close()

    return {
        "work_id": work_id,
        "units": len(units),
        "fts5": fts_ok,
        "db_path": str(db_path),
        "source": units_src.name,
    }
