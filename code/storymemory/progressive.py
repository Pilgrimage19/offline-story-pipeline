"""渐进快照的结构和显式顺序校验；不声称替代语义防剧透审计。"""
from __future__ import annotations

from typing import Any

ORDER_FIELDS = {"order", "last_order", "first_seen_order", "last_seen_order",
                "opened_order", "closed_order"}
GROUPS = ("characters", "objects", "locations", "plotlines", "recent", "backdrop_buffer")


def validate_snapshot(snapshot: Any, work_id: str, order: int) -> str | None:
    if not isinstance(snapshot, dict):
        return "missing_snapshot"
    if snapshot.get("work_id") != work_id or snapshot.get("last_order") != order:
        return "snapshot_identity"
    if not isinstance(snapshot.get("backdrop", ""), str):
        return "invalid_backdrop"
    for group in GROUPS:
        items = snapshot.get(group, [])
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            return "invalid_group"
    def check(value: Any) -> bool:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ORDER_FIELDS and item is not None:
                    if type(item) is not int or not 0 <= item <= order:
                        return False
                elif key == "key_orders":
                    if not isinstance(item, list) or any(type(n) is not int or not 0 <= n <= order for n in item):
                        return False
                elif not check(item):
                    return False
        elif isinstance(value, list):
            return all(check(item) for item in value)
        return True
    return None if check(snapshot) else "future_or_invalid_order"
