"""离线管线 CLI：按阶段执行，支持局部重跑与缓存控制。

用法（在 code/ 目录下执行）：
    python pipeline.py --work wandering_earth --stage all
    python pipeline.py --work wandering_earth --stage extract --force
    python pipeline.py --work wandering_earth --stage segment --scene-threshold 80

阶段：normalize -> segment -> extract -> index -> validate
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import storypipe.extract as extract_mod
import storypipe.index as index_mod
import storypipe.normalize as normalize_mod
import storypipe.segment as segment_mod
import storypipe.validate as validate_mod
import storypipe.vector_index as vector_index_mod
from storypipe.config import (
    DATA_ROOT_DEFAULT,
    DEFAULT_SCENE_THRESHOLD,
    PIPELINE_VERSION,
    RAW_DIR_DEFAULT,
    WORK_META,
)

logger = logging.getLogger("pipeline")
CORE_STAGES = ["normalize", "segment", "extract", "index", "validate"]
STAGES = CORE_STAGES + ["vector-index"]


def _setup_console() -> None:
    """Windows 管道下保证中文输出不乱码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def _run_stage(stage: str, args: argparse.Namespace) -> dict:
    if stage == "normalize":
        return normalize_mod.normalize_work(args.work, args.data_root, raw_dir=args.raw_dir)
    if stage == "segment":
        return segment_mod.segment_work(args.work, args.data_root, threshold=args.scene_threshold)
    if stage == "extract":
        return extract_mod.extract_work(
            args.work, args.data_root, force=args.force, limit=args.limit
        )
    if stage == "index":
        return index_mod.build_index(args.work, args.data_root)
    if stage == "vector-index":
        return vector_index_mod.build_vector_index(
            args.work, args.data_root, model_name=args.embedding_model
        )
    if stage == "validate":
        return validate_mod.validate_work(args.work, args.data_root)
    raise ValueError(f"未知阶段: {stage}")


def main(argv=None) -> int:
    _setup_console()
    parser = argparse.ArgumentParser(description=f"离线 Story 管线 V0（pipeline v{PIPELINE_VERSION}）")
    parser.add_argument("--work", required=True, choices=sorted(WORK_META), help="作品 id")
    parser.add_argument("--stage", default="all", choices=STAGES + ["all"], help="要执行的阶段")
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT_DEFAULT, help="数据根目录（默认仓库 data/）")
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR_DEFAULT, help="原文目录（默认仓库 raw_text/）")
    parser.add_argument("--scene-threshold", type=int, default=DEFAULT_SCENE_THRESHOLD, help="scene 长叙述段阈值（字）")
    parser.add_argument("--extractor", choices=["auto", "mock", "llm"], default="auto", help="抽取器选择")
    parser.add_argument(
        "--embedding-model", default=None,
        help="向量模型的 Hugging Face ID 或本地目录（仅 vector-index）",
    )
    parser.add_argument("--force", action="store_true", help="extract 阶段忽略缓存重新抽取")
    parser.add_argument(
        "--limit", type=int, default=None,
        help="extract 阶段最多处理前 N 个单元（用于小批量试跑）",
    )
    args = parser.parse_args(argv)

    os.environ["STORYPIPE_EXTRACTOR"] = args.extractor
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    # 向量索引有可选重型依赖，保持原有 all 为零第三方依赖的核心流程。
    stages = CORE_STAGES if args.stage == "all" else [args.stage]
    for stage in stages:
        print(f"\n===== [{stage}] {args.work} =====")
        try:
            result = _run_stage(stage, args)
        except (FileNotFoundError, ValueError, RuntimeError) as e:
            print(f"[pipeline] {stage} 失败: {e}", file=sys.stderr)
            return 1
        if isinstance(result, dict):
            for k, v in result.items():
                print(f"  {k}: {v}")
    print("\n完成。检索试试：python search_story.py --work wandering_earth --query \"你的问题\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
