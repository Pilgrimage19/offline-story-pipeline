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
import logging
import os
import re

from .model import StoryUnit

PROMPT_VERSION = "extract-v0.2-stateful"
logger = logging.getLogger(__name__)

_TEMPERATURE = 0.2
_EXTRACT_MAX_TOKENS = 1200
_COMPRESS_MAX_TOKENS = 300
_DEFAULT_MAX_ATTEMPTS = 3

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

_REVIEW_PROMPT = """请校验下面这段小说 chunk 的抽取结果，只依据 chunk 原文和已有故事状态修正错误。
输出一个 JSON 对象，字段必须只有 summary、entity_updates、plotline_updates、context_refs、recent_event。
保持 summary 1~2 句；没有内容的数组输出 []；不要解释，不要 Markdown，不要新增原文没有的事实。

已有抽取结果：
{draft}

当前故事状态：
{state_view}

当前 chunk 原文：
{text}
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
    if not isinstance(parsed, dict):
        raise ValueError("LLM 输出根节点必须是 JSON 对象")
    summary = str(parsed.get("summary", "") or "").strip()
    if not summary:
        raise ValueError("summary 为空")
    summary = summary[:300]
    raw_entities = parsed.get("entity_updates", []) or []
    raw_plotlines = parsed.get("plotline_updates", []) or []
    raw_refs = parsed.get("context_refs", []) or []
    if not isinstance(raw_entities, list):
        raise ValueError("entity_updates 必须是数组")
    if not isinstance(raw_plotlines, list):
        raise ValueError("plotline_updates 必须是数组")
    if not isinstance(raw_refs, list):
        raise ValueError("context_refs 必须是数组")
    entity_updates = []
    for e in raw_entities[:30]:
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
    for p in raw_plotlines[:20]:
        if not isinstance(p, dict):
            continue
        title = str(p.get("title", "")).strip()
        if not title:
            continue
        action = str(p.get("action", "advance")).strip()
        if action not in {"open", "advance", "close"}:
            action = "advance"
        plotline_updates.append({
            "action": action,
            "title": title[:60],
            "note": str(p.get("note", "") or "").strip()[:120],
        })
    context_refs = []
    for r in raw_refs[:30]:
        if isinstance(r, str) and r.strip():
            context_refs.append(r.strip()[:80])
        elif isinstance(r, dict) and str(r.get("entity", "")).strip():
            context_refs.append({"entity": str(r["entity"]).strip()[:80]})
    ev = parsed.get("recent_event")
    recent_event = None
    if isinstance(ev, dict) and str(ev.get("text", "") or "").strip():
        recent_event = {"text": str(ev["text"]).strip()[:120]}
    elif isinstance(ev, str) and ev.strip():
        # 部分兼容接口偶尔把对象简化成字符串；这是无歧义的安全修复。
        recent_event = {"text": ev.strip()[:120]}
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
        review: bool | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url or os.environ.get(
            "STORYPIPE_LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        self.model = model or os.environ.get("STORYPIPE_LLM_MODEL", "qwen-plus")
        self.timeout = timeout
        # 一次请求失败后的重试次数可调；3 表示最多 3 次总尝试（首次 + 2 次重试）。
        try:
            self.max_attempts = max(1, int(os.environ.get("STORYPIPE_LLM_MAX_ATTEMPTS", str(_DEFAULT_MAX_ATTEMPTS))))
        except ValueError:
            self.max_attempts = _DEFAULT_MAX_ATTEMPTS
        if review is None:
            review = os.environ.get("STORYPIPE_LLM_REVIEW", "on").lower() not in {"0", "off", "false", "no"}
        self.review = bool(review)
        self.usage = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        if not self.api_key:
            raise RuntimeError("缺少 OPENAI_API_KEY，无法使用 LLM 抽取（可用 STORYPIPE_EXTRACTOR=mock 降级）")

    def cache_identity(self) -> dict:
        return {
            "extractor": self.name,
            "provider_base_url": self.base_url.rstrip("/"),
            "model": self.model,
            "temperature": _TEMPERATURE,
            "extract_max_tokens": _EXTRACT_MAX_TOKENS,
            "compress_max_tokens": _COMPRESS_MAX_TOKENS,
            "max_attempts": self.max_attempts,
            "review": self.review,
        }

    def _client(self):
        import openai  # 延迟导入

        return openai.OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout)

    def extract_unit(self, unit: StoryUnit, state_view: str = "", recent_raw: str = "") -> dict:
        client = self._client()
        user_msg = _USER_TEMPLATE.format(
            title=WORK_TITLE(unit.work_id),
            chapter_name=unit.chapter_name,
            order=unit.order,
            state_view=state_view or "（无）",
            recent_raw=recent_raw or "（无）",
            text=unit.text,
        )
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=_TEMPERATURE,
                    max_tokens=_EXTRACT_MAX_TOKENS,
                    response_format={"type": "json_object"},
                )
                self._record_usage(resp)
                content = resp.choices[0].message.content or ""
                fields = coerce_fields(_parse_json(content), unit)
                if self.review:
                    try:
                        return self._review_unit(unit, fields, state_view)
                    except Exception as e:  # review 失败不丢弃已成功的核心抽取
                        logger.warning("unit %s 复核失败，保留核心抽取结果: %s", unit.unit_id, e)
                return fields
            except Exception as e:  # noqa: BLE001 - API 与格式错误统一重试
                last_error = e
                if attempt < self.max_attempts:
                    logger.warning("unit %s LLM 调用/解析失败，第 %s 次重试: %s", unit.unit_id, attempt, e)
        raise RuntimeError(f"LLM 抽取连续 {self.max_attempts} 次失败: {last_error}") from last_error

    def _review_unit(self, unit: StoryUnit, draft: dict, state_view: str) -> dict:
        """第二次调用做轻量复核；复核失败时由调用方保留 draft。"""
        client = self._client()
        user_msg = _REVIEW_PROMPT.format(
            draft=json.dumps(draft, ensure_ascii=False),
            state_view=state_view or "（无）",
            text=unit.text,
        )
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "你是严格的 JSON 抽取结果校验器。"},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0,
                    max_tokens=900,
                    response_format={"type": "json_object"},
                )
                self._record_usage(resp)
                content = resp.choices[0].message.content or ""
                return coerce_fields(_parse_json(content), unit)
            except Exception as e:  # noqa: BLE001
                last_error = e
                if attempt < self.max_attempts:
                    logger.warning("unit %s 复核失败，第 %s 次重试: %s", unit.unit_id, attempt, e)
        raise RuntimeError(f"复核连续 {self.max_attempts} 次失败: {last_error}") from last_error

    def compress_backdrop(
        self,
        work_id: str,
        chapter_name: str,
        backdrop: str,
        buffer_events: list[dict],
    ) -> str:
        """章节压缩：buffer 事件 + 旧 backdrop -> 新 backdrop（纯文本）。"""
        events = "\n".join(f"@{e.get('order')} {e.get('text','')}" for e in buffer_events)
        user_msg = _COMPRESS_PROMPT.format(
            backdrop=backdrop or "（无）",
            events=events or "（无）",
        )
        client = self._client()
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "你是小说剧情 Reader 的章节归档器，输出要简洁。"},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=_TEMPERATURE,
                    max_tokens=_COMPRESS_MAX_TOKENS,
                )
                self._record_usage(resp)
                content = (resp.choices[0].message.content or "").strip()
                if not content:
                    raise ValueError("章节压缩结果为空")
                return content
            except Exception as e:  # noqa: BLE001 - API 与空响应统一重试
                last_error = e
                if attempt < self.max_attempts:
                    logger.warning("chapter %s 压缩失败，第 %s 次重试: %s", chapter_name, attempt, e)
        raise RuntimeError(f"章节压缩连续 {self.max_attempts} 次失败: {last_error}") from last_error

    def _record_usage(self, response) -> None:
        usage = getattr(response, "usage", None)
        self.usage["requests"] += 1
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = getattr(usage, key, None) if usage is not None else None
            if value is not None:
                self.usage[key] += int(value)


def WORK_TITLE(work_id: str) -> str:
    from .config import WORK_META

    return WORK_META.get(work_id, {}).get("title", work_id)
