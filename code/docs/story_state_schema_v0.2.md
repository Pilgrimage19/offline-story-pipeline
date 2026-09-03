# Story State & 状态流抽取 Schema（v0.2 定稿）

> 状态：已与负责人确认方向，等待代码实现验证后微调。
> 关联设计：`offline_story_pipeline_v0_plan.md` 第 6 节「状态流」。
> 核心原则：**LLM 只产出增量，state 的合并/记账由代码确定性完成**（可重放、可测试）。

## 1. 状态机

```
初始 state（空）
对 unit 1..N 按 order 顺序：
  输入 = 本段原文 + 章节 + state 精简视图 + 最近 1–2 段原文(消指代)
  LLM 返回「增量」JSON（不含整份 state）
  代码侧确定性 apply：entity/plotline 归并、recent 更新、快照 = 处理后的 state
每 chapter 结束：一次「章节压缩」调用（backdrop_buffer → backdrop），落 state 检查点
```

- `process(unit_k, state_{k-1}) -> (unit_k 的理解, state_k)`
- 离线整本跑 = 未来在线随读增量跑的同一状态机，产物逐位一致（用前向一致性验证检验）。

## 2. StoryState（落盘 JSON）

```jsonc
{
  "schema_version": "story-state@0.2",
  "work_id": "wandering_earth",
  "last_order": 45,

  "characters": [
    { "name": "刘欣",
      "aliases": ["他(当前语境)", "小刘"],
      "status": "在矿场主持地下气化试验",
      "note": "可选，尽量短",
      "first_seen_order": 12,
      "last_seen_order": 45 }
  ],
  "objects": [
    { "name": "地球发动机", "kind": "tech",
      "status": "已知事实/当前状况（如：共一万二千台）",
      "note": "",
      "first_seen_order": 1, "last_seen_order": 26 }
  ],
  "locations": [
    { "name": "北京地下城", "note": "",
      "first_seen_order": 3, "last_seen_order": 44 }
  ],
  "plotlines": [
    { "title": "飞船派 vs 地球派",
      "status": "closed",                       // open | closed
      "opened_order": 19, "closed_order": 25,
      "summary": "校园冲突后以联合政府决议启航告终",
      "key_orders": [19, 20, 21] }
  ],
  "recent": [                                   // 最近 ≤5 条，供消指代
    { "order": 44, "text": "加代子提出去看地球发动机推进器" }
  ],
  "backdrop": "更早历史的压缩摘要（≤150字，章节压缩产出）",
  "backdrop_buffer": [                          // 本章内 recent 溢出，章节末进 backdrop
    { "order": 30, "text": "……" }
  ]
}
```

记账规则（代码侧）：

- 实体按 `name` 或 `aliases` 匹配；匹配到 → 更新 `status/note/last_seen_order`；否则新建（`first_seen_order = 当前 order`）；
- 实体/事件线**只增不改删**；closed 的 plotline 保留 `summary`；
- `recent` 超 5 条 → 最旧移入 `backdrop_buffer`；
- chapter 结束时一次 LLM 压缩调用：`backdrop_buffer` + 旧 `backdrop` → 新 `backdrop`（限长 ~150 字），清空 buffer。

## 3. 每个 unit 的 LLM 输出（增量 JSON）

```jsonc
{
  "summary": "1~2 句：本段发生了什么（结合前文状态）",
  "entity_updates": [
    { "name": "加代子",
      "type": "character | object | location",   // 默认 character
      "status": "当前状态/新处境一句话",
      "note": "可选" }
  ],
  "plotline_updates": [
    { "action": "open | advance | close",
      "title": "事件线标题（沿用已有或新建）",
      "note": "一句话" }
  ],
  "context_refs": [ "we-0012", { "entity": "反物质炸弹" } ],
  "recent_event": { "text": "一句话：这段把剧情推进到了什么" }
}
```

## 4. LLM Prompt

### 4.1 系统提示（主抽取）

> 你是小说剧情陪伴 AI 的 Story Reader。你按顺序阅读作品的剧情单元，并维护一份"截至当前的故事状态"。规则：
> 1. 只能依据【本段原文】与【当前故事状态】作答，不得引入其中没有的信息（防止剧透后文）；
> 2. 实体一律沿用状态中已有的 canonical 名，不要另起新名；新实体才新建；
> 3. 理解要结合前文状态：能消解的指代（"他/那个东西"）必须消解到具体实体再写入实体更新；
> 4. 只输出一个 JSON 对象，字段见用户消息，不要输出多余文字。

### 4.2 用户消息

```text
作品：《{title}》   章节：{chapter_name}   本段：第 {order} 段

【当前故事状态（本段之前）】
{state 精简视图：人物 name——status；物件；进行中事件线；最近 5 条；backdrop}

【紧接着的上文原文（仅用于消解指代，不是本段）】
{最近 1~2 段原文}

【本段原文】
{unit.text}

请输出 JSON：
{ "summary": "...", "entity_updates": [...], "plotline_updates": [...],
  "context_refs": [...], "recent_event": {"text": "..."} }
```

### 4.3 章节压缩调用（每 chapter 末一次，纯文本输出）

> 你负责把本章尚未归档的事件压缩进故事总述 backdrop。要求：≤150 字，保留关键实体名与因果链，可参照旧 backdrop 去重。
> 输入：旧 backdrop + backdrop_buffer 事件列表。只输出压缩后的 backdrop 文本。

## 5. Mock 降级语义

mock 只产出 `summary`（首句截断）与 `recent_event`，实体/事件线更新为空；
章节压缩退化为 buffer 文本拼接。用途：**验证管道机制**（字段齐全、last_order 单调、
快照逐 order 落盘、前向一致性），不验证语义质量。LLM 语义质量需填 key 后验证。

## 6. 缓存 / 检查点 / 容错

- 主抽取 cache 键 = `sha256(prompt_version + state 指纹 + 本段文本 + 前 1~2 段文本)`；
  前序 state 变化 ⇒ 下游链缓存自动失效（连锁重抽是状态流固有代价）；
- 章节压缩 cache 键 = `sha256(prompt_version + compress + 章节名 + state 指纹)`；
- 每 chapter 末落 `03_extracted/checkpoint_ch{idx}.json`（state 检查点，供回滚/局部重跑）；
- 单 unit 失败：降级（summary 兜底）且**不更新 state**、标记 degraded；缓存回放保留 degraded 标记，保证重放一致；
- 缓存目录带 prompt_version 标记文件：版本升级自动清空旧缓存。

## 7. 前向一致性验证

只跑前 p 个 chunk 的增量结果（同 extractor、走缓存），必须与整本跑出的前 p 个结果
（summary / entity_updates / plotline_updates / context_refs / recent_event / degraded / state_snapshot）逐字段一致。
通过 ⇒ 离线整本处理等价于未来在线随读增量。
