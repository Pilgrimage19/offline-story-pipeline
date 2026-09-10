from __future__ import annotations

from storymemory.adapter import StoryMemory
from storypipe.model import StoryUnit, save_units


def _unit(order, state, entities=None, plots=None):
    unit = StoryUnit("demo", f"d-{order:04d}", order, 1, "第一章", order, order, f"正文{order}")
    unit.entity_updates = entities or []
    unit.plotline_updates = plots or []
    unit.state_snapshot = state
    return unit


def test_structured_memory_keeps_progressive_recent_and_filters_boundary(tmp_path):
    units = [
        _unit(1, {"recent": [{"order": 1, "text": "起点"}], "backdrop": ""}, [{"name": "甲", "type": "character", "status": "出现"}]),
        _unit(2, {"recent": [{"order": 1, "text": "起点"}, {"order": 2, "text": "推进"}], "backdrop": "第一段背景", "characters": [{"name": "甲", "status": "推进后状态"}], "objects": [], "locations": [], "plotlines": [{"title": "线索", "status": "open"}]}, [{"name": "甲", "type": "character", "status": "推进"}], [{"action": "open", "title": "线索", "note": "开始"}]),
        _unit(3, {"recent": [{"order": 3, "text": "未来"}], "backdrop": "未来背景"}, [{"name": "甲", "type": "character", "status": "未来"}]),
    ]
    save_units(tmp_path / "demo" / "02_segmented" / "units.jsonl", units)
    memory = StoryMemory(tmp_path)

    recap = memory.get_recap("demo", max_order=2)
    entity = memory.get_entity_context("demo", "甲", max_order=2)
    plot = memory.get_plotline_context("demo", "线索", max_order=2)

    assert recap["recent"] == [{"order": 1, "text": "起点"}, {"order": 2, "text": "推进"}]
    assert recap["backdrop"] == "第一段背景"
    assert [item["order"] for item in entity["history"]] == [1, 2]
    assert entity["current"]["status"] == "推进后状态"
    assert plot["history"][0]["update"]["action"] == "open"
    assert plot["current"]["status"] == "open"