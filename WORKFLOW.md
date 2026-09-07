# StoryMemory 工作流程

本文面向维护离线 StoryMemory 管线的开发者，说明从一部小说原文到 Chatbot 可安全消费的证据与索引，实际需要执行哪些步骤、产生哪些文件，以及修改后如何验证。

## 1. 总览

```text
原始小说文本
  → normalize（统一 UTF-8、保留原始行号）
  → segment（识别章节、切成顺序 story unit）
  → extract（顺序 LLM 抽取 + 状态机合并）
  → index / vector-index（FTS5 与 LanceDB side index）
  → validate（结构、文本、章节、行号与抽检清单）
  → StoryMemory Adapter（供 Chatbot 调用）
```

离线管线只维护作品数据和检索能力；它不维护某位读者的进度、笔记或会话记忆。读者的 `max_order` 由 Chatbot 会话层保存，并作为每次检索的强制边界传入 Adapter。

## 2. 输入与目录约定

每部作品使用一个 `work_id`，所有数据放在 `data/<work_id>/`：

```text
raw_text/                         外部导入的原始作品文件
 data/<work_id>/
   00_raw/                        已复制的原始输入与元信息
   01_normalized/                 UTF-8 正文、原始行号映射
   02_segmented/                  按章节与顺序切分的 units
   03_extracted/                  含原文和理解字段的事实源
   04_validated/                  自动校验报告和人工抽检清单
   05_index/                      可重建 FTS5 / LanceDB 索引
```

新增作品前，先在 `code/storypipe/config.py` 的 `WORK_META` 中登记标题、原文匹配规则、章节标题和 `work_short`。章节边界没有确认前，不应假装已具备安全的章节阅读位置。

## 3. 阶段一：normalize

入口：`python pipeline.py --work <work_id> --stage normalize`

输入是原始文本；输出 `01_normalized/text.txt` 和 `meta.json`。此阶段负责：

- 从原有编码（当前《流浪地球》是 GB18030）转换为 UTF-8；
- 处理标题、空行等格式；
- 保存正文字符数、正文行号与原始行号的关系。

后续 unit 的 `start_line` / `end_line` 均回指原始作品行号。这是引用、人工复核和“带我回到那一段”的 provenance 基础。

## 4. 阶段二：segment

入口：`python pipeline.py --work <work_id> --stage segment --scene-threshold 80`

输出 `02_segmented/units.jsonl` 与 `meta.json`。先按登记的章节标题切章，再以长叙述段作为 scene unit 锚点，短段会并入相邻单元。每个 unit 至少包含：`unit_id`、`order`、章节、原始行号和原文。

`order` 是防剧透边界的唯一在线依据：用户只读到 order 26 时，检索、按 ID 读取及任何后续扩展都必须过滤 `order <= 26`。

切分阈值或章节识别规则改变时，后续 unit ID 可能变化。因此长期用户笔记不能只保存 `unit_id`；应同时保存作品版本和可迁移的文本/章节锚点。

## 5. 阶段三：extract：顺序理解与状态流

入口：`python pipeline.py --work <work_id> --stage extract`。

每个 unit 不会被独立总结。处理第 k 段时，抽取器输入：当前原文、最近 1–2 段原文、当前章节、截至 k-1 的精简状态；LLM 只返回增量 JSON，代码负责确定性合并状态。顺序如下：

```text
state(k-1) + recent text + unit(k)
  → LLM 增量（summary / entity_updates / plotline_updates / refs / recent_event）
  → 代码 apply
  → state(k)
  → 写入 unit(k).state_snapshot
```

每章结束后，用本章积累的背景事件压缩为 `backdrop`，并写入 `checkpoint_chXX.json`。全书结束后写 `state_final.json`。

### 抽取器、缓存与容错

- `STORYPIPE_EXTRACTOR=auto`：有可用 LLM 配置时调用 LLM，否则用 mock；
- `mock` 仅验证流程，主要生成截断 summary，不代表内容质量；
- 缓存键包含 prompt、状态 schema、模型、端点、生成参数、上文与当前文本；上游状态变化会让下游缓存自然失效；
- 单元调用失败最多重试三次，最终降级时标记 `degraded=true`，并不把不可靠增量并入状态；
- `--force` 忽略已有缓存重跑。

正式抽取后，应检查 `03_extracted/meta.json` 的抽取器、模型、错误数、降级数和最终状态指纹；`llm_errors.json` 应为空或有明确处置记录。

## 6. 阶段四：建立检索层

### FTS5 / BM25

入口：`python pipeline.py --work <work_id> --stage index`。

输出 `05_index/story.db`。它是从 `03_extracted` 构建的 SQLite FTS5 索引，用于关键词、专名和短语检索；不是事实源，随时可重建。

### LanceDB 向量索引

入口：`python pipeline.py --work <work_id> --stage vector-index --embedding-model <本地模型目录或模型名>`。

输出 `05_index/vectors.lance/` 与 `_meta.json`。索引写入每个 unit 的检索文本及 embedding；元数据保存模型、维度、单元数和源文件 SHA-256。Adapter 发现事实源哈希与索引不一致时会拒绝使用旧向量。

当前《流浪地球》的实际索引使用本地 `BAAI/bge-m3`，1024 维；默认配置只是新建索引的缺省值，不应覆盖已验证的模型选择。

## 7. 阶段五：validate

入口：`python pipeline.py --work <work_id> --stage validate`。

输出：`04_validated/report.txt` 和 `spot_check.md`。

自动检查包括 order 连续且唯一、分块文本与规范化正文一致、章节标题覆盖、原始行号合法。它保证“数据没有丢”，不等价于“LLM 理解一定正确”；语义质量仍需人工抽检、检索金标和真实陪读轨迹验证。

## 8. 在线接入契约

Chatbot 只能通过 `code/storymemory/adapter.py` 的 `StoryMemory` 使用数据：

- `list_works()`：作品列表与索引可用性；
- `search(work_id, query, max_order, top_k, filters)`：已读范围检索；
- `get_unit(work_id, unit_id)`：读取单条证据；
- `get_reading_locations(work_id)`：从现有 unit 派生无剧透章节位置；
- `search_with_diagnostics(...)`：供日志与测试读取实际策略和降级信息。

`retrieval="auto"` 依次尝试向量、FTS5、纯扫描；显式 `vector` 不会静默降级。所有路径都在检索层执行 `max_order` 和章节过滤，Chatbot 还应再做一次结果边界校验。

## 9. 修改后的最低验证

- 只改 Adapter/索引逻辑：`PYTHONPATH=code python -m pytest code/tests -q`；
- 改切分或抽取：重新跑受影响作品的 `segment → extract → index → validate`；
- 改向量检索：重建 vector index，检查 `_meta.json` 的源哈希与模型，并跑检索评测；
- 对真实作品至少验证：章节位置、`max_order` 边界、FTS 与 vector/auto、越界 `get_unit` 拒绝。

提交前运行 `git diff --check`，不要提交 `.env`、模型缓存、临时探针；原文、索引和抽取产物是否版本化由项目协作规则单独决定。