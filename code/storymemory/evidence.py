"""Chatbot 侧统一 Evidence 契约（V0 冻结字段，见设计文档第 8 节）。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class StoryEvidence:
    work_id: str
    unit_id: str
    order: int
    raw_text: str
    summary: str
    score: float
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
