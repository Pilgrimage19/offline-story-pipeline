"""LLM 抽取器（OpenAI 兼容接口，可选依赖 openai）—— 状态流版（v0.2）。

设计见 docs/story_state_schema_v0.2.md：
- LLM 只产出"增量"（summary / entity_updates / plotline_updates /
  context_refs / recent_event），state 合并由 state.py 确定性完成；
- 输入包含：本段原文 + 章节 + state 精简视图 + 最近 1~2 段原文（消指代）。

环境变量：
    OPENAI_API_KEY            必填（llm 模式）
    STORYPIPE_LLM_BASE_URL    默认 dashscope 兼容端点
    STORYPIPE_LLM_MODEL       默认 qwen-plus

openai 包为延迟导入：未安装时仅在使用 LLM 抽取时报错。
"""
from __future__ import annotations

import json
import os
import re

from .model import StoryUnit

PROMPT_VERSION = "extract-v0.2-stateful"

SYSTEM_PROMPT = (
    "你是小说剧情陪伴 AI 的 Story Reader。你按顺序阅读作品的剧情单元，"
    "并维护一份“截至当前的故事状态”。规则：\n"
    "1. 只能依据【本段原文】与【当前故事状态】作答，不得引入其中没有的信息（防止剧透后文）；\n"
    "2. 实体一律沿用状态中已有的 canonical 名，不要另起新名；确属新实体才新建；\n"
    "3. 理解要结合前文状态：能消解的指代（如“他/那个东西”）必须消解到具体实体，"
    "再写入实体更新；\n"
    "4. 只输出一个 JSON 对象，字段见用户消息，不要输出任何其他文字。"
)

_USER_TEMPLATE = """作品：《{title}》   章节：{chapter_name}   本段：第 {order} 段

【当前故事状态（本段之前）】
{state_view}

【紧接着的上文原文（仅用于消解指代，不是本段）】
{recent_raw}

【本段原文】
{text}

请输出 JSON（不要多余文字）：
{{
  "summary": "1~2句，结合前文状态概括本段发生了什么",
  "entity_updates": [
    {{"name": "实体名（沿用已有 canonical 名；新实体用原文称呼）",
      "type": "character|object|location",
      "status": "当前状态/新处境一句话",
      "note": "可选"}}
  ],
  "plotline_updates": [
    {{"action": "open|advance|close", "title": "事件线标题（沿用或新建）", "note": "一句话"}}
  ],
  "context_refs": ["本段理解依赖的前文线索（实体名/物件名，可省略）"],
  "recent_event": {{"text": "一句话：这段把剧情推进到了什么"}}
}}
"""

_COMPRESS_PROMPT = """你是小说剧情 Reader 的章节归档器。请把本章尚未归档的事件压缩进故事总述 backdrop：
- 输出 ≤150 字，保留关键实体名与因果链；
- 与旧 backdrop 重复的信息不要重复写；
- 只输出压缩后的 backdrop 文本本身，不要任何解释。

旧 backdrop：
{backdrop}

本章待归档事件（order 升序）：
{events}
"""

_FALLBACK_TYPES = {"character", "object", "location"}


def _first_sentence(text: str, maxlen: int = 60) -> str:
    summary = ""
    for sep in ("。", "！", "？", "；", "\n"):
        head = text.strip().split(sep, 1)[0]
        if head:
            summary = head
            break
    if not summary:
        summary = text[:maxlen]
    if len(summary) > maxlen:
        summary = summary[:maxlen] + "……"
    return summary


def _fallback_fields(unit: StoryUnit) -> dict:
    """解析失败 / 降级时的兜底字段（summary 取首句截断）。"""
    s = _first_sentence(unit.text)
    return {
        "summary": s,
        "entity_updates": [],
        "plotline_updates": [],
        "context_refs": [],
        "recent_event": {"text": s} if s else None,
    }


def _parse_json(text: str) -> dict:
    """容忍 ```json 围栏与多余文字，提取第一个 JSON 对象。"""
    cleaned = re.sub(r"```(?:json)?", "", text)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("输出中没有 JSON 对象")
    return json.loads(cleaned[start : end + 1])


def coerce_fields(parsed: dict, unit: StoryUnit) -> dict:
    """把 LLM 输出规整成受控字段；summary 缺失时抛 ValueError（由调用方降级）。"""
    summary = str(parsed.get("summary", "") or "").strip()
    if not summary:
        raise ValueError("summary 为空")
    entity_updates = []
    for e in parsed.get("entity_updates", []) or []:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name", "")).strip()
        if not name:
            continue
        etype = str(e.get("type", "character")).strip()
        if etype not in _FALLBACK_TYPES:
            etype = "character"
        entity_updates.append({
            "name": name[:40],
            "type": etype,
            "status": str(e.get("status", "") or "").strip()[:120],
            "note": str(e.get("note", "") or "").strip()[:120],
        })
    plotline_updates = []
    for p in parsed.get("plotline_updates", []) or []:
        if not isinstance(p, dict):
            continue
        title = str(p.get("title", "")).strip()
        if not title:
            continue
        plotline_updates.append({
            "action": str(p.get("action", "advance")).strip()[:10],
            "title": title[:60],
            "note": str(p.get("note", "") or "").strip()[:120],
        })
    context_refs = []
    for r in parsed.get("context_refs", []) or []:
        if isinstance(r, str) and r.strip():
            context_refs.append(r.strip()[:80])
        elif isinstance(r, dict) and str(r.get("entity", "")).strip():
            context_refs.append({"entity": str(r["entity"]).strip()[:80]})
    ev = parsed.get("recent_event")
    recent_event = None
    if isinstance(ev, dict) and str(ev.get("text", "") or "").strip():
        recent_event = {"text": str(ev["text"]).strip()[:120]}
    return {
        "summary": summary,
        "entity_updates": entity_updates,
        "plotline_updates": plotline_updates,
        "context_refs": context_refs,
        "recent_event": recent_event,
    }


class LLMExtractor:
    name = "llm"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 90.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url or os.environ.get(
            "STORYPIPE_LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        self.model = model or os.environ.get("STORYPIPE_LLM_MODEL", "qwen-plus")
        self.timeout = timeout
        if not self.api_key:
            raise RuntimeError("缺少 OPENAI_API_KEY，无法使用 LLM 抽取（可用 STORYPIPE_EXTRACTOR=mock 降级）")

    def extract_unit(self, unit: StoryUnit, state_view: str = "", recent_raw: str = "") -> dict:
        import openai  # 延迟导入

        client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout)
        user_msg = _USER_TEMPLATE.format(
            title=WORK_TITLE(unit.work_id),
            chapter_name=unit.chapter_name,
            order=unit.order,
            state_view=state_view or "（无）",
            recent_raw=recent_raw or "（无）",
            text=unit.text,
        )
        resp = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.2,
            max_tokens=600,
        )
        content = resp.choices[0].message.content or ""
        return coerce_fields(_parse_json(content), unit)

    def compress_backdrop(
        self,
        work_id: str,
        chapter_name: str,
        backdrop: str,
        buffer_events: list[dict],
    ) -> str:
        """章节压缩：buffer 事件 + 旧 backdrop -> 新 backdrop（纯文本）。"""
        import openai  # 延迟导入

        events = "\n".join(f"@{e.get('order')} {e.get('text','')}" for e in buffer_events)
        user_msg = _COMPRESS_PROMPT.format(
            backdrop=backdrop or "（无）",
            events=events or "（无）",
        )
        client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout)
        resp = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "你是小说剧情 Reader 的章节归档器，输出要简洁。"},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.2,
            max_tokens=300,
        )
        return (resp.choices[0].message.content or "").strip()


def WORK_TITLE(work_id: str) -> str:
    from .config import WORK_META

    return WORK_META.get(work_id, {}).get("title", work_id)
