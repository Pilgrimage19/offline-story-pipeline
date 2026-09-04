from __future__ import annotations

import pytest

from storymemory.adapter import StoryMemory
from storypipe.model import StoryUnit, save_units
from storypipe.vector_index import build_vector_index, read_vector_meta, retrieval_text


class FakeEmbedder:
    model_name = "fake-2d"

    def encode(self, texts, *, is_query=False):
        vectors = []
        for text in texts:
            vectors.append([
                1.0 if "发动机" in text else 0.0,
                1.0 if "加代子" in text else 0.0,
            ])
        return vectors


def _unit(order, text, chapter="第一章"):
    return StoryUnit("demo", f"d-{order:04d}", order, 1, chapter, order, order, text)


def test_retrieval_text_contains_structured_fields():
    unit = _unit(1, "原文")
    unit.summary = "摘要"
    unit.characters = ["人物甲"]
    unit.locations = ["北京"]
    unit.key_terms = ["物件"]
    text = retrieval_text(unit)
    assert all(value in text for value in ["摘要", "人物甲", "北京", "物件", "原文"])


def test_build_and_search_vector_index_with_progress_filter(tmp_path):
    units = [_unit(1, "地球发动机启动"), _unit(2, "加代子离开"), _unit(3, "发动机最终计划")]
    src = tmp_path / "demo" / "02_segmented" / "units.jsonl"
    save_units(src, units)
    result = build_vector_index("demo", tmp_path, embedder=FakeEmbedder())
    meta = read_vector_meta(tmp_path / "demo" / "05_index" / "vectors.lance")
    assert result["units"] == 3
    assert meta["model"] == "fake-2d"

    memory = StoryMemory(tmp_path, retrieval="vector")
    memory._embedders["fake-2d"] = FakeEmbedder()
    hits = memory.search("demo", "发动机", max_order=2, top_k=5)

    assert hits[0]["unit_id"] == "d-0001"
    assert hits[0]["score"] > hits[-1]["score"]
    assert all(hit["order"] <= 2 for hit in hits)


def test_stale_vector_index_is_not_used(tmp_path):
    src = tmp_path / "demo" / "02_segmented" / "units.jsonl"
    save_units(src, [_unit(1, "地球发动机启动")])
    build_vector_index("demo", tmp_path, embedder=FakeEmbedder())
    save_units(src, [_unit(1, "内容已经变化")])

    memory = StoryMemory(tmp_path, retrieval="vector")
    memory._embedders["fake-2d"] = FakeEmbedder()
    # 陈旧向量被拒绝；显式 vector 模式明确报错，不伪装成其他检索后端。
    with pytest.raises(RuntimeError, match="向量索引不可用"):
        memory.search("demo", "发动机")
