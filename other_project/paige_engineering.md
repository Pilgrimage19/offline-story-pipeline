# Paige：无剧透读书聊天工程文档（调研版）

> 研究对象：`derekmpeterson/paige`。
> 核查版本：`81a669e`（2026-09-10）。
> 这是四个项目中与 StoryPal“首次阅读陪读”产品语义最接近的系统。

---

## 1. 产品定位

Paige 允许用户上传 EPUB、设置已读百分比，再围绕已读文本与 AI 对话。它的边界设计非常直接：只把阅读位置之前的原文交给模型，并用 system prompt 禁止使用训练知识中的后续剧情。

```text
EPUB
  → Parse ordered chapters
  → Select exact progress percent
  → Build read-so-far context
  → OpenRouter / local model
  → Streaming chat response
```

它不是离线 Story Understanding 管线：没有事件抽取、Story Unit、embedding、检索、人物状态或因果图。

## 2. 技术架构

```text
Next.js + React UI
  ├─ POST /api/parse-epub
  │    → temp file → epub2 parser → in-memory BookStore
  └─ POST /api/chat
       → bookId + progressPercent + messages
       → buildBookContext()
       → buildSystemPrompt()
       → Vercel AI SDK streamText()
       → OpenRouter or OpenAI-compatible local server
```

前后端在同一 Next.js 应用中。无鉴权、无数据库、无任务系统，适合单用户本地运行。

## 3. EPUB 数据模型

```ts
interface BookChapter {
  id: string;
  title: string;
  text: string;
  charOffset: number;
  charLength: number;
  tokenCount: number;
}

interface ParsedBook {
  title: string;
  author: string;
  totalCharacters: number;
  totalTokens: number;
  chapters: BookChapter[];
}
```

`charOffset` 是全书正文拼接意义下的累计字符位置。token 计数固定使用 GPT-4o tokenizer，只是跨模型估算。

对客户端返回的 `BookMeta` 不包含章节正文，只包含 bookId、书名、作者、总长度、总 token 和章节元数据。

## 4. EPUB 解析流程

解析器按 EPUB spine (`epub.flow`) 保持阅读顺序：

1. 读取 title/creator；
2. 从 NCX TOC 建立 id/href 到标题的映射；
3. NCX 为空时自行解析 EPUB3 `<nav epub:type="toc">`；
4. 逐 spine item 读取 HTML；
5. `html-to-text` 转纯文本，跳过图片和链接 URL；
6. 按多级 fallback 确定章节标题；
7. 计算字符偏移与 token 数；
8. 单章解析失败时跳过该章，而不是使整本书失败。

标题 fallback 顺序：spine title → TOC id → TOC href → image alt → h1/h2/h3 → `Section N`。

风险是“跳过失败章节”没有把 warnings 返回给用户，可能产生无声内容缺失。

## 5. EPUB 上传接口

### `POST /api/parse-epub`

请求为 multipart form-data：

```text
field name: epub
file type: .epub
maximum size: 50 MiB
```

处理过程先写入系统临时目录 `<tmp>/paige-epub/<uuid>.epub`，解析后在 `finally` 删除临时文件。

成功响应：

```json
{
  "bookId": "uuid",
  "title": "Book title",
  "author": "Author",
  "totalCharacters": 123456,
  "totalTokens": 32000,
  "chapters": [{
    "id": "chapter-1",
    "title": "Chapter 1",
    "charOffset": 0,
    "charLength": 4500,
    "tokenCount": 1100
  }]
}
```

错误：400 缺文件/扩展名不符，413 超过 50 MiB，500 解析失败。

## 6. 存储模型

```ts
const books = new Map<string, ParsedBook>();
```

`storeBook()` 生成随机 UUID，`getBook()` 按 ID 读取。没有磁盘持久化、TTL、容量限制、用户隔离或重启恢复。上传后的原 EPUB 被删除，解析正文仅驻留服务进程内存。

这意味着：

- 服务重启必须重新上传；
- 多实例无法共享 bookId；
- 长书/多书可能耗尽内存；
- 暴露公网时 bookId 是唯一访问门槛；
- 没有 source hash，无法判断重新上传是否为同一版本。

## 7. 阅读进度语义

虽然 README 强调章节级进度，实际聊天 API 接收的是：

```ts
progressPercent: number // 0..100
```

`getTextUpToPercent()` 按全书累计字符数计算目标位置，完整纳入之前章节，并对当前章节用 `text.slice(0, charsNeeded)` 做章内截断。因此它实际上是“全书字符百分比”边界，而不是稳定章节 ID。

优点是粒度细；缺点是文本规范化变化会使同一百分比对应不同语义位置，且可能在句子中间截断。

## 8. Context 构建策略

核心接口：

```ts
getTextUpToPercent(book: ParsedBook, percent: number): string
buildBookContext(book: ParsedBook, percent: number): string
```

常规情况把所有已读原文按章节用分隔符拼接。最大上下文估算为 1,000,000 tokens × 4 chars。

超长时采用 Tier 2：

```text
前 20% 字符原文
+ 中间经过的章节标题/字符数列表
+ 最近 60% 字符原文
```

因此 README 所说“无 RAG、全部已读原文”只对未超阈值情况成立。超长后中间内容不是语义摘要，只是章节目录，会显著降低前文回忆能力；而且预留的 20% 空间并未全部使用。

## 9. Chat 接口

### `POST /api/chat`

请求 schema：

```json
{
  "messages": [],
  "bookId": "uuid",
  "progressPercent": 37.5
}
```

约束：bookId 非空，percent 为 0..100。messages 仅校验为 unknown array，之后交给 Vercel AI SDK 转换。

服务端流程：

1. 查找内存中的 ParsedBook；
2. 按 progressPercent 构建已读原文；
3. 生成两条 system message；
4. 转换 UI messages；
5. 简化历史 assistant content，只保留 text parts；
6. `streamText()` 调用模型；
7. 通过 UI message stream 返回 token 和 cost metadata。

404 表示 book 已丢失，需要重新上传；400 表示请求不合法；未配置 OpenRouter key 时返回 500。

## 10. 模型 Provider 接口

默认：

```text
MODEL_ID=x-ai/grok-4.3
OPENROUTER_API_KEY=...
```

本地模式：

```text
LLAMA_SERVER_URL=http://localhost:8080/v1
LLAMA_MODEL_ID=...
```

设置 `LLAMA_SERVER_URL` 后本地模式优先。为兼容部分 Qwen prompt template，代码会合并连续 system messages；为兼容 xAI，会从 assistant 历史的多段 content 中只保留 text。

## 11. 防剧透机制

机制由两层组成：

- 数据边界：只发送进度之前的原文；
- 行为约束：system prompt 禁止透露、暗示、确认或否认未来内容，允许只依据已读证据讨论猜测。

第一层比“把全书放进去再要求不要剧透”可靠得多。不过模型仍可能从预训练知识记起后文，所以不能称为形式化保证。StoryPal 的检索层硬过滤 + 证据回答可以进一步降低风险。

## 12. Token 与成本可观测性

响应 metadata：

```ts
interface ChatMessageMetadata {
  usage?: {
    inputTokens: number;
    outputTokens: number;
    totalTokens: number;
    cachedTokens?: number;
  };
  cost?: number;
}
```

OpenRouter 优先使用 provider 返回的实际 cache-adjusted cost；缺失时按模型价格估算。本地模型不查询价格。稳定书籍前缀有机会命中 provider prompt cache。

## 13. 与 StoryPal 的比较

| 能力 | Paige | StoryPal 当前方向 |
| --- | --- | --- |
| 输入 | EPUB | 规范化作品与未来多媒体 |
| 阅读位置 | 字符百分比 | chapter/location → unit order |
| 防剧透 | 截断全文 + prompt | `max_seen_order` 检索硬过滤 |
| 内容获取 | 全文上下文 | FTS5/LanceDB 检索 Evidence |
| StoryMemory | 无 | JSONL 事实源 + 可重建索引 |
| 证据 | 原文整体可见 | 单元 provenance |
| 持久化 | 无 | 本地文件/数据库 |

Paige 的产品闭环更简洁；StoryPal 的工程路径更适合长篇、多作品和跨会话使用。

## 14. 可融入 StoryPal 的接口

建议把用户友好位置与内部 order 明确拆开：

```python
class ReadingProgress:
    def get_locations(self, work_id: str) -> list[dict]: ...

    def resolve_location(
        self,
        work_id: str,
        *,
        chapter_id: str,
        percent_in_chapter: float | None = None,
    ) -> dict:
        """返回 current_anchor、max_seen_order、可能的 partial-unit boundary。"""

    def update(self, session_id: str, work_id: str,
               current_anchor: int, max_seen_order: int) -> dict: ...
```

建议公开位置：

```json
{
  "chapter_id": "ch-03",
  "title": "第三章",
  "start_order": 21,
  "end_order": 35,
  "source_version": "sha256"
}
```

## 15. 推荐的混合 Context 策略

StoryPal 可保留 Paige 的“连续原文”优点，但只用于局部：

```text
最近阅读位置附近的连续原文
+ 针对问题检索的较早 Evidence
+ 用户 recent context / notes
```

这样既保留当前段落语气、细节和代词，又避免把整本已读内容反复发送。

## 16. 测试与 Bad Cases

Paige 已测试 EPUB parser、context slicing、format 和 pricing。StoryPal 应补充：

- 0%、100%、章节边界和章中进度；
- 中文字符/emoji/换行对位置映射的影响；
- 解析失败章节必须返回 warning；
- 更新源文本后旧 progress 的迁移；
- 用户直接询问未来、正确猜测、错误猜测时均不确认；
- 模型依据训练知识泄漏后文；
- 同一对话中进度前进后可谈新内容；
- 回看前文时 current_anchor 回退但 max_seen_order 不回退。

## 17. 推荐开发顺序

### Phase 1：位置接口

完成 chapter/location 到 story unit order 的稳定映射。

### Phase 2：局部连续原文

在 `get_story_evidence` 之外提供当前 anchor 附近原文窗口。

### Phase 3：UI 边界

让用户通过章节标题和章内进度设置位置，并显示当前无剧透范围。

### Phase 4：安全评估

建立专门 spoiler red-team cases，不只检查检索结果，还检查最终回答。

## 18. Definition of Done

- [ ] 用户不需要知道 unit ID；
- [ ] 阅读位置可稳定映射到 order；
- [ ] 检索在模型调用前硬过滤未来内容；
- [ ] 支持当前位置附近连续原文；
- [ ] 解析遗漏和边界近似会显式提示；
- [ ] source version 改变时能检测旧进度；
- [ ] 预测讨论不确认或否认后文；
- [ ] 有跨模型 spoiler eval。

## 19. 来源与许可证

- 官方仓库：https://github.com/derekmpeterson/paige
- 许可证：MIT。
- 本文依据 README、`lib/epub-parser.ts`、`lib/book-context.ts`、`lib/book-store.ts`、`lib/system-prompt.ts` 和两个 API route 核查。
