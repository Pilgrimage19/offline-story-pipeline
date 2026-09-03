# 剧情陪伴 AI：Online Chatbot 工程文档（V0）

> 面向负责 **在线 Chatbot** 的开发者。  
> 当前已经存在一个“带 memory 和简单 tools 的 Chatbot 原型”，但源码尚未重新核验。  
> V0 的第一原则是 **优先复用现有原型，而不是重新搭框架**。

## 1. 产品定位

这是一个 **剧情陪伴 Chatbot**。

典型使用过程：

```text
用户第一次阅读 / 观看 / 游玩某个故事
        ↓
遇到想回忆、吐槽、讨论的剧情
        ↓
随时向 AI 说话
        ↓
AI 能够：
- 理解用户在指哪个实际剧情；
- 调取用户当前进度以前的 Story Memory；
- 结合最近对话；
- 记得用户过去的观点；
- 必要时调用外部工具；
- 不泄露后续剧情。
```

它不是：

- 角色扮演 Agent；
- 单纯小说 QA；
- 推荐系统；
- 纯知识库 RAG。

## 2. 当前与未来的关系

当前 V0：

```text
Offline Story Pipeline
        ↓
预处理 Story Memory
        ↓
Online Chatbot
```

未来计划：

```text
实时 Text / Subtitle / Game / VLM Input
        ↓
Incremental Story Understanding
        ↓
Story Memory
        ↓
Online Chatbot
```

因此 Chatbot 应通过抽象接口读取 Story Memory，不要绑定“小说预处理”的具体数据结构。

## 3. 当前工程范围

Chatbot V0 需要完成：

1. 核验并复用现有 Chatbot；
2. 保持正常多轮对话；
3. 接入 Story Memory；
4. 保存当前作品和阅读进度；
5. 在检索层执行 spoiler gating；
6. 接入或完善 Conversation Memory；
7. 保留现有 tool calling 能力；
8. 给 Story / Memory / Tool 形成清晰调用边界；
9. 记录基本 trace，方便之后分析 bad case；
10. 做一组真实剧情陪伴测试。

## 4. V0 不要求

第一版不要为了架构漂亮重写：

- 完整 Agent Framework；
- 复杂 Planning；
- 多 Agent；
- 长链 ReAct；
- 完整用户画像系统；
- Story Knowledge Graph；
- 复杂 Memory Reflection；
- 实时视频理解；
- UI 大改；
- 生产级部署；
- 大规模并发。

## 5. 建议先核验现有 Chatbot

现有原型已经具备：

- Chat；
- Memory；
- 一些简单 Tools。

因此第一步不是开发，而是本地代码审计。

另外提供独立文件：

`CODEX_CHATBOT_AUDIT.md`

建议让 Codex 在本地 repo 内执行该审计，再决定改造方案。

## 6. 暂定总体架构

```text
                User
                  ↓
        Existing Chatbot Loop
                  ↓
        ┌───────────────────┐
        │ Context / Session │
        └─────────┬─────────┘
                  ↓
        Chat Orchestration
         /        |        \
        /         |         \
 StoryMemory  Conversation   Tools
   Adapter       Memory
                  |
               EverOS?
```

后续才逐渐补：

```text
Query Analyzer
Intent / source routing
Query rewrite
Entity/coreference resolution
```

这些属于 Chatbot 层，不属于离线 Story Pipeline。

## 7. Session State

V0 至少维护：

```python
@dataclass
class ChatSessionState:
    session_id: str
    active_work_id: str | None
    story_progress: int | None
```

可选：

```python
spoiler_mode: str = "strict"
user_id: str | None = None
```

### 7.1 active_work

当前作品：

```text
wandering_earth
support_humanity
earth_fire
```

V0 可以允许用户显式切换：

```text
“我们现在聊《地火》”
```

也可以由 UI 下拉框完成。

首版不必让 LLM 每轮自动猜 work。

### 7.2 story_progress

当前用户看到的位置。

V0 只需要一种稳定数值：

```text
story_progress = order
```

它来自 Story Pipeline 的可比较顺序字段。

Story retrieval 强制：

```python
max_order = story_progress
```

这比 prompt 级“不要剧透”更可靠。

## 8. Story Memory：暂定接口

Chatbot 不读取底层表。

暂定：

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

返回统一 Evidence：

```json
{
  "work_id": "earth_fire",
  "unit_id": "stable-id",
  "order": 71,
  "raw_text": "...",
  "summary": "...",
  "score": 0.82,
  "metadata": {}
}
```

注意：

> 这是联调接口，不代表 Story Pipeline 最终存储结构。

## 9. Chatbot 对 Story Memory 的最低调用方式

第一版甚至可以非常简单：

```text
User Query
   ↓
如果当前处于作品对话
   ↓
story_memory.search(...)
   ↓
Story Evidence
   ↓
拼入模型上下文
   ↓
Answer
```

暂时不要求复杂 Router。

但一定要保留接口，以便后续升级为：

```text
Query
↓
Query Analyzer
├ Story Memory
├ Conversation Memory
├ Web/Tool
└ No Retrieval
```

## 10. Conversation Memory

Story Memory 和 Conversation Memory 必须逻辑分开。

### Story Memory

```text
作品发生了什么
```

- application/work-scoped；
- 当前 V0 离线生成；
- 只读；
- 有严格故事顺序。

### Conversation Memory

```text
用户说过什么
用户怎么看故事
用户之前猜过什么
```

- user/session-scoped；
- 在线持续产生；
- 可写；
- 需要长期检索。

## 11. EverOS 的 V0 定位

EverOS 可以作为候选 Conversation Memory Backend，但不要把所有 memory 行为绑死给它。

推荐：

```python
class ConversationMemory:
    def add_messages(self, ...):
        ...

    def search(self, query: str, ...):
        ...

    def flush(self):
        ...
```

Chatbot 只依赖这个 Adapter。

后端可以是：

```text
Existing Prototype Memory
EverOS
Simple Local Memory
```

### 11.1 EverOS 第一版建议

目标不是完整使用 EverOS 全功能。

当前更适合：

```text
recent conversation
        ↓
buffer / chunk
        ↓
达到阈值
        ↓
background extraction
        ↓
persistent memory
```

优先观察：

- Episode；
- Atomic Fact；
- hybrid retrieval；
- persistence。

第一版谨慎使用：

- Profile；
- Foresight；
- Reflection；
- Agent Case；
- Agent Skill；
- agentic retrieval。

理由：剧情讨论中的观点经常发生变化：

```text
“我觉得刘欣很理想主义”
        ↓
“现在觉得有点偏执”
        ↓
“最后还是挺尊敬他的”
```

第一版更重要的是保留原始历史，而不是过早压缩成稳定 persona。

## 12. Recent Context + Long-term Memory

推荐回答上下文：

```text
System Prompt
+
Current Work / Progress State
+
Retrieved Story Evidence
+
Retrieved Conversation Memory
+
Recent Conversation
+
Current User Query
```

具体顺序可实验。

不要把所有历史消息永久堆进 context。

## 13. 一个典型的双 Memory 场景

用户读《地火》时说：

```text
“我觉得刘欣现在已经有点太固执了。”
```

Story Memory 用于理解：

```text
当前剧情
刘欣正在做什么
他现在掌握什么信息
```

Conversation Memory 用于保存：

```text
用户此时认为刘欣“太固执”
```

之后用户问：

```text
“看到这里，你觉得我之前说他太固执还对吗？”
```

Chatbot 同时需要：

```text
当前 Story Evidence
+
过去 User Opinion
```

这类体验是当前项目的重要验证目标。

## 14. Query Analyzer：暂不锁死，但要留位置

当前不要求一次设计完整。

未来它可能负责：

- 当前 query 是否需要 Story Memory；
- 是否需要 Conversation Memory；
- 是否需要 Web/Tool；
- “他/这里/刚才”指代消解；
- query rewrite；
- entity extraction；
- temporal intent；
- retrieval depth。

例如：

```text
“我之前是不是就说过他会出问题？”
```

不应该直接把原句 embedding 后搜索所有 memory。

后续可以重写为：

```text
用户此前是否预测《地火》中刘欣当前行为会导致后续问题
```

但这属于后续增强项。

## 15. Tools

现有 Chatbot 已有简单工具能力，优先复用。

V0 需要核验：

- tool schema；
- tool dispatch；
- tool result 如何回到模型；
- 是否支持 async；
- 是否有 tool error handling；
- 是否有超时；
- 是否把 tool trace 记录下来。

未来可能包含：

- Web search；
- Wiki；
- Story Memory；
- Conversation Memory；
- progress control。

Story Memory 不一定要实现成模型可见 Tool；也可以由 orchestration 层直接调用。

## 16. Web / 现实知识的边界

剧情问题：

```text
“《地火》里刘欣为什么这么做？”
```

优先 Story Memory。

现实问题：

```text
“地下煤炭气化现实里真的可行吗？”
```

走 Web / external knowledge。

混合问题：

```text
“小说里这个技术和现实有什么差别？”
```

后续 Query Analyzer 可以同时调用：

```text
Story Memory + Web
```

V0 不要求把此路由做到完美。

## 17. Response 原则

Chatbot 回答应像“陪你看/读的人”，而不是：

```text
知识库检索结果如下：
1...
2...
```

应保持自然讨论。

同时：

- 不引用用户尚未看到的剧情；
- 不把推测说成原文事实；
- 如果 Story Evidence 不足，承认不确定；
- 可以讨论人物动机，但要区分“文本事实”和“解释”。

## 18. Trace / Logging

为了后续真正知道哪里坏了，每轮至少记录：

```json
{
  "session_id": "...",
  "active_work": "...",
  "progress": 100,
  "user_query": "...",
  "story_query": "...",
  "story_hits": [],
  "memory_query": "...",
  "memory_hits": [],
  "tools": [],
  "answer": "..."
}
```

如果当前原型已有 tracing，复用现有格式。

## 19. V0 测试集

三篇作品读完后构造约 20–30 个真实 query。

覆盖：

### Story retrieval
- 单事实；
- 前文回忆；
- 人物动机；
- 因果；
- 多段 evidence；
- “刚才/之前/他”这类自然语言。

### Progress
- 当前进度内问题；
- 后续剧情不能泄露。

### Conversation Memory
- 用户之前的观点；
- 用户观点变化；
- 用户以前的猜测；
- 跨 session 回忆。

### Routing / Tools
- 纯剧情；
- 纯现实知识；
- 混合剧情 + 现实。

## 20. Bad Case 比平均分更重要

建议保存至少 5 个失败案例，例如：

```text
1. story retrieval 找错上下文；
2. 人称代词导致 memory miss；
3. 用户观点被过度总结；
4. progress filter 漏掉 spoiler；
5. tool routing 误判；
6. Story 与 Conversation Memory 混淆；
7. cross-work contamination。
```

后续迭代就围绕这些 bad cases。

## 21. 第一阶段建议开发顺序

### Phase 0：代码审计
先让 Codex 审查已有 Chatbot。

### Phase 1：保持原功能
确认：
- 普通聊天可用；
- 原 memory 可用；
- tool loop 可用。

### Phase 2：接 StoryMemory Adapter
先硬编码 active work + progress 都可以。

### Phase 3：Spoiler-safe retrieval
确保 Story Memory 不返回未来内容。

### Phase 4：Conversation Memory
先评估现有 memory：
- 如果够用，暂不换；
- 如果明显不足，再接 EverOS；
- Memory Backend 必须可替换。

### Phase 5：Trace + Eval
跑真实剧情对话。

### Phase 6：再决定 Query Analyzer
不要在没有 bad case 的时候提前复杂化。

## 22. 开发约束

1. **不要重写已有 Chatbot，除非审计确认架构无法扩展。**
2. Story Memory 通过 Adapter 接入。
3. Conversation Memory 通过 Adapter 接入。
4. 不在 Chatbot 内重新实现 Story Pipeline。
5. 不把 Story 与 User Memory 放到同一个 collection 后靠 prompt 区分。
6. progress / spoiler 尽量在 retrieval 层控制。
7. 第一版优先跑通闭环，而不是追求 Framework 完整度。
8. 所有组件都应能单独替换。

## 23. 当前联调契约

Offline 侧交付：

```python
StoryMemory.search(...)
StoryMemory.get_unit(...)
StoryMemory.list_works(...)
```

Chatbot 侧维护：

```python
ChatSessionState.active_work_id
ChatSessionState.story_progress
```

调用：

```python
hits = story_memory.search(
    work_id=session.active_work_id,
    query=story_query,
    max_order=session.story_progress,
    top_k=8,
)
```

这就是当前两边唯一需要真正提前冻结的部分。

其余 Story schema 可继续演化。

## 24. Future Roadmap

V0：

```text
Offline Story Package
→ Online Chatbot
```

后续：

```text
Preprocessed Subtitle
→ incremental unlock
→ Chatbot
```

再后续：

```text
Live Game Text / Screen / Video
→ realtime or near-realtime Story Reader
→ incremental Story Memory
→ Chatbot
```

可以参考 open-watch-cinema 一类“预处理内容 + 按观看进度释放给 AI”的中间形态。

长期目标仍然是：

> **AI 能真正跟随用户共同经历故事，而不是只能在用户看完后问答。**

## 25. V0 Definition of Done

- [ ] 已有 Chatbot 功能完成审计；
- [ ] 普通多轮对话保持可用；
- [ ] StoryMemory Adapter 接通；
- [ ] 可以选择 active work；
- [ ] 可以设置 / 更新 story progress；
- [ ] retrieval 不越过 progress；
- [ ] Conversation Memory 可工作；
- [ ] Story Memory 与 User Memory 独立；
- [ ] 原有 tools 保持可用；
- [ ] 每轮有基础 trace；
- [ ] 三部作品均完成实际聊天测试；
- [ ] 至少记录 5 个 bad case；
- [ ] 后续 Query Analyzer 有明确插入位置，但 V0 不要求最终实现。
