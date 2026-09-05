from __future__ import annotations

from types import SimpleNamespace

import pytest

from storypipe.llm import LLMExtractor, coerce_fields
from storypipe.model import StoryUnit


@pytest.fixture
def unit():
    return StoryUnit("demo", "d-0001", 1, 1, "第一章", 1, 1, "测试原文。")


def test_coerce_repairs_recent_event_string_and_normalizes_action(unit):
    result = coerce_fields(
        {
            "summary": "摘要",
            "entity_updates": [],
            "plotline_updates": [{"action": "invalid", "title": "事件"}],
            "context_refs": [],
            "recent_event": "剧情推进",
        },
        unit,
    )
    assert result["recent_event"] == {"text": "剧情推进"}
    assert result["plotline_updates"][0]["action"] == "advance"


def test_coerce_rejects_wrong_collection_type(unit):
    with pytest.raises(ValueError, match="entity_updates"):
        coerce_fields({"summary": "摘要", "entity_updates": "错误"}, unit)


def test_extract_retries_after_invalid_json(unit, monkeypatch):
    responses = iter([
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="not-json"))]),
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content='{"summary":"成功","entity_updates":[],"plotline_updates":[],"context_refs":[],"recent_event":{"text":"推进"}}'
        ))]),
    ])
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return next(responses)

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    extractor = LLMExtractor(api_key="test", base_url="https://example.invalid", model="test-model", review=False, multi_task=False)
    monkeypatch.setattr(extractor, "_client", lambda: fake_client)

    result = extractor.extract_unit(unit)

    assert result["summary"] == "成功"
    assert len(calls) == 2


def test_compress_retries_after_empty_response(monkeypatch):
    responses = iter([
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=""))]),
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="归档成功"))]),
    ])
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return next(responses)

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    extractor = LLMExtractor(api_key="test", base_url="https://example.invalid", model="test-model", review=False, multi_task=False)
    monkeypatch.setattr(extractor, "_client", lambda: fake_client)

    result = extractor.compress_backdrop("demo", "第一章", "", [{"order": 1, "text": "事件"}])

    assert result == "归档成功"
    assert len(calls) == 2
