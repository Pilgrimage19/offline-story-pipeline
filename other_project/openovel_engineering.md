# Openovel：本地优先互动叙事运行时工程文档（调研版）

> 研究对象：`Feed-Scription/openovel`。
> 核查版本：`1b4404e`（2026-09-10）。
> 目的：说明其双循环、多代理、文件原生状态与记忆接口，并评估对 StoryPal 在线侧和用户阅读手账的价值。

---

## 1. 项目定位

Openovel 是本地优先的 AI Interactive Fiction 桌面应用。用户输入主角行动，前台 narrator 快速流式生成下一段故事；后台代理随后维护世界状态、长期记忆、角色卡、叙事指导和内部笔记。

```text
Reader Action
  → Fast Foreground Narrator
  → Canon Append
  → Async Background Team
  → File-native State Update
  → Next-turn Context Compile
```

StoryPal 与它的区别是：Openovel 生成未来故事，StoryPal 解释既有作品并必须服从已读边界。因此可借鉴运行时和记忆分层，不能直接复用其叙事生成语义。

## 2. 核心设计判断

长程互动小说存在两个冲突目标：读者要尽快看到正文；世界状态和后果又需要慢速、细致维护。Openovel 用双循环解耦：

- foreground：单次流式模型调用，无工具、无文件写入；
- background：异步、有工具、可写文件；
- narrator 只读取已经编译的有限工作集；
- 后台分析与内部状态默认不直接进入 narrator prompt。

这一点对 StoryPal 的直接启发是：实时回答和会后记忆整理可以解耦，用户不必等待长期记忆提取完成。

## 3. 主运行链路

```text
Electron UI
  → SessionViewModel
  → SessionProcessor
  → 记录 reader action / scene event
  → fastActivateContextCards()
  → compileForegroundContext()
  → narrator provider stream
  → append chapters.md + scene_log.jsonl
  → post-narration options / background signal
  → BackgroundJob
  → BackgroundAgentRuntime
  → ToolLoop + ToolRegistry
  → story files
```

后台任务 fire-and-forget，并通过 job ledger、agent lease、inbox 和 resume snapshot 处理并发与应用重启后的恢复。

## 4. 常驻代理结构

默认 resident team 包含：

- Showrunner：协调与前台 guidance 组合；
- World Keeper：世界逻辑、连续性、幕后模拟、`state/`；
- Director：节奏、质量、选项和内部分析；
- Card Manager：角色/实体 context cards；
- Memory：长期故事记忆和用户观察；
- 可选 Render/Image/Music 等代理。

每个代理通过 YAML card 定义模型 profile、工具权限、write scope 和唤醒策略。写路径由 `writeGuard` 和工具层校验，避免多个代理同时把同一文件当作事实源。

关闭 `OPENOVEL_RESIDENT_TEAM` 后回退到单 Storykeeper 路径。

## 5. 文件存储模型

Openovel 不以向量库或数据库为默认 StoryMemory，而以普通 Markdown/JSON/JSONL 为事实基座。

```text
$OPENOVEL_HOME/
  settings.local.json
  memory/USER.md
  memory/OBSERVED.md
  memory/topics/
  context-cards/
  references/INDEX.md
  stories/<story-id>/

<story-root>/
  BRIEF.md
  canon/chapters.md
  canon/chapters.recent.md
  canon/scene_log.jsonl
  canon/PROVENANCE.md
  frontend/
  guidance/FG_template.md
  guidance/FOREGROUND.md
  guidance/cards.md
  guidance/cards.auto.md
  director/
  worldkeeper/
  state/
  context-cards/
  inbox/
  memory/MEMORY.md
  memory/topics/
  research/
  packets/ profiles/ jobs/
```

`OPENOVEL_HOME`、`OPENOVEL_STORY_ID`、`OPENOVEL_STORY_ROOT` 决定路径；项目也保留旧 `AI_STORY_*` 环境变量兼容。

## 6. Canon、State、Memory 的语义边界

### Canon

`chapters.md` 是面向作品正文的累积结果，`chapters.recent.md` 是近期镜像，`scene_log.jsonl` 是 append-only 运行事件。Canon 是发生过的故事，不等于压缩记忆。

### State

`state/` 可存数值、标志、角色状态和世界模拟数据。Markdown 适合叙述性 digest，JSON/YAML 适合结构化变量。

### Story Memory

`memory/MEMORY.md` 是滚出 recent canon 后 narrator 仍能回忆的长期事实入口；`memory/topics/` 保存展开内容。Memory agent 独占写入，其他代理的写工具会拒绝直接修改。

### User Memory

`USER.md` 是用户主动设置且对模型只读的偏好；模型观察写入 `OBSERVED.md`，避免自动观察覆盖用户明确意图。

## 7. Context 编译接口

核心模块接口为：

```js
compileForegroundContext({
  storyRoot,
  action,
  snapshot,
  memory,
  env
}) -> Promise<{
  text,
  report,
  sections
}>
```

具体参数会随内部版本调整，但语义稳定：读取 `FG_template.md` 的 `@include` 顺序，组合 `frontend/*`、精选/自动 context cards、story memory、用户偏好和近期 canon，并按预算裁剪。

`FOREGROUND.md` 是 runtime 生成的只读视图，后台代理应编辑 source sections 或 template，不能直接编辑它。

辅助能力包括：

```js
contextBudgetDefaults(env)
selectForegroundMemory(snapshot, maxChars)
stripVolatileGuidanceLines(text)
getLatestContextReport()
```

## 8. Context Card 接口

自动激活使用确定性触发，不额外调用 selector model：

```js
fastActivateContextCards({
  action,
  recentCanon,
  storyRoot
})

writeCardManifest(manifestPath, cards)
discoverContextCardIndex(...)
validateContextCardContent(raw)
findConflictingCards({ slug, content })
```

`cards.auto.md` 每回合按关键词匹配重写；`cards.md` 是后台精选的长期激活集合。系统还检测重复 slug/实体和编译后超预算。

## 9. Memory Review 接口与输出契约

Memory workflow 每回合读取 story snapshot 和 memory snapshot，模型最多运行两步，要求严格 JSON：

```json
{
  "memory": ["本回合形成的长期故事事实，最多 2 条"],
  "observed": ["跨故事有价值的用户观察，最多 1 条"],
  "references": ["可复用外部背景，最多 1 条"],
  "notes": ["处理说明"]
}
```

内部应用接口：

```js
getMemorySnapshot()
applyMemoryPatch({ memory, observed, references })
recordSceneEvent({ type: "memory_review_completed", ... })
```

策略是 story memory 积极维护；observed/reference 保守写入；纯瞬时情绪和逐句复述不应写入。

## 10. 后台代理运行接口

居民代理通过消息和任务接口协作：

```js
broadcastTurn({
  event,
  turnId,
  action,
  foreground,
  selectedEffect,
  backgroundSignal,
  wakeSubAgents
})

launchSubAgents({ turnId, action, foreground, backgroundSignal })
wakeAgent(agentId, { turnId, action, sourceAgent })
resumeResidentAgents({ interruptedAgents, turnId })
```

广播只传紧凑摘要和 `chapters.recent.md` 指针，不复制完整 prose。每个 agent 的活动锁按 `story root + agent id` 隔离，防止切换故事时旧任务阻塞新故事。

## 11. Tool 与权限模型

后台 `ToolRegistry` 注册文件读写、搜索、shell、web 等能力，`BackgroundAgentRuntime` 驱动有界 tool loop。关键安全规则：

- narrator 没有工具；
- agent 的路径写入必须落在自己 scope；
- Memory 目录由 Memory agent 独占；
- shell 工作目录固定为当前 story root，并受沙箱约束；
- foreground 与 background 文件严格分域；
- JSON 状态文件写入后必须仍可解析；
- 故事切换时运行任务绑定原 story root。

## 12. 配置接口

配置覆盖顺序：

```text
defaults
  → ~/.openovel/settings.jsonc
  → .openovel/settings.jsonc
  → .openovel/settings.local.json
  → environment variables
```

支持 JSONC、尾随逗号、`{env:VAR}` 和 `{file:path}`。桌面 UI 写入 `$OPENOVEL_HOME/settings.local.json`，启动时再镜像为环境变量。

Provider 支持内置和自定义 OpenAI-compatible/Anthropic endpoint，并可按 agent/model profile 路由。诊断入口为 `npm run config:doctor` 与 `npm run provider:doctor`。

## 13. 对 StoryPal 的可融入设计

### 13.1 异步阅读记忆维护

```text
Chatbot 立即回复
  ├─ 同步：StoryMemory evidence + session state
  └─ 异步：提取用户反应/问题/预测 → 待确认或写入 reading journal
```

不要让用户等待 Notes/HistoryMemory consolidation。

### 13.2 用户意图与模型观察分离

建议增加：

```text
user_notes.jsonl       用户明确要求保存
observed_signals.jsonl 模型观察，低信任、可过期
reading_journal.jsonl  阅读事件、感受、问题和预测
```

显式 Notes 优先级高于自动观察。

### 13.3 Context compiler

把 prompt 拼装集中到一个可测试函数，并输出 report：每个 section 的字符/token、来源、是否裁剪、纳入的 evidence IDs 和 spoiler boundary。

## 14. 不建议直接照搬

- 常驻多代理：StoryPal 当前工具量不足以证明收益，先保持有界单 Agent loop。
- 纯关键词 context cards：适合生成故事实体，不足以替代现有 BM25/向量检索。
- 背景代理自动改写故事事实：既有作品内容必须只读，不能被会话代理更新。
- 文件数量无限增长：需要 schema、版本和索引，否则长期维护会变成隐式数据库。

## 15. StoryPal 建议接口

```python
class ReadingJournal:
    def append(self, entry: dict) -> str: ...
    def list(self, *, work_id: str | None = None,
             entry_type: str | None = None,
             max_order: int | None = None,
             limit: int = 50) -> list[dict]: ...
    def get(self, entry_id: str) -> dict | None: ...

class ContextAssembler:
    def build(self, *, query: str, session_state: dict,
              recent_messages: list[dict],
              story_evidence: list[dict],
              memory_evidence: list[dict]) -> dict: ...
```

`ContextAssembler.build()` 建议返回 `messages` 与 `report`，而不只返回 prompt string。

## 16. 测试与评估

Openovel 原项目大量测试覆盖文件、context、tool loop、agent lease、story transaction、switch guard、memory、foreground parallel 和长程 probe。StoryPal 最值得复制的是“断言状态与持久化结果，而非逐字断言模型 prose”。

建议新增：

- 回答完成后后台记忆失败不影响用户回复；
- 重启后 journal 与 notes 可恢复；
- 自动观察不能覆盖显式 Notes；
- 工作切换不串记忆；
- context report 中所有故事 evidence 均满足 `order <= max_seen_order`；
- 同一事件重复出现时 consolidation 去重；
- 后台任务有 job ID、状态、错误和可重试记录。

## 17. 推荐开发顺序

### Phase 1：Context report

集中现有 Chatbot prompt assembly，记录各层来源和 token 使用。

### Phase 2：Reading Journal

实现用户确认写入的 append-only JSONL 和只读查询工具。

### Phase 3：异步 consolidation

从聊天历史提取候选记忆，先进入 pending/trace，不直接污染 Notes。

### Phase 4：后台任务恢复

只有真实使用出现丢任务后，再加入持久 job ledger 和 resume。

## 18. Definition of Done

- [ ] StoryMemory 事实保持只读；
- [ ] 显式 Notes、自动观察、阅读手账彼此隔离；
- [ ] 在线回答不等待后台 consolidation；
- [ ] Context assembly 有可检查报告；
- [ ] 后台失败不破坏会话主链路；
- [ ] 多作品状态隔离；
- [ ] 写入为 append-only 或可审计；
- [ ] 暂不引入无证据收益的常驻多代理。

## 19. 来源与许可证

- 官方仓库：https://github.com/Feed-Scription/openovel
- 许可证：Apache-2.0。
- 本文依据 README、配置文档和 `src/context`、`src/memory`、`src/runtime`、`src/workflows` 实际代码核查。
