"""Stage 01_normalized -> 02_segmented：章节识别与 scene unit 切分。

两级结构（初版假设，未冻结，见设计文档第 5 节）：
    chapter   ：由 WORK_META.chapter_headers 显式标题切分（精确匹配 strip 后行）；
    scene unit：方案 A —— 长叙述段（> threshold 字）作为新单元锚点，
                其后短段（对话为主）并入当前单元，直到下一个长叙述段。
                阈值可调：--scene-threshold。

切分是纯规则、确定性、无 LLM 调用；规则调整只影响单元粒度，
unit_id（work_short-ORDER）与 order（全局顺序号）保持稳定。
"""
from __future__ import annotations

from pathlib import Path

from .config import DEFAULT_SCENE_THRESHOLD, PIPELINE_VERSION, SCHEMA_VERSION, WORK_META, WorkPaths
from .model import StoryUnit, load_json, save_json, save_units


def segment_work(
    work_id: str,
    data_root: Path,
    threshold: int = DEFAULT_SCENE_THRESHOLD,
) -> dict:
    meta = WORK_META[work_id]
    paths = WorkPaths(data_root, work_id)
    paths.ensure()

    text_path = paths.normalized_dir / "text.txt"
    if not text_path.exists():
        raise FileNotFoundError(f"{work_id}: 缺少 {text_path}，请先跑 normalize 阶段")
    norm_meta = load_json(paths.normalized_dir / "meta.json", {})
    lines = text_path.read_text(encoding="utf-8").split("\n")
    orig_numbers = norm_meta.get("body_line_numbers", list(range(1, len(lines) + 1)))

    headers = set(meta.get("chapter_headers", []))
    work_short = meta.get("work_short") or work_id[:2]

    units: list[StoryUnit] = []
    chapter_idx = 0
    chapter_name = ""
    order = 0
    buffer: list[tuple[int, str]] = []

    def flush() -> None:
        """把 buffer 中的段落固化为一个 scene unit。"""
        nonlocal buffer, order
        if not buffer:
            return
        order += 1
        first_no = buffer[0][0]
        last_no = buffer[-1][0]
        text = "\n".join(ln for _, ln in buffer)
        units.append(
            StoryUnit(
                work_id=work_id,
                unit_id=f"{work_short}-{order:04d}",
                order=order,
                chapter_idx=chapter_idx,
                chapter_name=chapter_name,
                start_line=first_no,
                end_line=last_no,
                text=text,
            )
        )
        buffer = []

    for orig_no, line in zip(orig_numbers, lines):
        if line in headers:  # 章节边界
            flush()
            chapter_idx += 1
            chapter_name = line
            continue
        if buffer and len(line) <= threshold:
            buffer.append((orig_no, line))
            continue
        flush()
        buffer = [(orig_no, line)]
    flush()

    # 章节统计
    chapter_stats: dict[str, dict] = {}
    for u in units:
        key = str(u.chapter_idx)
        cs = chapter_stats.setdefault(key, {"idx": u.chapter_idx, "chapter": u.chapter_name, "units": 0, "chars": 0})
        cs["units"] += 1
        cs["chars"] += u.chars

    save_units(paths.segmented_dir / "units.jsonl", units)
    save_json(
        paths.segmented_dir / "meta.json",
        {
            "work_id": work_id,
            "schema_version": SCHEMA_VERSION,
            "pipeline_version": PIPELINE_VERSION,
            "rule": "scene_anchor_long_paragraph",
            "threshold": threshold,
            "unit_count": len(units),
            "chapter_count": chapter_idx,
            "chapters": sorted(chapter_stats.values(), key=lambda c: c["idx"]),
            "warnings": [] if chapter_idx == len(headers) else ["章节标题数与命中数不一致，请核对 chapter_headers"],
        },
    )
    return {
        "work_id": work_id,
        "units": len(units),
        "chapters": chapter_idx,
        "threshold": threshold,
    }
