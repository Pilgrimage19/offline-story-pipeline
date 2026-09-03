"""Story State：状态流的书keeping 与确定性 apply（v0.2）。

设计文档：docs/story_state_schema_v0.2.md
原则：
- LLM 只产出"增量"，state 的合并/记账由本模块确定性完成（可重放、可测试）；
- 实体/事件线只增不改删；closed 的 plotline 保留 summary；
- recent 保留最近 RECENT_MAX 条，溢出进 backdrop_buffer；
- 章节末由 extractor 把 backdrop_buffer 压缩进 backdrop（一次 LLM 调用）。
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from .model import load_json, save_json

STATE_SCHEMA_VERSION = "story-state@0.2"
RECENT_MAX = 5
BACKDROP_MAX_CHARS = 300
_EVENT_TEXT_MAX = 120

_ENTITY_TYPES = {"character", "object", "location"}
_PLOT_ACTIONS = {"open", "advance", "close"}


def new_state(work_id: str) -> dict:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "work_id": work_id,
        "last_order": 0,
        "characters": [],  # name/aliases/status/note/first_seen_order/last_seen_order
        "objects": [],     # name/kind/status/note/first_seen_order/last_seen_order
        "locations": [],   # name/note/first_seen_order/last_seen_order
        "plotlines": [],   # title/status/opened_order/closed_order/key_orders/summary
        "recent": [],      # {order, text}
        "backdrop": "",
        "backdrop_buffer": [],  # {order, text}
    }


def clone(state: dict) -> dict:
    return copy.deepcopy(state)


def fingerprint(state: dict) -> str:
    payload = json.dumps(state, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def serialize(state: dict) -> str:
    return json.dumps(state, ensure_ascii=False)


# ---------- 确定性 apply ----------


def _upsert(records: list[dict], name: str, aliases: list[str], order: int, fields: dict) -> dict:
    for r in records:
        if r["name"] == name or name in r.get("aliases", []):
            for k, v in fields.items():
                if v:
                    r[k] = v
            r["last_seen_order"] = order
            return r
    rec = {
        "name": name,
        "first_seen_order": order,
        "last_seen_order": order,
    }
    if aliases:
        rec["aliases"] = aliases
    else:
        rec["aliases"] = []
    rec.update({k: v for k, v in fields.items() if v})
    records.append(rec)
    return rec


def upsert_entity(state: dict, e: dict, order: int) -> None:
    """LLM 的 entity_updates 单项 -> 归并进对应列表。"""
    name = str(e.get("name", "")).strip()
    if not name:
        return
    etype = str(e.get("type", "character")).strip() or "character"
    if etype not in _ENTITY_TYPES:
        etype = "character"
    status = str(e.get("status", "") or "").strip()
    note = str(e.get("note", "") or "").strip()
    if etype == "character":
        _upsert(state["characters"], name, list(e.get("aliases", []) or []), order,
                {"status": status, "note": note})
    elif etype == "location":
        _upsert(state["locations"], name, [], order, {"note": note or status})
    else:
        _upsert(state["objects"], name, [], order,
                {"kind": str(e.get("kind", "object") or "object").strip(), "status": status, "note": note})


def apply_plotline(state: dict, p: dict, order: int) -> None:
    title = str(p.get("title", "")).strip()
    if not title:
        return
    action = str(p.get("action", "advance")).strip()
    if action not in _PLOT_ACTIONS:
        action = "advance"
    note = str(p.get("note", "") or "").strip()
    for pl in state["plotlines"]:
        if pl["title"] == title:
            pl["key_orders"] = list(dict.fromkeys(pl.get("key_orders", []) + [order]))
            if action == "close":
                pl["status"] = "closed"
                pl["closed_order"] = order
            elif pl["status"] != "closed":
                pl["status"] = "open"
            if note:
                pl["summary"] = note
            return
    state["plotlines"].append({
        "title": title,
        "status": "open" if action != "close" else "closed",
        "opened_order": order,
        "closed_order": order if action == "close" else None,
        "key_orders": [order],
        "summary": note,
    })


def push_recent(state: dict, order: int, text: str) -> None:
    state["recent"].append({"order": order, "text": text[: _EVENT_TEXT_MAX]})
    while len(state["recent"]) > RECENT_MAX:
        state["backdrop_buffer"].append(state["recent"].pop(0))


def apply_unit_output(state: dict, out: dict, order: int) -> None:
    """把 unit 增量输出确定性合并进 state（原地修改）。"""
    for e in out.get("entity_updates", []) or []:
        if isinstance(e, dict):
            upsert_entity(state, e, order)
    for p in out.get("plotline_updates", []) or []:
        if isinstance(p, dict):
            apply_plotline(state, p, order)
    ev = out.get("recent_event")
    if isinstance(ev, dict) and str(ev.get("text", "") or "").strip():
        push_recent(state, order, str(ev["text"]).strip())
    state["last_order"] = order


def archive_buffer(state: dict, compressed: str) -> None:
    """章节压缩结果落地：合并进 backdrop 并清空 buffer。"""
    merged = state.get("backdrop", "") or ""
    text = (compressed or "").strip()
    if text:
        merged = f"{merged}；{text}" if merged else text
    state["backdrop"] = merged[:BACKDROP_MAX_CHARS]
    state["backdrop_buffer"] = []


# ---------- Prompt 视图与 IO ----------


def state_view(state: dict, max_characters: int = 80, max_objects: int = 80,
               max_recent: int = 5) -> str:
    """生成给 LLM 的 state 精简视图（纯文本，控制长度）。"""
    parts = [f"（故事状态，截至第 {state.get('last_order', 0)} 段）"]
    chars = state.get("characters", [])[-max_characters:]
    if chars:
        items = []
        for c in chars:
            desc = c.get("status") or c.get("note") or ""
            items.append(f"{c['name']}：{desc}" if desc else c["name"])
        parts.append("人物：" + "；".join(items))
    objs = state.get("objects", [])[-max_objects:]
    if objs:
        items = [f"{o['name']}（{o.get('status') or o.get('note') or o.get('kind','object')}）"
                 for o in objs]
        parts.append("物件/设定：" + "；".join(items))
    locs = state.get("locations", [])
    if locs:
        parts.append("地点：" + "、".join(l["name"] for l in locs[-40:]))
    opens = [p for p in state.get("plotlines", []) if p.get("status") == "open"]
    closed = [p for p in state.get("plotlines", []) if p.get("status") == "closed"][-2:]
    if opens or closed:
        lines = []
        for p in opens + closed:
            tag = "进行中" if p.get("status") == "open" else "已结束"
            lines.append(f"{p['title']}[{tag}@{p.get('opened_order')}"
                         f"{'→' + str(p.get('closed_order')) if p.get('closed_order') else ''}]"
                         f"：{p.get('summary','')}")
        parts.append("事件线：" + "；".join(lines))
    recents = state.get("recent", [])[-max_recent:]
    if recents:
        parts.append("最近发生：" + "；".join(f"@{r['order']} {r['text']}" for r in recents))
    if state.get("backdrop"):
        parts.append("更早背景：" + state["backdrop"])
    return "\n".join(parts)


def save_checkpoint(path: Any, state: dict) -> None:
    save_json(path, state)


def load_checkpoint(path: Any) -> dict | None:
    return load_json(path)
