from __future__ import annotations

from storymemory.adapter import StoryMemory
from storypipe.model import StoryUnit, save_units


def _unit(order: int, chapter_idx: int, chapter: str) -> StoryUnit:
    return StoryUnit("demo", f"d-{order:04d}", order, chapter_idx, chapter, order * 10, order * 10 + 2, f"第 {order} 段发动机内容")


def test_get_reading_locations_groups_existing_units_without_story_summary(tmp_path):
    save_units(tmp_path / "demo" / "02_segmented" / "units.jsonl", [_unit(1, 1, "上篇"), _unit(2, 1, "上篇"), _unit(3, 2, "中篇")])
    result = StoryMemory(tmp_path).get_reading_locations("demo")
    assert result["work_id"] == "demo"
    assert result["source_version"]
    assert result["locations"] == [
        {"location_id": "chapter-01", "label": "上篇", "kind": "chapter", "start_order": 1, "end_order": 2, "start_line": 10, "end_line": 22, "unit_count": 2},
        {"location_id": "chapter-02", "label": "中篇", "kind": "chapter", "start_order": 3, "end_order": 3, "start_line": 30, "end_line": 32, "unit_count": 1},
    ]


def test_search_with_diagnostics_reports_scan_fallback_and_boundary(tmp_path):
    save_units(tmp_path / "demo" / "02_segmented" / "units.jsonl", [_unit(1, 1, "上篇"), _unit(2, 1, "上篇")])
    result = StoryMemory(tmp_path, retrieval="auto").search_with_diagnostics("demo", "发动机", max_order=1)
    assert [item["unit_id"] for item in result["evidence"]] == ["d-0001"]
    assert result["diagnostics"]["requested_retrieval"] == "auto"
    assert result["diagnostics"]["used_retrieval"] == "scan"
    assert result["diagnostics"]["eligible_unit_count"] == 1
    assert result["diagnostics"]["result_candidate_count"] == 1
    assert result["diagnostics"]["source_version"]