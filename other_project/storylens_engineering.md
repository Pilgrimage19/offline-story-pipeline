# StoryLens：叙事事件抽取与诊断工程文档（调研版）

> 研究对象：`roopamgarg/StoryLens`，仓库产品名为 Narrative Checker。
> 核查版本：root `fe580f2`、LLM layer `9a00e23`、Web app `fe3d007`（2026-09-10）。
> 目的：说明其抽取管线、数据契约、服务接口和可视化诊断，并判断哪些能力可融入 StoryPal。

---

## 1. 项目定位

StoryLens 接收一段自由文本故事，调用 LLM 抽取结构化事件，再把事件转换为时间线图和人物关系图，并运行确定性叙事诊断。

```text
Story Text
  → LLM Event Extraction
  → Strict Validation
  → Event Array
  → Timeline / Character Graph
  → Diagnostics UI
```

它不是 StoryMemory，也不是陪读 Chatbot：没有作品库、阅读进度、检索索引、对话记忆和防剧透边界。它最值得 StoryPal 借鉴的是“LLM 抽取结果必须穿过严格契约和证据校验”这一层。

## 2. 仓库与职责边界

根仓库用 Git submodule 协调两个独立仓库：

```text
StoryLens/
  llm-layer/  Fastify API + contracts + LLM extractor
  web-app/    Next.js UI + proxy + graph transforms + diagnostics
```

职责划分：

- `packages/contracts`：Zod schema 和 TypeScript 类型，是后端 canonical contract。
- `packages/llm-extractor`：prompt、provider adapter、JSON 解析、重试和语义校验。
- `apps/api`：鉴权、限流、body guard、HTTP 状态码和 request ID。
- Web API route：隔离浏览器与后端密钥，处理超时、取消和错误归一化。
- Web client：事件到图模型的确定性转换，以及本地诊断。

当前根仓库锁定的 `llm-layer` submodule commit 已无法从远端取回；本文改为核查两个子仓库当前 `main`。因此不能保证它们与根仓库 README 描述的历史组合逐字一致。

## 3. 端到端抽取流程

```text
POST Web /api/extract
  → 校验请求 + 并发限制
  → 可选代词解析
  → POST LLM /v1/events/extract
  → buildExtractionPrompt(story)
  → provider.generateJson(...)
  → fenced JSON recovery / parse
  → llmEventSchema.validate
  → sourceText 原文包含校验
  → 服务端生成 eventId
  → response schema 再校验
  → Web graph transform + diagnostics
```

抽取层会在 JSON 解析、schema 校验或 source match 失败后重试。`LLM_MAX_RETRIES=2` 的实现语义是初始调用加最多两次重试。

## 4. 核心事件数据模型

LLM 只能生成以下字段：

```ts
type LlmEvent = {
  action:
    | "MOVE" | "SPEAK" | "ATTACK" | "DEFEND" | "DISCOVER"
    | "INTERACT" | "EMOTIONAL_CHANGE" | "STATE_CHANGE"
    | "ALLIANCE_CHANGE" | "ITEM_CHANGE";
  actors: string[];
  targets?: string[];
  location?: string;
  timeHint?: string;
  sourceText: string;
  confidence: number;
};
```

服务端在校验后追加 `eventId: UUID`。README 将 ID 描述为稳定、可信边界，但实现使用 `randomUUID()`，只保证单次响应内不由模型控制，并不保证同一文本重跑得到相同 ID。StoryPal 若需要持久 StoryMemory，必须使用基于 `work_id + source span + extractor version` 的确定性 ID。

## 5. 后端 HTTP 接口

### 5.1 `POST /v1/events/extract`

请求：

```json
{
  "story": "Alice entered the station...",
  "metadata": {"storyId": "optional-client-id"}
}
```

约束：`story` 非空；默认 `MAX_STORY_CHARS=50000`；Fastify body 上限 512 KiB；metadata 为 strict object。

成功响应：

```json
{
  "events": [{
    "eventId": "uuid",
    "action": "MOVE",
    "actors": ["Alice"],
    "location": "station",
    "sourceText": "Alice entered the station",
    "confidence": 0.93
  }],
  "model": "gemini-2.5-flash",
  "usage": {"promptTokens": 100, "completionTokens": 40, "totalTokens": 140},
  "warnings": [],
  "requestId": "uuid"
}
```

错误 envelope：

```json
{
  "error": {
    "code": "INVALID_REQUEST | EXTRACTION_FAILED | PROVIDER_ERROR | RATE_LIMITED | INTERNAL_ERROR",
    "message": "...",
    "details": {}
  },
  "requestId": "uuid"
}
```

状态映射：400 请求错误、401 API key、413 过长、422 抽取失败、429 限流、502 provider、500 未处理错误。

### 5.2 健康检查

```text
GET /healthz → {"status":"ok"}
GET /readyz  → {"status":"ready"}
```

当前 ready endpoint 不检查 Gemini 连通性，只表示应用进程可响应。

## 6. Provider 内部接口

```ts
interface LlmProvider {
  generateJson(
    prompt: string,
    config: { model: string; timeoutMs: number }
  ): Promise<{
    text: string;
    model: string;
    usage?: { promptTokens: number; completionTokens: number; totalTokens: number };
  }>;
}
```

配置允许 `provider = "gemini" | "custom"`，但仓库实际内置实现只有 Gemini；`custom` 需要调用方自行提供 adapter，不能视为开箱即用。

## 7. 证据和可靠性

`STRICT_SOURCE_MATCH=true` 时，系统压缩空白后检查：

```text
normalizedStory.includes(normalizedSourceText)
```

优点是阻止完全虚构的证据片段；不足是不能验证事件解释是否忠于原文，不保存字符 offset，且改写或跨段证据可能被拒绝。

StoryPal 应将它升级为 provenance：

```json
{
  "source_id": "...",
  "source_sha256": "...",
  "start_char": 1200,
  "end_char": 1248,
  "raw_text": "..."
}
```

## 8. Web 代理接口

浏览器调用 `POST /api/extract`。代理会校验请求、限制进程内并发、可选运行代词解析、只向上游转发 `storyId`、传递 API key，并处理 client abort、上游 timeout、不可达和无效响应。

代理可能返回 499（客户端取消）和 504（上游超时）。进程内计数不适用于多实例全局限流。

## 9. 图模型与诊断

同一 Event[] 被转换成两种视图：timeline 按事件序列展示；character 按人物共现、动作和目标建立关系。

诊断规则是客户端确定性 heuristic，覆盖：

- 时间提示冲突与顺序问题；
- 弱连通分量和缺失链接；
- 缺少前置原因、链条断裂和环状因果；
- 相同签名事件重复；
- 某类动作连续重复形成潜在 narrative loop；
- 物品使用早于获得等 dependency reversal；
- 人物在连续事件间同时换地点和动作形成 abrupt transition。

诊断包含 category、subtype、severity、message、confidence、node IDs 和 evidence IDs。它适合作为抽取 QA，不应直接被当成文学真值。

## 10. 存储模型

StoryLens 当前没有持久 Story Store。一次请求产生一次 Event[]，Web 前端直接消费；没有 JSONL、SQLite、向量索引或多作品隔离。

若融入 StoryPal，建议：

```text
units_extracted.jsonl   = 单元与原文事实源
events_extracted.jsonl  = 事件事实源，引用 story_unit_id
SQLite/LanceDB          = 可重建索引
diagnostics.jsonl       = 抽取诊断，不进入用户回答事实
```

## 11. 与 StoryPal 的 Adapter 建议

```python
class StoryEventStore:
    def list_events(
        self,
        work_id: str,
        *,
        min_order: int | None = None,
        max_order: int | None = None,
        actors: list[str] | None = None,
        actions: list[str] | None = None,
    ) -> list[dict]: ...

    def get_event(self, work_id: str, event_id: str) -> dict | None: ...

    def get_event_evidence(self, work_id: str, event_id: str) -> list[dict]: ...
```

事件必须引用现有 Story Unit：

```json
{
  "event_id": "wandering_earth:u0007:e02",
  "work_id": "wandering_earth",
  "story_unit_id": "u0007",
  "order": 7,
  "action": "STATE_CHANGE",
  "actors": ["..."],
  "evidence_spans": [{"start_char": 10, "end_char": 42}],
  "extractor_version": "event-v1"
}
```

在线 `StoryMemory.search()` 仍返回统一 Evidence；event 只是召回和 rerank 信号，不能取代原文。

## 12. StoryPal 可优先吸收的能力

1. 为所有 LLM 抽取字段建立 strict schema 和版本号。
2. 保留模型原始输出、校验错误、重试次数和 request/job ID。
3. 验证 evidence span 确实位于输入单元。
4. 把诊断结果作为离线 QA artifact，支持定位到 unit/event。
5. 增加人物/事件视图用于开发者核验，而非首版用户百科页。

## 13. 不建议照搬的部分

- AWS Lambda、API Gateway、ECR、Terraform：当前离线本地管线不需要服务化。
- 只靠十类动作表示完整故事：人物状态、因果、知识变化和叙事视角仍需单独实验。
- 随机 UUID：不适合作为可重复构建的 StoryMemory ID。
- 单次 50k 字符整体抽取：长篇需要分段、重叠、跨段合并和实体归一化。
- Web 端 heuristic 诊断：应在离线构建时运行并持久化。

## 14. 测试策略

原项目已有 contracts、extractor、API route、graph transform、diagnostics 和代词解析测试。StoryPal 适配后还应增加：

- 同一输入重跑 ID 稳定性；
- evidence offset 与原文精确回溯；
- 跨 unit 同一人物归一化；
- 跨段事件合并与重复率；
- `max_order` 下事件不越界；
- event 检索对现有对话式用例的增益/退化；
- 抽取失败后局部重跑和缓存命中。

## 15. 建议开发顺序

### Phase 1：Schema 实验

在《流浪地球》前 10 个 unit 上抽取事件，冻结 provenance、event ID 和 action/status 字段。

### Phase 2：确定性构建

实现 cache、schema version、stable ID、错误 artifact 和局部重跑。

### Phase 3：检索实验

比较原文、summary、event 文本和多路融合；只有确有增益才进入默认索引。

### Phase 4：开发者诊断页

展示 unit → event → evidence → diagnostics，不直接做用户人物百科。

## 16. Definition of Done

- [ ] Event schema 有版本且经过严格校验；
- [ ] 每个 event 引用 work、unit、order 和原文 span；
- [ ] 同输入重建得到同一 stable ID；
- [ ] 抽取错误和 warnings 可追踪；
- [ ] 支持按 `max_order` 过滤；
- [ ] 可回到真实原文；
- [ ] 已证明 event 层对至少一类陪读检索有增益；
- [ ] 诊断仅作为 QA 信号，不被误当成故事真值。

## 17. 来源与许可证提醒

- Root：https://github.com/roopamgarg/StoryLens
- LLM layer：https://github.com/roopamgarg/storylens-llm-layer
- Web app：https://github.com/roopamgarg/storylens-webapp
- 核查时两个子仓库根目录均未发现 LICENSE 文件；借鉴代码前必须向上游确认许可证。本文只做架构分析。
