"""前向一致性检查 CLI（状态流 v0.2）。

用法（在 code/ 目录下）：
    python check_consistency.py --work wandering_earth
    python check_consistency.py --work wandering_earth --prefix 26,60,108
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from storypipe.config import DATA_ROOT_DEFAULT
from storypipe.consistency import check_prefix_consistency

_DEFAULT_PREFIXES = (10, 26, 30, 60, 108)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="状态流前向一致性检查（V0.2）")
    parser.add_argument("--work", required=True)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT)
    parser.add_argument("--prefix", default=None, help="逗号分隔的 prefix order 列表")
    args = parser.parse_args(argv)

    prefixes = tuple(int(x) for x in args.prefix.split(",")) if args.prefix else _DEFAULT_PREFIXES
    report = check_prefix_consistency(args.work, args.data_root, prefixes=prefixes)
    if report.get("error"):
        print(f"[consistency] 无法验证: {report['error']}")
        return 2
    ok = True
    for r in report["prefixes"]:
        ok = ok and r["ok"]
        status = "PASS" if r["ok"] else "FAIL"
        extra = "" if r["ok"] else f"  差异: {r['diffs']}（共{r['diff_count']}处）"
        print(f"[{status}] prefix={r['prefix']:<5} units={r['units']}{extra}")
    print(f"\n结果: {'全部一致 ✔ 离线整本处理等价于增量前缀' if ok else '存在不一致 ✘ 请检查缓存键/状态推进逻辑'}")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main())
