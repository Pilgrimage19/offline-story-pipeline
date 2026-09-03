"""Stage 00_raw -> 01_normalized：原文导入与规范化。

- 探测编码（utf-8 / gb18030 等），统一转 UTF-8；
- 统一换行、去掉每行首尾空白、丢弃空行；
- 保留“原始行号”作为 provenance 锚（后续 unit.start_line/end_line 均指原始行号）；
- 可选：首行标题与正文分离（按 WORK_META.title_is_first_line）。
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from .config import PIPELINE_VERSION, RAW_DIR_DEFAULT, WORK_META, WorkPaths
from .model import save_json

DECODE_ORDER = ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5")


def decode_bytes(raw: bytes) -> tuple[str, str]:
    """按顺序尝试解码，返回 (text, encoding)。"""
    last_err: Exception | None = None
    for enc in DECODE_ORDER:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError as e:  # noqa: PERF203
            last_err = e
    raise ValueError(f"无法用 {DECODE_ORDER} 解码原文: {last_err}")


def load_raw(work_id: str, raw_dir: Path) -> tuple[Path, str, str]:
    """定位并解码原文，返回 (源文件, 规范化后的 text, 编码)。"""
    meta = WORK_META[work_id]
    pattern = meta.get("raw_pattern", "*.txt")
    matches = sorted(Path(raw_dir).glob(pattern))
    if not matches:
        raise FileNotFoundError(f"{work_id}: 在 {raw_dir} 找不到匹配 {pattern} 的原文")
    src = matches[0]
    raw = src.read_bytes()
    text, enc = decode_bytes(raw)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return src, text, enc


def normalize_work(work_id: str, data_root: Path, raw_dir: Path = RAW_DIR_DEFAULT) -> dict:
    """执行 00->01 规范化，返回过程统计。"""
    meta = WORK_META[work_id]
    paths = WorkPaths(data_root, work_id)
    paths.ensure()

    src, text, enc = load_raw(work_id, raw_dir)

    # 每行 strip 后保留非空行，记录原始行号（1-based）
    kept: list[tuple[int, str]] = [
        (i + 1, line.strip()) for i, line in enumerate(text.split("\n")) if line.strip()
    ]
    if not kept:
        raise ValueError(f"{work_id}: 原文为空")

    # 标题分离
    title_line_no: int | None = None
    body = kept
    if meta.get("title_is_first_line", True):
        title_line_no = kept[0][0]
        body = kept[1:]

    body_line_numbers = [no for no, _ in body]
    body_text = "\n".join(line for _, line in body)

    # 00_raw：原文存档 + 哈希
    sha256 = hashlib.sha256(src.read_bytes()).hexdigest()
    shutil.copyfile(src, paths.raw_dir / src.name)
    save_json(
        paths.raw_dir / "meta.json",
        {
            "work_id": work_id,
            "source_file": src.name,
            "source_bytes": src.stat().st_size,
            "source_sha256": sha256,
            "encoding": enc,
            "pipeline_version": PIPELINE_VERSION,
        },
    )

    # 01_normalized：正文（每段一行）+ 行号映射
    (paths.normalized_dir / "text.txt").write_text(body_text, encoding="utf-8")
    save_json(
        paths.normalized_dir / "meta.json",
        {
            "work_id": work_id,
            "title": meta.get("title") or (kept[0][1] if kept else ""),
            "title_line": title_line_no,
            "encoding": enc,
            "body_line_numbers": body_line_numbers,
            "char_count_body": len(body_text),
            "line_count_body": len(body),
        },
    )
    return {
        "work_id": work_id,
        "source_file": src.name,
        "encoding": enc,
        "lines_kept": len(kept),
        "body_lines": len(body),
        "body_chars": len(body_text),
        "title_line": title_line_no,
    }
