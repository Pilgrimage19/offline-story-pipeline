from __future__ import annotations

import json

from storypipe.extract import MockExtractor, ResultCache, _cache_identity, run_chain
from storypipe.model import StoryUnit, save_units


class RecordingExtractor(MockExtractor):
    name = "recording"

    def __init__(self) -> None:
        self.compressed_chapters: list[str] = []

    def cache_identity(self) -> dict:
        return {"extractor": self.name, "version": 1}

    def compress_backdrop(self, work_id, chapter_name, backdrop, buffer_events):
        self.compressed_chapters.append(chapter_name)
        return f"归档:{chapter_name}"


def _unit(order: int, chapter_idx: int, chapter: str) -> StoryUnit:
    return StoryUnit(
        work_id="demo",
        unit_id=f"d-{order:04d}",
        order=order,
        chapter_idx=chapter_idx,
        chapter_name=chapter,
        start_line=order,
        end_line=order,
        text=f"第{order}段。",
    )


def _prepare(tmp_path, units):
    path = tmp_path / "demo" / "02_segmented" / "units.jsonl"
    save_units(path, units)


def test_cache_isolated_by_identity(tmp_path):
    mock = _cache_identity(MockExtractor())
    other = {**mock, "extractor": "llm", "model": "demo"}
    cache_a = ResultCache(tmp_path, mock)
    cache_b = ResultCache(tmp_path, other)
    assert cache_a.cache_dir != cache_b.cache_dir
    cache_a.put("same-key", {"summary": "mock"})
    assert cache_b.get("same-key") is None
    assert json.loads((cache_a.cache_dir / "_identity.json").read_text(encoding="utf-8")) == mock


def test_chapter_compression_uses_previous_name_and_updates_snapshot(tmp_path):
    units = [_unit(i, 1, "第一章") for i in range(1, 7)] + [_unit(7, 2, "第二章")]
    _prepare(tmp_path, units)
    extractor = RecordingExtractor()

    processed, final_state, _ = run_chain("demo", tmp_path, extractor)

    assert extractor.compressed_chapters == ["第一章", "第二章"]
    assert processed[5].state_snapshot["backdrop"] == "归档:第一章"
    assert processed[-1].state_snapshot == final_state


def test_prefix_inside_chapter_is_not_compressed(tmp_path):
    units = [_unit(i, 1, "第一章") for i in range(1, 7)] + [_unit(7, 2, "第二章")]
    _prepare(tmp_path, units)
    extractor = RecordingExtractor()

    processed, state, _ = run_chain("demo", tmp_path, extractor, limit=3)

    assert extractor.compressed_chapters == []
    assert state["backdrop"] == ""
    assert processed[-1].state_snapshot == state


def test_failed_unit_is_not_cached(tmp_path):
    class BrokenExtractor(RecordingExtractor):
        name = "broken"

        def extract_unit(self, unit, state_view="", recent_raw=""):
            raise RuntimeError("temporary failure")

    _prepare(tmp_path, [_unit(1, 1, "第一章")])
    extractor = BrokenExtractor()
    processed, _, stats = run_chain("demo", tmp_path, extractor)
    cache = ResultCache(tmp_path / "demo" / "03_extracted" / "cache", _cache_identity(extractor))

    assert processed[0].degraded is True
    assert stats["degraded"] == 1
    assert list(cache.cache_dir.glob("*.json")) == [cache.cache_dir / "_identity.json"]
