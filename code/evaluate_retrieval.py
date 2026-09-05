"""用人工标注的小型 query 集比较 LanceDB 与 FTS 的召回效果。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from storymemory.adapter import StoryMemory
from storypipe.config import DATA_ROOT_DEFAULT


def evaluate(memory: StoryMemory, cases: list[dict], top_k: int) -> dict:
    rows = []
    for case in cases:
        hits = memory.search(
            case["work_id"], case["query"],
            max_order=case.get("max_order"), top_k=top_k,
        )
        orders = [h["order"] for h in hits]
        expected = set(case.get("relevant_orders", []))
        # 无指定 gold 单元的案例（如防剧透边界）单独看 spoiler_ok，不计入召回分母。
        has_gold = bool(expected)
        hit = bool(expected.intersection(orders)) if has_gold else None
        spoiler_ok = all(
            h["order"] <= case["max_order"]
            for h in hits
        ) if case.get("max_order") is not None else True
        rows.append({
            "id": case["id"], "query": case["query"], "expected_orders": sorted(expected),
            "returned_orders": orders, "hit": hit, "spoiler_ok": spoiler_ok,
        })
    return {
        "retrieval": memory.retrieval,
        "cases": len(rows),
        "scored_cases": sum(r["hit"] is not None for r in rows),
        "hit_count": sum(r["hit"] is True for r in rows),
        "recall_at_k": (
            sum(r["hit"] is True for r in rows) / sum(r["hit"] is not None for r in rows)
            if any(r["hit"] is not None for r in rows) else 0.0
        ),
        "spoiler_violations": sum(not r["spoiler_ok"] for r in rows),
        "results": rows,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="StoryMemory 检索召回评测")
    parser.add_argument("--work", default="wandering_earth")
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT)
    parser.add_argument("--cases", type=Path, default=Path(__file__).parent / "eval/retrieval_cases.json")
    parser.add_argument("--retrieval", choices=["vector", "fts", "auto"], default="auto")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args(argv)
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    report = evaluate(StoryMemory(args.data_root, retrieval=args.retrieval), cases, args.top_k)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["spoiler_violations"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
