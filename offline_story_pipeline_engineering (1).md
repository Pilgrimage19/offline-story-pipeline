# 剧情陪伴 AI：离线 Story 提取管线工程文档（V0）

> 面向负责 **Offline Story Pipeline** 的开发者。  
> 当前最重要的任务不是照着既定 schema 做抽取，而是：**在三篇短篇小说上跑通离线 Story Understanding，并探索、确定一套真正适合在线剧情陪伴 Chatbot 使用的 Story Memory 存储形态与查询接口。**

---

## 1. 项目整体认识

项目最终目标是一个 **剧情陪伴 AI**：用户第一次阅读 / 观看 / 游玩故事时，可以随时回忆、吐槽、讨论已经经历过的剧情；AI 需要知道用户正在指代的真实故事内容，而不是只靠模型参数里的模糊记忆空聊。

当前为了快速闭环，Story Understanding 暂时采用离线方案：

```text
完整作品内容
    ↓
Offline Story Pipeline
    ↓
静态 Story Memory
    ↓
Online Chatbot 在用户阅读过程中检索
```

这只是 V0 的工程妥协。长期希望演进到：

```text
字幕 / 游戏文本 / 画面 / 视频
    ↓
实时或近实时 Story Reader
    ↓
增量 Story Understanding
    ↓
持续更新 Story Memory
    ↓
Chatbot 与用户共同经历故事
```

因此，V0 的 Story Memory 应尽量保持“输入来源可替换”，不要被写死成只适合小说全文离线处理的形态。

---

## 2. V0 数据范围

第一批只处理刘慈欣三篇中短篇：

1. 《流浪地球》
2. 《赡养人类》
3. 《地火》

原因：

- 篇幅短，能够完整人工阅读和核验；
- 三篇都具备完整叙事闭环；
- 先统一作者与文本类型，减少首版 ingestion 的变量；
- 后续再用电影剧本、字幕、视觉内容验证跨媒体泛化。

V0 不需要处理《让子弹飞》《西虹市首富》等其他资产。

---

## 3. 本工程真正负责什么

Offline Pipeline 需要完成：

1. 原始小说文本导入与规范化；
2. 结合已有 MoEye / chapter 对齐思路，规划 Story 内容如何切分、对齐、组织；
3. 探索哪些结构化信息真正能帮助后续 Chatbot 理解用户所指剧情；
4. 保留原文 provenance，使检索结果能够回到真实故事证据；
5. 为内容单元建立稳定顺序语义，供阅读进度与防剧透使用；
6. 选择并落地 Story Memory 的最终本地存储与索引形态；
7. 提供一个很薄的 `StoryMemory` 本地 Adapter 给 Chatbot；
8. 在三部作品上验证 retrieval 与剧情定位效果；
9. 输出设计说明和可复现的处理脚本。

---

## 4. 本工程暂时不负责什么

不要在离线侧提前实现：

- Chatbot Query Analyzer / Router；
- Agent 的工具选择逻辑；
- Conversation Memory；
- 用户观点 / 偏好抽取；
- Notes；
- Web / Wiki / MCP；
- 最终回答生成；
- 多 Agent；
- 实时 VLM；
- 为了“像知识图谱”而强行建立复杂 KG。

特别注意：

> **不要把 Story Unit、Chapter、Chunk、Event 的关系按本文档预先锁死。**

此前讨论里出现过 `event`、`scene` 等示例，但它们不是正式设计。最终结构应由本工程结合 MoEye 中 chapter 对齐方式、实际原文特征与检索测试重新确定。

---

# 5. Story Memory 的产品语义

Story Memory 的核心目的不是“把小说做成 QA 知识库”，而是：

> **用户第一次看到某处时，Chatbot 能恢复他正在说的具体剧情上下文，并能找到此前已经发生过的相关内容。**

典型问题：

```text
“前面是不是已经提到过这个东西？”
“刚才这个人为什么突然这么做？”
“他之前不是还反对吗？”
“我记得前面有人提醒过他，对不对？”
```

因此 V0 更看重：

- 可定位；
- 可检索；
- 可回原文；
- 有稳定叙事顺序；
- 能服务模糊自然语言回忆；
- 能在需要时限定“用户已经看过”的范围。

而不是字段越多越好。

---

# 6. 首要任务：探索最终 Story Memory 存储形态

这是本工程需要主动完成的设计任务，而不是直接照临时接口编码。

请至少比较以下几类方案：

## 6.1 纯结构化文件

例如：

```text
JSON / JSONL / Markdown
```

优点：

- 最透明；
- 容易调试；
- 适合 Demo；
- 方便人工查看抽取结果。

缺点：

- retrieval / filtering / index 需要额外实现；
- 后续数据量增长后不一定方便。

## 6.2 SQLite 为主

例如：

```text
SQLite
+ FTS
+ 可选 embedding side index
```

适合：

- 明确结构化关系；
- metadata filter；
- chapter/order 查询；
- 小规模本地持久化。

需要评估向量检索如何组合。

## 6.3 LanceDB / 本地向量数据库为主

适合：

- dense retrieval；
- metadata filter；
- 后续 hybrid retrieval；
- Chatbot 本地直接打开数据库。

需要评估：

- Story 的结构化层级是否适合直接扁平化进一个 table；
- 原文 / chapter / retrieval chunk 是否应混在同一层；
- 多级内容如何回溯。

## 6.4 混合形态

例如：

```text
JSON/Markdown = source of truth
SQLite/LanceDB = 查询与索引层
```

或者：

```text
结构化 Story DB
+ 独立 retrieval index
```

这很可能更适合长期演进，但 V0 要权衡复杂度。

---

# 7. 存储设计必须回答的问题

最终请不要只说“用了某个向量数据库”，而要回答这些问题：

### 7.1 基本剧情单元是什么？

需要结合 MoEye / chapter 对齐探索：

- chapter 是否就是上层自然单元？
- 是否还需要 retrieval chunk？
- chunk 是否跨 chapter？
- 一个可检索单元与一个可展示 / 可定位单元是否相同？

### 7.2 Story Unit 如何稳定定位？

临时统一叫：

```text
story_unit_id
```

它只是“可稳定定位的故事单元 ID”，**不默认等于切分 chunk 编号**。

最终可以是：

```text
chapter_id
chunk_id
chapter_id + local_index
其他稳定标识
```

由离线侧决定。

### 7.3 顺序如何表示？

Chatbot 需要：

```text
order
```

用于：

- 当前阅读 anchor；
- 最远已读位置；
- 防剧透边界；
- 前文范围过滤。

这个 order 到底绑定 chapter、chunk 还是其他层级，由最终结构决定。

### 7.4 原文如何保留？

必须能从检索结果返回：

```text
Story representation
→ provenance
→ raw source text
```

不要只存 LLM summary 后丢掉原文。

### 7.5 检索到底检索哪一层？

至少需要实验：

- 原文 chunk；
- summary；
- chapter-level representation；
- 结构化抽取字段；
- 多路召回后融合。

不要预设 embedding(summary) 就是最佳方案。

### 7.6 多作品怎么隔离？

当前只有三篇，可以比较：

```text
每部作品独立文件 / table / collection
```

和：

```text
共享 index + work_id filter
```

V0 以简单、稳定为主。

### 7.7 未来增量写入时是否还能复用？

即使 V0 是静态离线，也需要思考：

```text
future realtime input
→ 新 story units
→ append / update index
```

是否会迫使整个存储模型推倒重来。

不要求现在实现增量写入，只需要在设计说明中讨论。

---

# 8. 推荐的离线处理工程形态

具体算法自己决定，但工程上建议分阶段并保留中间产物：

```text
Raw Source
   ↓
Normalization
   ↓
Story Segmentation / Alignment
   ↓
Story Representation Extraction
   ↓
Validation
   ↓
Storage / Index Build
```

例如：

```text
data/
  wandering_earth/
    raw/
    normalized/
    aligned/
    extracted/
    final/
```

目录只是建议，不是强制。

高成本 LLM 调用建议带 cache，便于调整 prompt / schema 后局部重跑。

---

# 9. 与 Chatbot 的临时联调契约

最终内部 schema 可以复杂，但 Chatbot 暂时只需要一个很薄的 Adapter。

## 9.1 统一 Evidence

建议至少能返回：

```json
{
  "work_id": "wandering_earth",
  "story_unit_id": "stable-id",
  "order": 123,
  "text": "用于回答的真实剧情证据或可回溯原文",
  "score": 0.82,
  "metadata": {}
}
```

注意：

- `story_unit_id` 不等于固定意义上的 chunk id；
- `metadata` 可以带 chapter、人物、位置等，但 V0 不强制具体字段；
- 最终如果返回多级结构，也可以由 Adapter 展平为 Evidence。

## 9.2 暂定 Python 接口

```python
class StoryMemory:
    def list_works(self) -> list[dict]:
        ...

    def search(
        self,
        query: str,
        *,
        work_id: str | None = None,
        max_order: int | None = None,
        top_k: int = 8,
        filters: dict | None = None,
    ) -> list[dict]:
        ...

    def get_unit(
        self,
        work_id: str,
        story_unit_id: str,
    ) -> dict | None:
        ...
```

如果最终需要定位当前阅读位置，可以由同一个 `search()` 返回候选 `story_unit_id/order`；V0 不强制额外设计 `locate_progress` API。

---

# 10. 两个工程如何通信

当前直接通过 **本地文件 / 本地数据库** 最合适：

```text
Offline Pipeline
      ↓
Local Story Files / DB / Index
      ↓
StoryMemory Adapter
      ↓
Online Chatbot
```

不需要：

- HTTP Story Service；
- RPC；
- 消息队列；
- 单独 MCP Server。

这是 Demo，Story 数据又是离线静态的，不需要为并发写入、事务一致性或热更新过度工程化。

如果后续真的变成实时 Story Reader，再考虑 service / MCP / message queue。

---

# 11. Retrieval 需要探索，而不是预设

建议至少比较：

- dense retrieval；
- BM25 / FTS；
- metadata / order filtering；
- hybrid retrieval；
- 是否需要 reranker；
- 不同 Story Representation 的召回差异。

重点验证真实用户说法，而不是只测标准问句：

```text
“前面那个东西是不是出现过？”
“他不是之前反对的吗？”
“刚才那个决定是因为啥？”
```

如果模糊指代完全需要 Chatbot 上下文才能解开，也可以接受；离线侧只要保证给定合理 query 后能返回正确 Story evidence。

---

# 12. Progress / Spoiler 对离线侧的最低要求

离线侧不维护用户状态，只需要确保内容存在可比较的顺序：

```text
story_unit_id
order
```

Chatbot 会维护：

```text
active_work
current_anchor
max_seen_order
```

其中 `max_seen_order` 可以作为 `search(max_order=...)` 的过滤条件。

因为这是 Demo，不需要对所有边界情况做严格安全保证；主要验证“不会明显拿后续剧情回答前文问题”。

---

# 13. 人工验证

每部作品至少做以下 spot check：

### 内容完整性
- 主要章节 / 段落是否遗漏；
- 顺序是否明显错误。

### Provenance
随机抽取结果：
- 能否回到正确原文；
- 结构化描述是否与原文一致。

### Retrieval
每篇至少准备：
- 事实回忆；
- 人物动机；
- 前文提及；
- 多段关联；
- 模糊自然语言描述。

### Progress filter
设定一个中间 order：
- 检查 retrieval 是否可以限制在前文。

### Work isolation
- 三部作品之间不能明显串内容。

---

# 14. 必须交付的设计文档

除代码外，请额外产出：

## `STORY_MEMORY_DESIGN.md`

至少包含：

1. 对 MoEye / 现有 chapter 对齐方式的理解；
2. 最终 Story Representation；
3. `story_unit_id` 与 chapter / chunk 的关系；
4. 选择的本地存储方式；
5. 为什么选择它，而不是其他候选；
6. source-of-truth 与 retrieval index 的关系；
7. Story retrieval 流程；
8. progress/order 如何表示；
9. 多作品如何隔离；
10. Chatbot Adapter 的最终接口；
11. 至少 5 个实际 retrieval case；
12. 当前方案迁移到未来 incremental Story Reader 的可能路径；
13. 当前仍未解决的问题。

**这份设计文档是离线侧 V0 的核心产物之一。**

---

# 15. 推荐开发顺序

## Phase 1：理解与设计

- 阅读现有 MoEye / 相关 Story 数据结构；
- 阅读三篇原始小说；
- 做少量手工样例；
- 提出 2–3 种 Story Memory 方案；
- 先选一个最小方案跑实验。

## Phase 2：《流浪地球》单篇闭环

完成：

```text
raw
→ processing
→ local story store
→ search CLI
```

先验证真正能支撑“前文回忆 / 吐槽”。

## Phase 3：三篇泛化

加入：

- 《赡养人类》；
- 《地火》。

确认没有针对单篇写死。

## Phase 4：确定 Story Memory Design

根据 retrieval 实验，冻结 V0 存储方案与 Adapter，并输出 `STORY_MEMORY_DESIGN.md`。

## Phase 5：与 Chatbot 联调

Chatbot 直接读取本地 Story Store。

---

# 16. Future：从离线到实时

参考 `open-watch-cinema` 一类项目的演进思路，可逐步从：

```text
整部作品提前理解
```

过渡到：

```text
预处理字幕 / 画面
+ 按播放进度释放
```

再到：

```text
Live Game / Movie / Reading Input
→ Incremental Story Understanding
→ Mutable Story Memory
```

V0 不实现，但存储设计应说明未来是否可扩展。

---

# 17. V0 Definition of Done

- [ ] 《流浪地球》《赡养人类》《地火》可完成离线处理；
- [ ] 已明确 Story Unit / chapter / retrieval chunk 的最终关系；
- [ ] 已选定本地 Story Memory 存储形态；
- [ ] 每个可检索单元可稳定定位并有 `order`；
- [ ] 检索结果可回到原文；
- [ ] 支持基本 work isolation；
- [ ] 支持可选 `max_order` 过滤；
- [ ] 有统一 `StoryMemory` Adapter；
- [ ] Chatbot 可以直接打开本地文件 / DB 使用；
- [ ] 有真实 retrieval 测试与人工抽检；
- [ ] 已提交 `STORY_MEMORY_DESIGN.md`；
- [ ] 不要求生产级服务化、并发与严格一致性。
