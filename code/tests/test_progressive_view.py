from copy import deepcopy

import pytest

from storymemory.adapter import StoryMemory
from storypipe.model import StoryUnit, save_units


def make_unit(order):
    unit = StoryUnit("demo", f"d-{order}", order, 1, "第一章", order, order, "测试正文")
    unit.state_snapshot = {"work_id": "demo", "last_order": order, "recent": [{"order": order, "text": "当前事件"}]}
    return unit


def test_boundary_and_source_reload(tmp_path):
    path = tmp_path / "demo/02_segmented/units.jsonl"
    save_units(path, [make_unit(3), make_unit(1), make_unit(2)])
    memory = StoryMemory(tmp_path)
    view = memory.get_progressive_view("demo", max_order=2)
    assert view["snapshot_order"] == 2 and view["status"] == "ok"
    assert view["provenance"]["order_range"] == [1, 2]
    view["snapshot"]["recent"].clear()
    assert memory.get_progressive_view("demo", max_order=2)["snapshot"]["recent"]
    newer = make_unit(2)
    newer.state_snapshot["backdrop"] = "更新后的背景"
    save_units(tmp_path / "demo/03_extracted/units_extracted.jsonl", [newer])
    reloaded = memory.get_progressive_view("demo", max_order=2)
    assert reloaded["source_version"] != view["source_version"]
    assert reloaded["snapshot"]["backdrop"] == "更新后的背景"
    newer.state_snapshot["backdrop"] += "第二次更新"
    save_units(tmp_path / "demo/03_extracted/units_extracted.jsonl", [newer])
    assert memory.get_progressive_view("demo", max_order=2)["source_version"] != reloaded["source_version"]
    assert memory.get_progressive_view("demo", max_order=0)["snapshot"] is None


@pytest.mark.parametrize("mutation", [
    {"last_order": 3}, {"work_id": "other"}, {"characters": [{"last_seen_order": 3}]},
    {"plotlines": [{"key_orders": [1, 3]}]}, {"recent": [{"order": True}]},
    {"recent": "错误类型"}, {"backdrop": []},
])
def test_invalid_snapshots_fail_closed(tmp_path, mutation):
    unit = make_unit(2)
    unit.state_snapshot.update(deepcopy(mutation))
    save_units(tmp_path / "demo/03_extracted/units_extracted.jsonl", [make_unit(1), unit, make_unit(3)])
    result = StoryMemory(tmp_path).get_progressive_view("demo", max_order=2)
    assert result["status"] == "invalid" and result["snapshot"] is None


def test_degraded_and_missing(tmp_path):
    unit = make_unit(1)
    unit.degraded = True
    path = tmp_path / "demo/02_segmented/units.jsonl"
    save_units(path, [unit])
    assert StoryMemory(tmp_path).get_progressive_view("demo", max_order=1)["reason"] == "degraded_snapshot"
    unit.degraded = False
    unit.state_snapshot = None
    save_units(path, [unit])
    assert StoryMemory(tmp_path).get_progressive_view("demo", max_order=1)["snapshot"] is None
