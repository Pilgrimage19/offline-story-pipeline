from copy import deepcopy

import pytest
from storymemory.adapter import StoryMemory
from storypipe.model import StoryUnit, save_units


def unit(order, references):
    item = StoryUnit("demo", f"d-{order}", order, 1, "第一章", order, order, "测试线索正文")
    item.context_refs = references
    return item


def test_name_hints_survive_get_unit_and_search_without_reference_edges(tmp_path):
    original = unit(1, ["甲", "地球发动机"])
    save_units(tmp_path / "demo/03_extracted/units_extracted.jsonl", [original, unit(2, ["未读名称"])])
    memory = StoryMemory(tmp_path, retrieval="fts")
    item = memory.get_unit("demo", "d-1")
    assert item["metadata"]["context_refs"] == ["甲", "地球发动机"]
    assert item["order"] == 1 and item["raw_text"] == original.text
    hits = memory.search("demo", "测试线索", max_order=1)
    assert hits and all(hit["order"] <= 1 for hit in hits)
    assert hits[0]["metadata"]["context_refs"] == ["甲", "地球发动机"]
    assert "未读名称" not in str(hits)
    item["metadata"]["context_refs"].append("不应污染缓存")
    assert memory.get_unit("demo", "d-1")["metadata"]["context_refs"] == ["甲", "地球发动机"]


@pytest.mark.parametrize("references,expected", [
    ([], None), (["甲", " 甲 ", " ", None, 5, {"entity": "乙"}, {"entity": 5}, {"unit_id": "d-2"}], ["甲", "乙"]),
    (["d-2"], ["d-2"]), ("不是列表", None),
])
def test_normalization_is_optional_and_does_not_invent_unit_graph(tmp_path, references, expected):
    item = unit(1, deepcopy(references))
    result = StoryMemory(tmp_path)._to_evidence(item, 1).to_dict()
    if expected is None:
        assert "context_refs" not in result["metadata"]
    else:
        assert result["metadata"]["context_refs"] == expected
    assert not any(key in result["metadata"] for key in ["related_units", "parent_id", "causal_edges"])
    assert item.context_refs == references
