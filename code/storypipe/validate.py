"""Stage 04_validated：自动化校验 + 人工抽检清单生成。

自动检查：
    A. 单元存在且 order 唯一连续；
    B. 文本无丢失/重复（normalized 正文去掉章节标题后，应与所有 unit 文本拼接一致）；
    C. 所有章节标题均命中；
    D. provenance 行号合法（start_line <= end_line 且 > 0）。

人工抽检：04_validated/spot_check.md —— 抽样（均匀 10 个以内）单元，
要求核对 summary 与原文一致性、章节归属与顺序、切分粒度。
"""
from __future__ import annotations

from pathlib import Path

from .config import WORK_META, WorkPaths
from .model import StoryUnit, load_json, load_units

_SPOT_MAX = 10


def _sample_indices(n: int) -> list[int]:
    if n <= 0:
        return []
    if n <= _SPOT_MAX:
        return list(range(n))
    return sorted({round(i * (n - 1) / (_SPOT_MAX - 1)) for i in range(_SPOT_MAX)})


def validate_work(work_id: str, data_root: Path) -> dict:
    paths = WorkPaths(data_root, work_id)
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    text_path = paths.normalized_dir / "text.txt"
    norm_meta = load_json(paths.normalized_dir / "meta.json", {})
    if not text_path.exists() or not norm_meta:
        check("normalized 产物存在", False, f"缺少 {text_path} 或 meta.json，请先跑 normalize")
        return _finish(work_id, paths, checks)

    units_file = paths.segmented_dir / "units.jsonl"
    units: list[StoryUnit] = load_units(units_file) if units_file.exists() else []
    if not units:
        check("units.jsonl 非空", False, f"缺少 {units_file}，请先跑 segment")
        return _finish(work_id, paths, checks)

    # A. order 唯一且连续
    orders = [u.order for u in units]
    check("order 唯一且从 1 连续", orders == list(range(1, len(orders) + 1)),
          f"unit 数={len(units)}")

    # B. 文本无丢失/重复（去掉章节标题行后逐字比对）
    headers = set(WORK_META.get(work_id, {}).get("chapter_headers", []))
    body_lines = text_path.read_text(encoding="utf-8").split("\n")
    body_no_headers = [ln for ln in body_lines if ln not in headers]
    joined_body = "\n".join(body_no_headers)
    joined_units = "\n".join(u.text for u in units)
    check("文本无丢失/重复", joined_body == joined_units,
          f"normalized={len(joined_body)}字 vs units={len(joined_units)}字")

    # C. 章节标题全覆盖
    chapter_names = {u.chapter_name for u in units}
    missing = sorted(h for h in headers if h not in chapter_names)
    check("章节标题全覆盖", not missing, f"未命中章节: {missing or '无'}")

    # D. provenance 行号合法
    bad = [u.unit_id for u in units if not (0 < u.start_line <= u.end_line)]
    check("provenance 行号合法", not bad, f"非法单元: {bad or '无'}")

    # 汇总统计
    per_chapter: dict[str, int] = {}
    for u in units:
        per_chapter[u.chapter_name] = per_chapter.get(u.chapter_name, 0) + 1
    stats = {k: v for k, v in sorted(per_chapter.items(), key=lambda kv: kv[0])}

    _write_report(paths.validated_dir / "report.txt", work_id, checks, stats, units)
    _write_spot_check(paths.validated_dir / "spot_check.md", work_id, units, stats)
    return _finish(work_id, paths, checks, extra={"per_chapter": stats})


def _write_report(path: Path, work_id: str, checks: list[dict], stats: dict, units: list) -> None:
    lines = [f"# {work_id} 自动校验报告（{path.parent.name}）", ""]
    for c in checks:
        lines.append(f"{'PASS' if c['ok'] else 'FAIL'}  {c['name']}: {c['detail']}")
    lines += ["", "按章节单元数:", ""]
    for k, v in stats.items():
        lines.append(f"  {k or '(未分章)'}: {v}")
    lines.append(f"\n抽样人工核验见同目录 spot_check.md（共 {len(units)} 个单元）")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_spot_check(path: Path, work_id: str, units: list, stats: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {work_id} 人工抽检清单",
        "",
        f"共 {len(units)} 个 scene unit（{len(stats)} 个章节）。请对下列抽样单元逐条核对：",
        "",
        "- [ ] summary 与原文一致，未引入原文外信息（若有 summary）；",
        "- [ ] 单元归属章节 / 顺序正确；",
        "- [ ] 切分粒度合适：可定位、不割裂剧情、对话未散落；",
        "- [ ] start_line/end_line 指向正确原文段落。",
        "",
    ]
    for i, u in enumerate(units):
        if i not in set(_sample_indices(len(units))):
            continue
        preview = u.text.replace("\n", " / ")
        if len(preview) > 220:
            preview = preview[:220] + " ……"
        lines += [
            f"## {u.unit_id}  order={u.order}  章节「{u.chapter_name}」  原文行 {u.start_line}-{u.end_line}",
            "",
            f"> {preview}",
            "",
            "- [ ] summary 一致 / 章节 / 顺序 / 粒度 通过",
            "",
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _finish(work_id: str, paths: WorkPaths, checks: list[dict], extra: dict | None = None) -> dict:
    ok = all(c["ok"] for c in checks)
    return {
        "work_id": work_id,
        "ok": ok,
        "checks": checks,
        "report_path": str(paths.validated_dir / "report.txt"),
        "spot_check_path": str(paths.validated_dir / "spot_check.md"),
        **(extra or {}),
    }
