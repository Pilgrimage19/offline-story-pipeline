"""全局配置与路径约定。

目录约定（默认，可用 --data-root / --raw-dir 覆盖）：
    原文目录   : <repo>/raw_text/
    数据根目录 : <repo>/data/
    每部作品   : data/<work_id>/{00_raw,01_normalized,02_segmented,03_extracted,04_validated,05_index}

story_unit / chapter / scene unit 的最终定义尚未冻结（见设计文档），
这里只承载 V0 可执行的组织方式，字段与规则后续可调整。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# code/storypipe/config.py -> 仓库根目录
REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR_DEFAULT = REPO_ROOT / "raw_text"
DATA_ROOT_DEFAULT = REPO_ROOT / "data"


def load_env_file(path: Path | None = None) -> int:
    """极简 .env 加载（零第三方依赖，标准 dotenv 语义）。

    - 默认读取 code/.env（相对本文件所在目录），可用环境变量 STORYPIPE_ENV_FILE 覆盖；
    - 已存在的环境变量优先，.env **不覆盖**（命令行/系统显式设置优先于 .env）；
    - 返回实际写入的条目数。
    """
    if path is None:
        env_path = os.environ.get("STORYPIPE_ENV_FILE", "")
        path = Path(env_path) if env_path else Path(__file__).resolve().parents[1] / ".env"
    if not Path(path).exists():
        return 0
    count = 0
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            count += 1
    return count


# 任何模块 import storypipe.config 都会自动加载 code/.env（LLM 抽取相关变量）
load_env_file()

PIPELINE_VERSION = "0.1.0"
SCHEMA_VERSION = "story-unit@0.1"

# 场景切分默认阈值（方案 A）：大于该字数的段落视为“长叙述段”，作为新 scene unit 的锚点
DEFAULT_SCENE_THRESHOLD = 80

# 每部作品的静态元信息。
# raw_pattern      : 在 raw_dir 下定位原文的 glob
# work_short       : unit_id 前缀（如 we-0001）
# title_is_first_line : 首行是否为标题（切分时跳过，标题只进 meta）
# chapter_headers  : 显式章节标题（正文行 strip 后精确匹配即视为分章边界）
WORK_META: dict[str, dict] = {
    "wandering_earth": {
        "title": "流浪地球",
        "author": "刘慈欣",
        "raw_pattern": "2000-2-*.txt",
        "work_short": "we",
        "title_is_first_line": True,
        "chapter_headers": [
            "上篇 刹车时代",
            "中篇 逃逸时代",
            "下篇 叛乱",
            "流浪时代",
        ],
    },
    # TODO(phase2)：《赡养人类》2005-3-*.txt、《地火》2000-1-*.txt
    # 读完原文、确认章节边界后再补充 chapter_headers。
}


@dataclass(frozen=True)
class WorkPaths:
    """某部作品在 data_root 下的目录集。"""

    data_root: Path
    work_id: str

    @property
    def root(self) -> Path:
        return self.data_root / self.work_id

    @property
    def raw_dir(self) -> Path:
        return self.root / "00_raw"

    @property
    def normalized_dir(self) -> Path:
        return self.root / "01_normalized"

    @property
    def segmented_dir(self) -> Path:
        return self.root / "02_segmented"

    @property
    def extracted_dir(self) -> Path:
        return self.root / "03_extracted"

    @property
    def validated_dir(self) -> Path:
        return self.root / "04_validated"

    @property
    def index_dir(self) -> Path:
        return self.root / "05_index"

    def ensure(self) -> None:
        for d in (
            self.raw_dir,
            self.normalized_dir,
            self.segmented_dir,
            self.extracted_dir,
            self.validated_dir,
            self.index_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)
