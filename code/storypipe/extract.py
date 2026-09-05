"""Stage 02_segmented -> 03_extracted：状态流抽取（顺序状态机，v0.2）。

    process(unit_k, state_{k-1})  ->  (unit_k 的理解, state_k)

- 每个 unit 的 LLM 输入 = 本段原文 + 章节 + state 精简视图 + 最近 1~2 段原文；
- LLM 只产出增量，state 合并/记账由 storypipe.state 确定性完成；
- 每个 unit 落一份 state_snapshot（= state_k）；
- 每 chapter 结束做一次章节压缩（backdrop_buffer -> backdrop）并落检查点；
- 缓存键含前序 state 指纹：state 变化 ⇒ 下游链缓存自动失效（连锁重抽是固有代价）；
- 单 unit 失败：降级且不更新 state，标记 degraded（缓存回放保留标记）。

详情：docs/story_state_schema_v0.2.md
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Optional, Protocol

from . import state as state_mod
from .config import PIPELINE_VERSION, SCHEMA_VERSION, WorkPaths
from .llm import LLMExtractor, PROMPT_VERSION, _fallback_fields
from .model import StoryUnit, load_units, save_json, save_units

logger = logging.getLogger(__name__)

_CACHE_IDENTITY_FILE = "_identity.json"


class UnitExtractor(Protocol):
    name: str

    def extract_unit(self, unit: StoryUnit, state_view: str = "", recent_raw: str = "") -> dict: ...

    def compress_backdrop(
        self, work_id: str, chapter_name: str, backdrop: str, buffer_events: list[dict]
    ) -> str: ...


class MockExtractor:
    """无 LLM 时的降级抽取（机械、确定性，仅验证管道机制，不验证语义）。

    - summary / recent_event = 首句截断；
    - 实体/事件线更新为空；
    - 章节压缩 = buffer 文本拼接。
    """

    name = "mock"

    def cache_identity(self) -> dict:
        return {"extractor": self.name, "implementation": "fallback-first-sentence-v1"}

    def extract_unit(self, unit: StoryUnit, state_view: str = "", recent_raw: str = "") -> dict:
        fields = _fallback_fields(unit)
        return fields

    def compress_backdrop(
        self, work_id: str, chapter_name: str, backdrop: str, buffer_events: list[dict]
    ) -> str:
        joined = "；".join(f"@{e.get('order')} {e.get('text','')}" for e in buffer_events)
        return joined[:200]


class ResultCache:
    def __init__(self, cache_root: Path, identity: dict) -> None:
        self.identity = identity
        identity_json = json.dumps(identity, ensure_ascii=False, sort_keys=True)
        namespace = self.digest(identity_json)[:16]
        self.cache_dir = Path(cache_root) / namespace
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        marker = self.cache_dir / _CACHE_IDENTITY_FILE
        if not marker.exists():
            marker.write_text(
                json.dumps(identity, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )

    @staticmethod
    def digest(*parts: str) -> str:
        payload = "\n".join(parts).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def chain_key(self, prompt_version: str, state_fp: str, text: str, prev_texts: list[str]) -> str:
        return self.digest(prompt_version, state_fp, text, *prev_texts)

    def get(self, key: str) -> dict | None:
        p = self.cache_dir / f"{key}.json"
        if not p.exists():
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def put(self, key: str, fields: dict) -> None:
        with open(self.cache_dir / f"{key}.json", "w", encoding="utf-8") as f:
            json.dump(fields, f, ensure_ascii=False)


def _cache_identity(extractor: UnitExtractor) -> dict:
    """返回会影响抽取结果的配置；任何变化都会进入独立缓存命名空间。"""
    identity_fn = getattr(extractor, "cache_identity", None)
    specific = identity_fn() if callable(identity_fn) else {"extractor": extractor.name}
    return {
        "prompt_version": PROMPT_VERSION,
        "state_schema_version": state_mod.STATE_SCHEMA_VERSION,
        **specific,
    }


def make_extractor() -> UnitExtractor:
    """按环境选择抽取器：STORYPIPE_EXTRACTOR = auto|mock|llm（默认 auto）。"""
    mode = os.environ.get("STORYPIPE_EXTRACTOR", "auto")
    if mode == "mock":
        return MockExtractor()
    if mode == "llm":
        return LLMExtractor()  # 缺 key 时抛 RuntimeError
    if os.environ.get("OPENAI_API_KEY"):
        return LLMExtractor()
    return MockExtractor()


def _compress_chapter(
    cache: ResultCache,
    extractor: UnitExtractor,
    work_id: str,
    chapter_name: str,
    state: dict,
    stats: dict,
) -> bool:
    """章节边界：backdrop_buffer -> backdrop。失败时保留 buffer，返回 False。"""
    if not state.get("backdrop_buffer"):
        return True
    fp = state_mod.fingerprint(state)
    key = cache.digest(PROMPT_VERSION, "compress", chapter_name, fp)
    hit = cache.get(key)
    if hit is not None and isinstance(hit, dict) and "backdrop" in hit:
        compressed = str(hit.get("backdrop", ""))
        stats["compress_cached"] += 1
    else:
        try:
            compressed = extractor.compress_backdrop(
                work_id, chapter_name, state.get("backdrop", ""), state.get("backdrop_buffer", [])
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("chapter %s 压缩失败（%s），保留 buffer", chapter_name, e)
            return False
        compressed = (compressed or "")[: state_mod.BACKDROP_MAX_CHARS]
        cache.put(key, {"backdrop": compressed})
        stats["compress_fresh"] += 1
    state_mod.archive_buffer(state, compressed)
    return True


def _checkpoint(paths: WorkPaths, state: dict, chapter_idx: int) -> None:
    p = paths.extracted_dir / f"checkpoint_ch{chapter_idx:02d}.json"
    state_mod.save_checkpoint(p, state)


def run_chain(
    work_id: str,
    data_root: Path,
    extractor: UnitExtractor,
    force: bool = False,
    limit: Optional[int] = None,
) -> tuple[list[StoryUnit], dict, dict]:
    """按 order 顺序推进状态机，返回 (处理过的 units, 最终 state, 统计)。

    limit 用于前向一致性验证：只处理前 limit 个 unit（走缓存，应与整本跑出的前
    limit 个结果逐字段一致）。
    """
    paths = WorkPaths(data_root, work_id)
    paths.ensure()
    units_file = paths.segmented_dir / "units.jsonl"
    if not units_file.exists():
        raise FileNotFoundError(f"{work_id}: 缺少 {units_file}，请先跑 segment 阶段")
    all_units = load_units(units_file)
    if not all_units:
        raise ValueError(f"{work_id}: units.jsonl 为空")

    n = len(all_units) if limit is None else max(0, min(int(limit), len(all_units)))
    cache_identity = _cache_identity(extractor)
    cache = ResultCache(paths.extracted_dir / "cache", cache_identity)

    state = state_mod.new_state(work_id)
    stats = {
        "units": n,
        "cached": 0,
        "fresh": 0,
        "degraded": 0,
        "compress_cached": 0,
        "compress_fresh": 0,
    }
    processed: list[StoryUnit] = []
    prev_chapter: Optional[int] = None

    for idx in range(n):
        u = all_units[idx]
        if prev_chapter is not None and u.chapter_idx != prev_chapter:
            previous_chapter_name = processed[-1].chapter_name
            _compress_chapter(cache, extractor, work_id, previous_chapter_name, state, stats)
            processed[-1].state_snapshot = state_mod.clone(state)
            _checkpoint(paths, state, prev_chapter)
        prev_chapter = u.chapter_idx

        fp = state_mod.fingerprint(state)
        prev_texts = [all_units[j].text for j in range(max(0, idx - 2), idx)]
        view = state_mod.state_view(state)
        key = cache.chain_key(PROMPT_VERSION, fp, u.text, prev_texts)

        fields: Optional[dict] = None if force else cache.get(key)
        degraded = False
        if fields is not None:
            stats["cached"] += 1
        else:
            try:
                fields = extractor.extract_unit(u, state_view=view, recent_raw="\n".join(prev_texts))
            except Exception as e:  # noqa: BLE001 —— 单单元失败不阻塞整篇
                logger.warning("unit %s 抽取失败（%s），降级且不更新 state", u.unit_id, e)
                fields = _fallback_fields(u)
                degraded = True
                stats["degraded"] += 1
            if degraded:
                fields["degraded"] = True
            # 临时 API/解析错误产生的降级结果不能污染正常缓存。
            if not degraded:
                cache.put(key, fields)
            stats["fresh"] += 1
        degraded = bool(fields.get("degraded", False))

        # 状态推进：degraded 单元不更新 state
        new_state = state_mod.clone(state)
        if not degraded:
            state_mod.apply_unit_output(new_state, fields, u.order)
        state = new_state

        # 回写 unit 产物
        u.summary = str(fields.get("summary", "") or "")
        u.entity_updates = list(fields.get("entity_updates", []) or [])
        u.plotline_updates = list(fields.get("plotline_updates", []) or [])
        u.context_refs = list(fields.get("context_refs", []) or [])
        u.recent_event = fields.get("recent_event")
        u.degraded = degraded
        u.characters = [
            e["name"] for e in u.entity_updates
            if isinstance(e, dict) and e.get("type", "character") == "character"
        ]
        u.locations = [
            e["name"] for e in u.entity_updates
            if isinstance(e, dict) and e.get("type") == "location"
        ]
        u.key_terms = [
            e["name"] for e in u.entity_updates
            if isinstance(e, dict) and e.get("type") == "object"
        ]
        u.state_snapshot = state_mod.clone(state)
        processed.append(u)

    # limit 停在章节中间时不做伪造的“章节末压缩”；真实章节末或全书末才压缩。
    ends_at_chapter_boundary = n > 0 and (
        n == len(all_units) or all_units[n].chapter_idx != all_units[n - 1].chapter_idx
    )
    if prev_chapter is not None and ends_at_chapter_boundary:
        _compress_chapter(cache, extractor, work_id, processed[-1].chapter_name, state, stats)
        processed[-1].state_snapshot = state_mod.clone(state)
        _checkpoint(paths, state, prev_chapter)

    return processed, state, stats


def extract_work(
    work_id: str,
    data_root: Path,
    extractor: UnitExtractor | None = None,
    force: bool = False,
    limit: Optional[int] = None,
) -> dict:
    paths = WorkPaths(data_root, work_id)
    paths.ensure()
    extractor = extractor or make_extractor()

    units, state, stats = run_chain(work_id, data_root, extractor, force=force, limit=limit)
    save_units(paths.extracted_dir / "units_extracted.jsonl", units)
    save_json(paths.extracted_dir / "meta.json", {
        "work_id": work_id,
        "schema_version": SCHEMA_VERSION,
        "state_schema_version": state_mod.STATE_SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "prompt_version": PROMPT_VERSION,
        "extractor": extractor.name,
        "cache_identity": _cache_identity(extractor),
        "unit_count": len(units),
        "limit": limit,
        "llm_usage": getattr(extractor, "usage", None),
        **stats,
        "final_state_fp": state_mod.fingerprint(state),
    })
    save_json(paths.extracted_dir / "state_final.json", state)
    return {
        "work_id": work_id,
        "extractor": extractor.name,
        "units": len(units),
        "cached": stats["cached"],
        "fresh": stats["fresh"],
        "degraded": stats["degraded"],
        "compress_fresh": stats["compress_fresh"],
        "llm_usage": getattr(extractor, "usage", None),
    }
