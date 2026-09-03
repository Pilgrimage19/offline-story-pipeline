"""前向一致性验证（v0.2 状态流）。

验证命题：只跑前 p 个 unit 的增量结果，必须与整本跑出的前 p 个结果
（summary / entity_updates / plotline_updates / context_refs / recent_event /
degraded / state_snapshot）逐字段一致。

通过 ⇒ 离线整本处理等价于未来在线随读增量（同一状态机、同一缓存链）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .config import WorkPaths
from .extract import run_chain
from .llm import LLMExtractor
from .extract import MockExtractor
from .model import load_json, load_units

logger = logging.getLogger(__name__)

_COMPARE_FIELDS = (
    "summary",
    "entity_updates",
    "plotline_updates",
    "context_refs",
    "recent_event",
    "degraded",
)


def _extractor_matching(meta: dict) -> object:
    """按产物 meta 记录的抽取器重建同款，保证与整本跑使用的逻辑一致。"""
    name = meta.get("extractor")
    if name == "mock":
        return MockExtractor()
    if name == "llm":
        return LLMExtractor()  # 缺 key 会抛 RuntimeError
    raise RuntimeError(f"未知 extractor: {name!r}")


def check_prefix_consistency(
    work_id: str,
    data_root: Path,
    prefixes: tuple[int, ...] = (10, 26, 30, 60, 108),
) -> dict:
    paths = WorkPaths(data_root, work_id)
    extracted_file = paths.extracted_dir / "units_extracted.jsonl"
    meta = load_json(paths.extracted_dir / "meta.json", {})
    if not extracted_file.exists() or not meta:
        return {"work_id": work_id, "ok": False,
                "error": "缺少 03_extracted 产物，请先跑 extract 阶段"}

    extractor = _extractor_matching(meta)

    stored = {u.order: u.to_dict() for u in load_units(extracted_file)}
    results = []
    for p in prefixes:
        if p > len(stored):
            continue
        units_run, _state, stats = run_chain(work_id, data_root, extractor, limit=p)
        diffs: list[tuple[int, str]] = []
        for u in units_run:
            s = stored.get(u.order)
            if s is None:
                diffs.append((u.order, "missing_in_full_run"))
                continue
            d = u.to_dict()
            for k in _COMPARE_FIELDS:
                if d.get(k) != s.get(k):
                    diffs.append((u.order, k))
            if (d.get("state_snapshot") or None) != (s.get("state_snapshot") or None):
                diffs.append((u.order, "state_snapshot"))
        results.append({
            "prefix": p,
            "ok": not diffs,
            "units": len(units_run),
            "diffs": diffs[:8],
            "diff_count": len(diffs),
        })
    ok = all(r["ok"] for r in results)
    return {"work_id": work_id, "ok": ok, "prefixes": results}
