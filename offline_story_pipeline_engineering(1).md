# 剧情陪伴 AI：离线 Story 提取管线工程文档（V0）

> 面向负责 **离线 Story Pipeline** 的开发者。  
> 本文档描述当前阶段的目标、边界、暂定接口与验收标准。  
> **Story Memory 的最终数据结构尚未冻结。** 本文中的 schema 只作为 Chatbot 联调所需的临时契约，不应反过来限制最终离线管线设计。

## 1. 项目背景与当前阶段

项目目标不是做一个“小说问答库”，而是做一个 **剧情陪伴 AI**：

- 用户第一次阅读 / 观看 / 游玩作品；
- 在过程中随时回忆、吐槽、讨论已经发生的故事；
- AI 需要知道用户正在指代的实际剧情，而不是只依赖模型参数中的模糊知识；
- AI 还需要与独立的 Conversation Memory 结合，记住用户自己的看法、猜测和讨论历史；
- 未来计划探索 **实时或增量 Story Reading / Understanding**，例如字幕、游戏文本、画面/VLM 输入。

当前 V0 为降低工程复杂度，先采用：

```text
完整作品文本
    ↓
离线 Story Pipeline
    ↓
可供在线 Chatbot 查询的 Story Memory
```

因此“离线”是当前的工程实现选择，不是项目长期定位。

## 2. V0 数据范围

第一批作品固定为刘慈欣三篇中短篇：

1. 《流浪地球》
2. 《赡养人类》
3. 《地火》

选择原因：

- 体量较短，能够人工完整阅读和核验；
- 都有明确、完整的叙事闭环；
- 用户本人能够较快熟悉全文，方便构造真实对话测试；
- 统一作者和文本类型可降低首版 ingestion 的变量。

后续可能加入电影剧本、字幕、视觉信息、其他小说，但 **V0 不需要为所有媒体类型一次性泛化**。

## 3. 本工程负责什么

离线 Story Pipeline 需要负责：

1. 原始故事文本导入与规范化；
2. 将全文转换为适合后续检索和剧情定位的 Story Representation；
3. 保留原文 provenance，确保 Chatbot 能回到真实原文证据；
4. 支持“用户当前阅读进度以内”的检索约束；
5. 生成可被 Chatbot 读取的稳定产物；
6. 对三部作品完成自动处理与人工抽检；
7. 管线可重复执行、可缓存、可局部重跑；
8. 为未来实时/增量 Story Ingestion 留出接口兼容空间。

## 4. 本工程暂时不负责什么

以下内容不要在离线管线里提前实现：

- Chatbot Query Analyzer；
- 用户意图识别；
- 多轮对话状态；
- Conversation Memory；
- 用户观点/偏好抽取；
- Web/Wiki 等现实知识工具；
- 最终回答生成；
- 复杂 Agent Loop；
- 实时 VLM 输入；
- 为了“像知识图谱”而强行设计复杂 KG。

尤其注意：

> **不要把“Story Unit / Chapter / Event”的最终定义锁死在本文档的临时 schema 上。**

真实 Story DB 的结构应由离线管线开发者结合现有 MoEye / 其他已有实现重新规划。

## 5. 当前最重要的产品语义

离线 Story Memory 的核心用途是：

> 当用户第一次经历故事时，AI 能知道用户正在谈论的“过去已经发生的真实剧情”。

典型场景：

```text
用户：
“刚才这里他为什么突然这么做？”

AI 需要：
1. 知道当前作品；
2. 知道用户已经读到哪里；
3. 找到前面与当前问题有关的剧情；
4. 不依赖后续剧情解释当前行为；
5. 给出基于真实故事内容的讨论。
```

另一个例子：

```text
用户：
“前面是不是已经提到过这个东西？”

AI 需要：
从当前进度以前的 Story Memory 中找回实际提及位置。
```

所以离线管线的优先级应是：

**可定位、可检索、可追溯、可按进度过滤**

而不是追求抽取字段数量最大化。

## 6. 推荐的离线管线分层

具体算法由开发者决定，但工程上建议至少拆成：

```text
Raw Source
   ↓
Normalization
   ↓
Story Segmentation / Alignment
   ↓
Structured Extraction / Enrichment
   ↓
Validation
   ↓
Story Store / Index Build
   ↓
Exported Story Package
```

每一步建议留下中间产物，避免修改抽取逻辑后从头重新跑。

例如：

```text
data/
  wandering_earth/
    00_raw/
    01_normalized/
    02_segmented/
    03_extracted/
    04_validated/
    05_index/
```

目录仅作参考。

## 7. Source 与 provenance 要求

无论最终怎么设计结构，都必须能够从检索结果返回到原始文本。

至少保留：

- `work_id`
- 某种稳定的内容单元 ID
- 单元在全文中的顺序
- 原文文本或原文可定位引用
- 原始文本版本
- 如果进行了摘要 / 抽取，记录其 source span / parent unit

禁止只存摘要后丢弃原文。

推荐把“模型生成的结构化信息”和“原始证据”逻辑上分开。

## 8. 暂定 Story Memory 联调契约

> 这是 **V0 联调用的最小契约**，最终可以替换。  
> Chatbot 不应直接依赖底层数据库表结构，而应通过 Adapter 调用。

### 8.1 Work Manifest

```json
{
  "work_id": "wandering_earth",
  "title": "流浪地球",
  "source_type": "novel",
  "version": "v0.1",
  "unit_count": 0,
  "metadata": {}
}
```

### 8.2 Story Evidence

Chatbot 只需要拿到统一 Evidence，不要求离线侧真实存储结构长这样。

```json
{
  "work_id": "wandering_earth",
  "unit_id": "stable-id",
  "order": 123,
  "raw_text": "原始证据文本",
  "summary": "可选的简要描述",
  "score": 0.0,
  "metadata": {
    "chapter": null,
    "characters": [],
    "locations": [],
    "source_ref": null
  }
}
```

其中：

- `unit_id`：稳定可复现；
- `order`：用于阅读进度和 spoiler gating；
- `raw_text`：必须能作为最终证据；
- `summary`：可选；
- `metadata`：保持可扩展，不要求 V0 填满。

### 8.3 暂定读取接口

建议离线 Story Store 最终至少能通过 Python Adapter 提供：

```python
class StoryMemory:
    def list_works(self) -> list[dict]:
        ...

    def search(
        self,
        work_id: str,
        query: str,
        *,
        max_order: int | None = None,
        top_k: int = 8,
        filters: dict | None = None,
    ) -> list[dict]:
        ...

    def get_unit(
        self,
        work_id: str,
        unit_id: str,
    ) -> dict | None:
        ...
```

可选：

```python
def get_range(
    work_id: str,
    start_order: int,
    end_order: int,
) -> list[dict]:
    ...
```

### 8.4 为什么只规定 Adapter

当前 Story DB 可能最终使用：

- LanceDB；
- SQLite + vector extension；
- 自定义 JSON/JSONL + index；
- 其他本地存储。

Chatbot 不需要知道。

边界应保持为：

```text
Chatbot
   ↓
StoryMemory Adapter
   ↓
实际 Story DB / Index
```

这样之后从 Offline V0 迁移到 Incremental/Realtime Story Memory 时，不需要重写 Chatbot。

## 9. 检索实现：当前建议，而非硬要求

V0 可先考虑：

- Dense retrieval；
- BM25 / FTS；
- metadata filter；
- hybrid fusion；
- `work_id` 强隔离；
- `order <= user_progress` 强约束。

重点是验证：

1. 人名/术语能否准确召回；
2. 模糊语义问题能否召回；
3. 跨作品不串；
4. 不召回用户尚未看到的后续剧情。

是否使用 LanceDB、RRF、reranker 等由开发者根据实现成本决定。

## 10. Progress / Spoiler 的数据要求

离线侧不负责维护“用户读到哪里”，但必须提供一种可过滤的顺序语义。

最低要求：

```text
每个可检索内容单元拥有稳定的 order
```

在线 Chatbot 将保存：

```text
active_work
current_progress
```

检索时调用：

```python
story_memory.search(
    work_id=active_work,
    query=query,
    max_order=current_progress
)
```

这样防剧透在 retrieval 阶段完成，而不是只依赖 prompt。

## 11. V0 的人工验证

每部作品处理完成后至少做一次人工 spot check。

### A. 顺序
- Story Unit 是否保持正确叙事顺序；
- 是否发生明显前后倒置或缺失。

### B. 原文追溯
随机抽 10 个结果：
- 能否回到正确 raw text；
- 模型生成 summary 是否与原文一致。

### C. 检索
每篇准备至少：
- 3 个明确事实问题；
- 3 个人物/动机问题；
- 2 个“前面有没有提过”的问题；
- 2 个多段关联问题。

### D. spoiler
人为设定中间进度：
- 后续剧情必须不可检索。

### E. work isolation
同一 query 分别在三部作品查询：
- 不允许跨作品混入证据。

## 12. 推荐工程能力

### 12.1 可重复执行

每个阶段应支持类似：

```bash
python pipeline.py --work wandering_earth --stage segment
```

修改后处理逻辑时避免每次重新跑全部 LLM 调用。

### 12.2 Cache

所有高成本 LLM 调用建议：

- 输入 hash；
- prompt/version hash；
- 输出 cache；
- 可 force rerun。

### 12.3 Versioning

Story Package 建议保存：

```text
source_version
pipeline_version
schema_version
prompt_version
embedding_model
```

### 12.4 Error Recovery

长文本批处理必须考虑：

- 单请求失败；
- JSON parse failure；
- timeout；
- malformed model output；
- resume；
- failed units retry。

不要因为一处抽取失败导致全书重跑。

## 13. 第一阶段交付物

离线侧 V0 完成时，希望至少有：

```text
1. 三部小说的原始文本导入
2. 可重复运行的离线处理脚本
3. 中间产物
4. 最终 Story Package / DB
5. StoryMemory Adapter
6. 一个最小检索 CLI / test script
7. 人工 spot-check 结果
8. README
```

例如：

```bash
python search_story.py \
  --work wandering_earth \
  --query "为什么人类要建造地球发动机" \
  --max-order 120
```

可以直接看到 Evidence。

## 14. 推荐开发顺序

### Phase 1：单作品跑通
优先《流浪地球》。

目标：
- raw → final package；
- 能 search；
- 能按 progress filter；
- provenance 正确。

### Phase 2：三作品泛化
加入：
- 《赡养人类》
- 《地火》

验证是否有针对单篇写死的规则。

### Phase 3：联调
与 Chatbot 侧对齐 `StoryMemory Adapter`。

### Phase 4：优化
再考虑：
- hybrid retrieval；
- reranker；
- 更复杂的结构化提取；
- query-aware index；
- story graph；
- incremental ingestion。

## 15. 当前不急着决定的问题

以下问题应保留为设计项，而不是本周必须完成：

- 最终“chapter / story unit / event”的定义；
- 是否引入显式人物关系图；
- 是否建立 timeline graph；
- 是否保存不同角色 knowledge state；
- 是否需要多级 summary；
- 是否需要 agentic retrieval；
- 是否统一所有作品到一个物理 index；
- 是否直接复用 MoEye 数据模型；
- 未来视频/game input 如何映射到同一 Story Representation。

## 16. Future：实时/增量 Story Reader

V0 的接口应允许未来替换：

```text
Full Raw Text
   ↓
Offline Extraction
```

为：

```text
Subtitle / Game Text / Screenshot / Video Frame
   ↓
Incremental Text/VLM Understanding
   ↓
Story Memory Update
```

关键原则：

> Chatbot 面向的是 `StoryMemory` 能力，不应面向“离线小说数据库”的具体实现。

因此这次离线开发既是当前可交付方案，也是未来实时 Story Reader 的基线实现。

## 17. V0 Definition of Done

- [ ] 《流浪地球》《赡养人类》《地火》均可完整处理；
- [ ] 每个结果可以追溯原文；
- [ ] Story Unit 顺序稳定；
- [ ] 支持 work isolation；
- [ ] 支持 `max_order` 过滤；
- [ ] Chatbot 可以通过统一 Adapter 搜索；
- [ ] 发生 LLM/网络错误后可以恢复；
- [ ] 有基础检索测试；
- [ ] 有人工核验记录；
- [ ] 不要求最终 schema 冻结。
