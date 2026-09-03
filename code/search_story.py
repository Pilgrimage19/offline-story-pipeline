"""检索 CLI：直接体验 StoryMemory Adapter。

用法（在 code/ 目录下执行）：
    python search_story.py --list
    python search_story.py --work wandering_earth --query "为什么人类要建造地球发动机"
    python search_story.py --work wandering_earth --query "加代子为什么离开" --max-order 300
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from storymemory.adapter import StoryMemory
from storypipe.config import DATA_ROOT_DEFAULT


def _setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def _preview(text: str, limit: int = 160) -> str:
    flat = text.replace("\n", " / ").strip()
    return flat if len(flat) <= limit else flat[:limit] + " ……"


def main(argv=None) -> int:
    _setup_console()
    parser = argparse.ArgumentParser(description="StoryMemory 检索 CLI（V0）")
    parser.add_argument("--work", help="作品 id（--list 可省略）")
    parser.add_argument("--query", help="自然语言问题")
    parser.add_argument("--max-order", type=int, default=None, help="只看 order <= 该值的单元（防剧透）")
    parser.add_argument("--top-k", type=int, default=8, help="返回条数")
    parser.add_argument("--chapter", default=None, help="可选：限定章节（包含匹配）")
    parser.add_argument("--list", action="store_true", help="列出已处理作品")
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT)
    args = parser.parse_args(argv)

    sm = StoryMemory(data_root=args.data_root)

    if args.list or not (args.work and args.query):
        works = sm.list_works()
        if not works:
            print("还没有已处理的作品。先运行：python pipeline.py --work wandering_earth --stage all")
            return 0
        print("已处理作品：")
        for w in works:
            print(f"  {w['work_id']}  《{w['title']}》  unit={w['unit_count']}  has_index={w['has_index']}")
        if not args.work:
            return 0

    if not (args.work and args.query):
        parser.error("--work 与 --query 必须同时给出（或使用 --list）")

    filters = {"chapter": args.chapter} if args.chapter else None
    hits = sm.search(args.work, args.query, max_order=args.max_order, top_k=args.top_k, filters=filters)

    if not hits:
        print(f"无命中：work={args.work} query={args.query!r} max_order={args.max_order}")
        print("提示：可换更口语的措辞 / 提高 max_order / 先确认已跑 pipeline 全阶段（或索引缺失时自动降级为扫描）")
        return 0

    print(f"\n检索《{args.work}》 top-{len(hits)}（query: {args.query}）\n")
    for i, h in enumerate(hits, 1):
        meta = h.get("metadata", {})
        print(f"[{i}] {h['unit_id']}  order={h['order']}  "
              f"章节「{meta.get('chapter','')}」  原文行 {meta.get('start_line')}-{meta.get('end_line')}  score={h['score']}")
        if h.get("summary"):
            print(f"    summary: {h['summary']}")
        print(f"    原文: {_preview(h.get('raw_text',''))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
