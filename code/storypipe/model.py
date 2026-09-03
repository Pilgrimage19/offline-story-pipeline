"""故事单元数据模型与 JSONL / JSON 读写。

story_unit 的最终定义尚未冻结（见设计文档第 5、11 节），
此处只提供 V0 的可序列化载体；字段可增删，unit_id 生成规则保持稳定。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional


@dataclass
class StoryUnit:
    """一个可稳定定位的故事单元（V0 版本）。

    start_line / end_line 均指原始文件行号（1-based），作为 provenance 锚，
    可据此回到 00_raw 原始文本。
    """

    work_id: str
    unit_id: str
    order: int
    chapter_idx: int
    chapter_name: str
    start_line: int
    end_line: int
    text: str
    chars: int = 0
    summary: str = ""
    characters: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    key_terms: list[str] = field(default_factory=list)
    # ---- v0.2 状态流字段（见 docs/story_state_schema_v0.2.md）----
    entity_updates: list[dict] = field(default_factory=list)
    plotline_updates: list[dict] = field(default_factory=list)
    context_refs: list = field(default_factory=list)
    recent_event: Optional[dict] = None
    state_snapshot: Optional[dict] = None
    degraded: bool = False

    def __post_init__(self) -> None:
        if not self.chars:
            self.chars = len(self.text)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "StoryUnit":
        return cls(
            work_id=d["work_id"],
            unit_id=d["unit_id"],
            order=int(d["order"]),
            chapter_idx=int(d.get("chapter_idx", 0)),
            chapter_name=str(d.get("chapter_name", "")),
            start_line=int(d.get("start_line", 0)),
            end_line=int(d.get("end_line", 0)),
            text=d["text"],
            chars=int(d.get("chars", 0) or 0),
            summary=str(d.get("summary", "")),
            characters=list(d.get("characters", []) or []),
            locations=list(d.get("locations", []) or []),
            key_terms=list(d.get("key_terms", []) or []),
            entity_updates=list(d.get("entity_updates", []) or []),
            plotline_updates=list(d.get("plotline_updates", []) or []),
            context_refs=list(d.get("context_refs", []) or []),
            recent_event=d.get("recent_event"),
            state_snapshot=d.get("state_snapshot"),
            degraded=bool(d.get("degraded", False)),
        )


# ---------- 通用 IO ----------


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def save_units(path: Path, units: Iterable[StoryUnit]) -> None:
    write_jsonl(path, (u.to_dict() for u in units))


def load_units(path: Path) -> list[StoryUnit]:
    return [StoryUnit.from_dict(r) for r in read_jsonl(path)]


def save_json(path: Path, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def load_json(path: Path, default: Any = None) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
