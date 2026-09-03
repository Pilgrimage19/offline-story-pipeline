# 离线 Story 管线（V0 初版代码）

剧情陪伴 AI 的离线部分：把小说原文变成 Chatbot 可检索、可定位、可防剧透的本地 Story Memory。

设计文档见 [`docs/offline_story_pipeline_v0_plan.md`](docs/offline_story_pipeline_v0_plan.md)（仓库根目录还有两份工程要求文档）。

## 目录结构

```text
code/
  pipeline.py            阶段 CLI：normalize -> segment -> extract -> index -> validate
  search_story.py        检索 CLI（直接调用 StoryMemory Adapter）
  requirements.txt       依赖说明（核心零第三方依赖）
  storypipe/             离线管线包
    config.py            路径/作品元信息/版本约定
    normalize.py         00_raw -> 01_normalized（GB18030 转码、清洗、保留原始行号）
    segment.py           01 -> 02_segmented（章节识别 + scene unit 切分，规则可调）
    extract.py           02 -> 03_extracted（LLM/mock 抽取 + 缓存 + 单单元容错）
    llm.py               LLM 抽取器（OpenAI 兼容接口，可选依赖）
    index.py             03/02 -> 05_index（SQLite FTS5 + BM25，可降级）
    validate.py          04_validated（自动校验 + 人工抽检清单）
    model.py             StoryUnit 模型与 JSONL/JSON IO
    textutil.py          query 轻量中文处理（V0 启发式）
  storymemory/           Chatbot 侧依赖层（契约冻结）
    adapter.py           StoryMemory: list_works / search / get_unit
    evidence.py          统一 Evidence
data/                   （运行后生成）每部作品一个目录：
  <work_id>/{00_raw,01_normalized,02_segmented,03_extracted,04_validated,05_index}
```

## 快速上手

```bash
cd code

# 1) 跑通《流浪地球》全链路（无 OPENAI_API_KEY 时自动用 mock 抽取，可离线跑完）
python pipeline.py --work wandering_earth --stage all

# 2) 检索验证
python search_story.py --list
python search_story.py --work wandering_earth --query "为什么人类要建造地球发动机"
python search_story.py --work wandering_earth --query "加代子为什么离开" --max-order 300
python search_story.py --work wandering_earth --query "前面是不是提过飞船派和地球派"

# 3) 人工抽检清单（validate 阶段生成）
#    data/wandering_earth/04_validated/spot_check.md
```

## 说明与约定

- **原文**：`raw_text/*.txt` 为 GB18030 编码；`normalize` 阶段转 UTF-8 并记录**原始行号**作 provenance 锚（unit 的 start_line/end_line 指原始文件行号）。
- **两级结构（初版假设，未冻结）**：chapter = 显式章节标题（见 `config.py` WORK_META.chapter_headers）；scene unit = 长叙述段（> `--scene-threshold` 字，默认 80）锚定 + 短段并入。规则/阈值调整不影响 unit_id 与 order 的稳定性。
- **抽取**：默认 `STORYPIPE_EXTRACTOR=auto` —— 有 `OPENAI_API_KEY` 用 LLM（默认 qwen-plus，可配 `STORYPIPE_LLM_MODEL`/`STORYPIPE_LLM_BASE_URL`），否则降级 mock（summary=首句截断）。结果按 `(prompt_version + 原文)` 哈希缓存于 `03_extracted/cache/`，改 prompt 或文本自然失效，`--force` 忽略缓存。
- **防剧透**：检索时传 `max_order`（= 用户当前进度 order），过滤在检索层完成；离线侧不维护用户状态。
- **source of truth**：`03_extracted/units_extracted.jsonl`（原文 + summary + 实体）；`05_index/story.db` 只是检索索引。索引缺失/FTS5 不可用时 Adapter 自动降级为纯扫描，接口不变。

## 开发状态（V0）

- [x] 全链路跑通《流浪地球》（mock 抽取）
- [ ] 接入 LLM 抽取并核对 summary 质量
- [ ] 《赡养人类》《地火》泛化（补 WORK_META 章节边界）
- [ ] 检索对比实验与 `STORY_MEMORY_DESIGN.md` 冻结
