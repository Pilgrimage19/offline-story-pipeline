# ChronoMind：时间与因果混合记忆检索工程文档（调研版）

> 研究对象：`sunandatata/ChronoMind`。
> 核查版本：`ea4ce01`（2026-09-10）。
> 目的：评估其事件模型、混合召回、排序、时间线重建和 explain API 对 StoryPal StoryMemory 的价值。

---

## 1. 项目定位

ChronoMind 是个人历史的搜索与推理引擎，不是普通聊天机器人。它把记忆拆为原子事件，并通过 Qdrant、Neo4j 和 BM25 从语义、关系、时间和精确词多个方向检索，最后按时间重建上下文并调用 LLM 回答。

```text
Raw Memory Text
  → Atomic Event Extraction
  → Vector + Graph + BM25 Index
  → Query Understanding
  → Multi-channel Retrieval
  → Fusion + Feature Reranking
  → Timeline Context
  → Grounded Answer + Debug Trace
```

它与 StoryPal 的共同点是都需要从长程事件中回答模糊、时间性、因果性问题；区别是 ChronoMind 面向可变个人记忆，没有作品顺序和 spoiler boundary。

## 2. 总体架构

| 层 | 实现 | 职责 |
| --- | --- | --- |
| Frontend | Next.js | 查询、时间线、图、检索回放、评估面板 |
| Backend | FastAPI | ingestion、query、ranking、reasoning |
| Semantic | Qdrant | embedding 与向量召回 |
| Graph | Neo4j | 事件、概念、实体和多跳关系 |
| Lexical | 内存 BM25 | 专名和精确词召回 |
| Model | OpenAI-compatible | 抽取、query understanding、最终回答 |

Docker Compose 启动 Web、API、Qdrant 和 Neo4j。相比 StoryPal 当前本地静态库，基础设施明显更重。

## 3. Memory Event 数据模型

```python
class MemoryEvent(BaseModel):
    id: str                         # 默认 UUID
    text: str
    timestamp: datetime
    source: SourceType
    event_type: EventType
    entities: list[str]
    topics: list[str]
    sentiment: float | None
    confidence: float
    importance_score: float
    memory_strength: float
    retrieval_count: int
    last_accessed_at: datetime | None
    decay_coefficient: float
    embedding_id: str | None
    version: int
    original_event_id: str | None
    source_id: str | None
```

`EventType`：observation、decision、belief、action、opinion、learning。
`SourceType`：note、email、chat、document、bookmark、manual。

该模型同时包含事实、检索统计和遗忘强度。StoryPal 不应把这些全部写进原始 Story Unit；作品事实应保持不可变，retrieval_count 等运行信号放到独立 telemetry/index 表。

## 4. 图数据模型

节点：

- Event：原子记忆；
- Concept：主题锚点；
- Entity：人物、地点、系统、技术等实体。

关系：

```text
ABOUT
MENTIONS
RELATED_TO
INFLUENCED_BY
CAUSED_BY
CONTRADICTS
REFINES
REINFORCES
PREVIOUS_VERSION
```

图检索支持 2–3 hop 展开、confidence threshold、因果边优先和 session 中已探索事件排除；分析包括连通分量、社区、最短路、中心性。

## 5. Ingestion 接口

### `POST /api/ingest`

请求：

```json
{
  "text": "I changed my mind after the experiment...",
  "timestamp": "2026-09-10T10:00:00",
  "source": "chat",
  "source_id": "conversation-123"
}
```

响应：

```json
{
  "events_extracted": 2,
  "event_ids": ["uuid-1", "uuid-2"],
  "message": "..."
}
```

服务会抽取原子事件、生成 embedding、写 Qdrant、写 Neo4j，并更新 lexical corpus。接口没有公开幂等键；重复提交需要由 source_id/上层逻辑处理。

## 6. Query 接口

### `POST /api/query`

```json
{
  "query": "Why did I change my decision?",
  "top_k": 8,
  "time_start": "2025-01-01",
  "time_end": "2026-12-31",
  "session_id": "optional",
  "reset_session": false
}
```

`top_k` 允许 1..25。响应：

```json
{
  "answer": "...",
  "source_events": [],
  "query_type": "decision_trace",
  "events_searched": 120,
  "confidence": 0.82,
  "session_id": "...",
  "debug_trace": {}
}
```

`debug_trace` 包含多通道候选、融合、排序解释、最终 context、selected IDs、query profile、各阶段 latency、failure analysis 和八步 playback。

### `POST /api/query/explain`

使用相同 QueryRequest，但返回每个结果的 rank、文本、event type、final score 以及各排序特征，适合开发者检查而非普通用户展示。

## 7. 辅助读取接口

### `GET /api/timeline/{concept}`

可选 `start`、`end`，返回按概念相关的事件时间线：

```json
{"concept": "vector search", "events": [], "total": 0}
```

### `GET /api/graph/explore`

返回最多一部分 Event/Concept 节点和关系边，用于前端图展示。

项目还包含 stats、session、belief evolution、graph metrics 和 evaluation routes，说明它把检索调试和评估作为一等能力。

## 8. Query Understanding

系统先把问题识别为策略类型，例如：

- Decision Trace；
- Temporal/Belief Evolution；
- Comparison；
- Fact Lookup；
- Learning/Project History；
- Relationship Exploration；
- Causal Analysis。

QueryProfile 决定时间窗口、召回偏好、过滤与后续 context instruction。它比 StoryPal 当前“让主 Agent 自己改写 query”更 workflow 化。

StoryPal V0 不宜照搬独立 Query Analyzer，但可以记录真实 trajectory；当某类“因果回顾/时间线”模式稳定后，再将其变成可选 retrieval mode。

## 9. 多通道召回

`hybrid_retrieve()` 并行运行：

```text
Qdrant vector search
Neo4j graph traversal
BM25 lexical search
temporal filtering
session-aware exclusions
```

每个候选统一为：ID、payload、embedding、timestamp、source、channel rank/score、match type、hop 和 temporal metadata。不同通道再通过 RRF 融合并按 ID 去重。

这与 StoryPal 的最佳结合点是：FTS5 与 LanceDB 不要各自直接返回最终结果，应先转统一候选，再融合、过滤和记录通道来源。

## 10. 排序特征

排序器使用可解释特征：

```text
vector_similarity_score
lexical_score
graph_distance_score
graph_centrality_score
temporal_distance_score
recency_score
event_type_weight
causal_edge_strength
entity_overlap_score
source_support_score
contradiction_score
importance_score
memory_strength
confidence_score
retrieval_source_score
graph_depth_score
```

无训练模型时用预设权重经过 sigmoid；也可用 trace/标签训练线性 logistic ranker。仓库说明当前训练主要来自 heuristic judgments，而非高质量人工相关性标签。

StoryPal 必须把 `boundary_allowed` 作为排序之前的硬过滤，不能把 spoiler 安全仅作为一个低权重 feature。

## 11. Context Assembly

`assemble_context()` 并非简单按相似度拼 top-k，而是：

1. 按 timestamp 和因果优先级排序；
2. 用文本 Jaccard/embedding 相似度去重；
3. 按 query type、score、support、confidence 做质量过滤；
4. 控制同月份低分事件数量；
5. 按月分组；
6. 标记 belief shift；
7. 附加面向该 query type 的推理指令。

核心接口：

```python
assemble_context(
    events: list[MemoryEvent],
    query: str,
    shift_ids: set[str] | None = None,
    embeddings: list[list[float]] | None = None,
    query_profile: QueryProfile | None = None,
    ranking_details: list[dict] | None = None,
) -> str
```

这类“从 ranked set 重建叙事顺序”非常适合 StoryPal：聊天问题需要相关性召回，但回答上下文常应恢复到 story order。

## 12. Memory Strength 与会话反馈

查询结束后，系统把 retrieved、selected、referenced、used_in_answer、ignored 等 interaction 反馈给 memory service，更新 retrieval count/strength；session 记录已选择和已探索 event IDs。

个人记忆可以随使用强化或衰减；既有作品事实不应因“较少被问到”而遗忘。StoryPal 可保留访问统计用于缓存和评估，但不能让它改变事实是否可检索的安全边界。

## 13. 可观测性

一次查询可回放：

```text
1 Query Classification
2 Vector Retrieval
3 Graph Retrieval
4 BM25 Retrieval
5 Fusion
6 Reranking
7 Timeline Reconstruction
8 Final Answer
```

每阶段记录 latency，并保留候选列表、特征分和最终 context。这正是 StoryPal 目前最应该优先吸收的部分。

建议 StoryPal 的 debug result：

```json
{
  "query": "...",
  "work_id": "wandering_earth",
  "max_order": 32,
  "channels": {"fts": [], "vector": []},
  "filtered_future_ids": [],
  "fused": [],
  "selected": [],
  "context_order": [],
  "latency_ms": {},
  "fallbacks": []
}
```

## 14. StoryPal Adapter 建议

保持原有薄接口，同时增加可选 explain：

```python
class StoryMemory:
    def search(
        self,
        query: str,
        *,
        work_id: str | None = None,
        max_order: int | None = None,
        top_k: int = 8,
        filters: dict | None = None,
        retrieval: str = "auto",
        explain: bool = False,
    ) -> list[dict] | dict: ...

    def timeline(
        self,
        concept: str,
        *,
        work_id: str,
        max_order: int,
        top_k: int = 20,
    ) -> list[dict]: ...
```

`explain=False` 保持 Chatbot 工具结果精简；`True` 返回候选通道、过滤原因和分数，供测试/开发 UI 使用。

## 15. 存储选择建议

不建议当前引入 Qdrant + Neo4j：

- 三部作品规模小；
- 增加 Docker 与双数据库运维；
- source-of-truth 容易分散；
- 图关系质量尚未证明能提高陪读回答。

更合适的阶段性实现：

```text
JSONL                    canonical units/events
SQLite FTS5              lexical + metadata/order
LanceDB                  optional vectors
SQLite relation table    optional event/entity/causal edges
```

先用 SQLite recursive CTE 或内存邻接表验证 1–2 hop 因果检索，再决定是否需要图数据库。

## 16. 评估体系

ChronoMind 评估 Recall@K、MRR、NDCG@10、Temporal Ordering Accuracy 和 Redundancy Score。StoryPal 应增加安全与证据指标：

- Evidence Recall@K；
- answer evidence coverage；
- story-order reconstruction accuracy；
- causal chain completeness；
- redundant unit ratio；
- future-unit leakage rate；
- work-isolation error rate；
- fallback rate 与各阶段 latency。

## 17. 已知限制

上游明确承认：质量受 seeded corpus 限制；图算法部分为近似；ranker 标签主要是 heuristic；10k 压测只验证了生成 artifact；LLM latency 在检索栈之外。

此外，代码复杂度与演示功能较多，应把它视为架构参考而不是成熟依赖。

## 18. 推荐开发顺序

### Phase 1：统一候选与 trace

让 FTS5、vector 和 scan fallback 输出同一候选结构及通道信息。

### Phase 2：硬边界 + 融合

先应用 `work_id/max_order`，再做 RRF 或简单归一化融合。

### Phase 3：叙事顺序重建

选中结果按 order 重排，并保留 relevance rank 与 context order 两套序号。

### Phase 4：事件关系实验

只在真实 bad cases 上验证因果邻接是否带来增益。

## 19. Definition of Done

- [ ] 所有召回通道有统一候选结构；
- [ ] `work_id/max_order` 在融合前硬过滤；
- [ ] explain trace 可看到召回、过滤、融合、选取和降级；
- [ ] 最终 context 可按 story order 重建；
- [ ] 混合检索相对单路检索有离线指标或 bad-case 证据；
- [ ] 不因记忆热度改变作品事实边界；
- [ ] 暂不引入无收益证明的 Neo4j/Qdrant。

## 20. 来源与许可证提醒

- 官方仓库：https://github.com/sunandatata/ChronoMind
- 本文依据 README、Graph/Ranking/Retrieval 文档和 FastAPI models/routes/services 实际代码核查。
- 仓库根目录未发现 LICENSE 文件；不能默认复制或衍生其代码，只宜参考公开架构思想，复用前需取得许可。
