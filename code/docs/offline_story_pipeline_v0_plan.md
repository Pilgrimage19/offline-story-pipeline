# 剧情陪伴 AI：离线 Story Pipeline V0 方案（初版）

> **状态**：V0 实施中，Story Memory Adapter 契约已冻结；切分与检索排序仍以评测结果迭代。
> **最近更新（2026-09-04）**：在完成基础 chunk 后加入本地文本向量 side index；检索默认采用“向量优先、FTS/扫描降级”，为 Chatbot 基本接入提供语义召回。
> **范围**：离线部分。当前以《流浪地球》单篇闭环为目标，跑通后泛化到《赡养人类》《地火》。
> **配套参考**：`offline_story_pipeline_engineering (1).md`、`offline_story_pipeline_engineering(1).md`、`online_chatbot_engineering(1).md`（三篇均在仓库根目录）。

---

## 1. 背景与职责边界

### 1.1 项目目标

做「剧情陪伴 AI」而非「小说 QA 知识库」：用户第一次读/看/玩故事时，AI 能基于**真实剧情内容**陪他回忆、吐槽、讨论。

- 可定位：知道用户在指哪个具体剧情；
- 可检索：能按模糊自然语言召回；
- 可回原文：检索结果必须能回溯真实证据（provenance）；
- 有稳定顺序：支持阅读进度与防剧透（`order`）；
- 不依赖模型参数里的模糊记忆空聊。

### 1.2 离线侧负责 / 不负责

**负责**：原文导入规范化 → 切分对齐 → 表示抽取 → 本地 Story Memory 存储与索引 → 统一 `StoryMemory` Adapter → 三篇作品验证 → `STORY_MEMORY_DESIGN.md`。

**不负责**：Query Analyzer / Router、Agent 工具选择、Conversation Memory、用户观点抽取、回答生成、Web/MCP、多 Agent、实时 VLM、强行知识图谱。

### 1.3 与 MoEye 的关系（重要澄清）

MoEye 是**多模态游戏角色识别项目**（CLIP 图像编码 + ChromaDB + VLM 判定），**不是小说处理代码**，全仓库无任何 chapter/切分逻辑（已全文检索确认）。

可借鉴的只是它的方法论：

1. **先粗召回、再精判定**：CLIP 向量召回 top-3 候选角色 → VLM 只在候选内做最终判定。对应 Story 侧「检索召回 → LLM/规则在候选内定位」；
2. **按实体组织参考证据**：MoEye 一个角色 = 一个文件夹（参考图 + T.png + 信息.json），对应「剧情单元 + 原文证据 + 薄 metadata」的组织思路；
3. **薄 schema**：metadata 只有 `character_name`，印证「不是字段越多越好」；
4. **ground-truth 评估闭环**：拿 T.png 当标准答案算准确率；Story 侧对应「用户问题 → 期望命中的 story unit / 原文段」标注集。

---

## 2. 现状盘点

| 已有 | 缺 |
|---|---|
| `raw_text/` 三篇原文（GB18030 编码，需转码） | 整个管线代码（转码/切分/抽取/索引） |
| MoEye 代码（图像识别，仅参考思路） | 存储层 + `StoryMemory` Adapter |
| 两份离线工程文档（要求已明确） | 检索验证 + `STORY_MEMORY_DESIGN.md` |

---

## 3. 《流浪地球》文本实测（已读原文核实）

| 项 | 值 |
|---|---|
| 全文 | 540 行 / 24,393 字 / 275 个非空段（每段一行，空行分隔） |
| 段落长度 | 中位 57 字、p90 179 字、最长 621 字 |
| 编码 | GB18030（`gb18030` 可无损解码） |
| 结构 | 4 个天然大节（见下表） |
| 高频实体 | 地球166 / 太阳104 / 发动机60 / 木星28 / 加代子27 / 小星20 / 联合政府19 / 妈妈12 / 爸爸12 / 氦闪11 / 岩浆9 / 地球派7 / 飞船派6 / 爷爷4 / 叛军4 |

### 3.1 四大节

| 节 | 行范围 | 字数 | 段数 | 内容锚点 |
|---|---|---|---|---|
| 上篇 刹车时代 | 3–135 | 6,564 | 66 | 发动机 / 爷爷之死 / 地球派与飞船派之争 / 启航(127) |
| 中篇 逃逸时代 | 136–413 | 12,007 | 138 | 木星引力弹弓 / 大灾难(246) / 反物质炸弹(314) / 加代子离开 |
| 下篇 叛乱 | 414–511 | 4,904 | 49 | 叛军审判 / 氦闪爆发(494) / 还有人活着(510) |
| 流浪时代 | 512–540 | 885 | 20 | 结尾诗与尾声 |

### 3.2 文本特点 → 对设计的含义

- **对话极多**（段落中位数 57 字）：直接按段做 unit 会碎成 275 个，需按场景聚合；
- **长叙述段推进叙事**（p90 179 字）：可作场景聚合锚点；
- **时间跨度大、整体顺序推进**：「前面是不是提过 XX」类跨节问题天然多，是前文检索的重点验证场景；
- 叙述体第一人称回顾，结尾「流浪时代」为诗歌 + 尾声，切分时作为独立 chapter 处理。

---

## 4. 整体管线设计

### 4.1 分层（保留中间产物，可局部重跑）

```text
Raw Source → Normalization → Segmentation/Alignment → Extraction → Validation → Storage/Index
```

### 4.2 数据目录（以《流浪地球》为例）

```text
data/wandering_earth/
  00_raw/          原样 GB18030 文件 + sha256（source_version）
  01_normalized/   UTF-8、去空行、每段一行；保留原行号作 provenance 锚
  02_segmented/    units.jsonl：{unit_id, order, chapter, start_line, end_line, text}
  03_extracted/    units_extracted.jsonl：+ summary / characters / locations / key_terms
  04_validated/    人工抽检记录
  05_index/        SQLite FTS5（bm25 over text+summary；metadata 列 work_id/chapter/order）
                   vectors.db（文本 embedding；可重建 side index）
```

### 4.3 工程要求

- 每个阶段可用 `pipeline.py --work wandering_earth --stage xxx` 单独执行；
- 高成本 LLM 调用按「抽取器 + 模型 + 端点 + 生成参数 + prompt/state schema + input/state hash」隔离缓存，支持 force rerun；降级结果不进入正常缓存；
- 产物记录版本：`source_version / pipeline_version / schema_version / prompt_version / embedding_model`；
- 批处理容错：单请求失败 / JSON parse 失败 / timeout / 断点 resume / failed units retry，不因一处失败重跑全书。

---

## 5. 切分方案（初版假设，未冻结）

### 5.1 两级结构

```text
chapter    = 4 个（上篇/中篇/下篇/流浪时代），天然边界，直接采用
scene unit = 切分基本单位
```

**scene unit 初版规则（候选方案 A）**：

> 以长叙述段（>80 字）为锚点开新 unit，把其后紧邻的短对话段并入，直到下一个长叙述段。

预估全篇 60–90 个 unit，每 unit 100–400 字。

**候选方案 B**：若检索实验表明细粒度更优，可退化为按自然段聚合为更小 unit（甚至段落级）。

> ⚠️ 按工程文档要求**不锁死**：scene 聚合规则只是候选，跑检索后若 B 更优则换 B。当前 `unit_id` 由 order 生成，规则切换会导致后续 ID 漂移；稳定 ID/旧 ID 映射是后续明确待办，不能把当前 ID 当永久引用。

### 5.2 ID 与顺序

```text
story_unit_id = "we-0001"（全局序号）
order         = 全局顺序号（同 unit 序）
映射表记录：unit_id → chapter + 段范围（start_line/end_line）
```

---

## 6. 抽取方案（03 阶段）—— 上下文结合的「状态流」设计（v0.2 已定方向）

> **设计修正**：不是"逐 unit 独立抽取 + BM25"。实际运行时 chunk 随用户阅读进度释放（增量 Story Reader），新 chunk 的理解必须结合相关前文 —— 抽取被设计为**顺序状态机**：
>
> ```
> process(unit_k, state_{k-1})  ->  (unit_k 的理解, state_k)
> ```
>
> 离线 V0 = 按 order 顺序把整本跑一遍；在线增量 = 同一状态机，输入换成随读释放的流。
> 两者产物逐位一致（见 6.4 前向一致性验证），即"输入来源可替换"的落地点。

### 6.1 每个 unit 的 LLM 输入

- 当前 chunk 原文 + 章节名；
- 前序状态 `state_{k-1}`（受控大小的表示，见 6.3）；
- 最近 1~2 个 chunk 原文（辅助指代消解）。

### 6.2 每个 unit 的产物字段

| 字段 | 说明 |
|---|---|
| `summary` | 1–2 句，这一段发生了什么（基于原文 + 前文状态） |
| `characters / locations / key_terms` | 本段出现实体（增量），并归并进实体表 |
| `entity_updates` | 实体状态变化（如：刘欣 → 正在矿场；加代子 → 离开） |
| `plotline_updates` | 事件线 开启/推进/闭合（记录涉及 order） |
| `context_refs` | 本段依赖的前序 unit_id / 实体（"前面是不是提过"沿链定位） |
| `state_delta` | 本次对状态的增量修改 |
| `state_snapshot` | 处理完本段后的完整 story state（= state_k，**每个 unit 都存**） |

约束：

- 模型生成的结构化信息与原文证据逻辑分开，禁止只存摘要丢原文；
- 原行号 provenance 不变，快照可回溯到触发它的原文行。

### 6.3 Story State schema（草案，JSON）

```json
{
  "characters": [
    {"name": "刘欣", "aliases": [], "status": "当前处境一句话",
     "first_seen_order": 12, "last_seen_order": 45}
  ],
  "objects": [
    {"name": "地球发动机", "facts": ["共一万二千台"], "first_seen_order": 1}
  ],
  "plotlines": [
    {"title": "飞船派 vs 地球派", "status": "closed",
     "orders": [19, 20, 21], "outcome": "……"}
  ],
  "recent": ["最近 3~5 个事件的一句话摘要（供指代消解）"],
  "backdrop": "更早历史的压缩摘要（定期归档）"}
}
```

状态对象大小受控：近期事件详细保留，更早历史定期归档进 backdrop 摘要 ——
否则每 unit 快照的成本随故事线性膨胀。快照 = 序列化 `state_k`，KB 级存储；
真正的成本在 LLM 侧（state 每步进 prompt），**压缩/归档策略是状态设计的关键点**。

### 6.4 缓存 / 重跑 / 一致性

- cache 命名空间 = `(extractor, model, endpoint, 生成参数, prompt_version, state_schema)`；
  命名空间内键 = `(prompt_version, unit.text, 前序 state 指纹, 最近原文)`：配置与前序均不变才命中；
  修改某 unit 的抽取会导致其后**整条链缓存失效连锁重抽**（状态流的固有代价）；
- 每 chapter 结束落一个 state 检查点文件，便于回滚 / 局部重跑；
- **前向一致性验证**：只跑前 p 个 chunk 的增量结果，必须与整本跑出的前 p 个结果一致。
  通过 ⇒ 离线整本处理等价于未来在线随读增量，可平滑迁移。

### 6.5 容错

- 单 unit 调用/解析失败先重试一次；仍失败才降级产出（summary 兜底），**不更新 state**，不污染后续链；记录 degraded 标记；
- 降级结果不写正常缓存，避免临时网络或模型格式错误长期污染数据；
- 章节压缩失败保留 `backdrop_buffer`，不丢弃尚未归档的事件。
- 出现 degraded 时人工抽检清单会标出，提示重跑该点。

### 6.6 对 Chatbot 侧的意义（预告）

- `state_at(order)` O(1) 可得 → "他读到这里时知道什么 / 不知道什么"；
- "前面是不是提过 X" 沿实体 `first_seen_order` / `context_refs` 定位，不再只靠 BM25 碰；
- 检索仍以 `max_order` 为硬边界，防剧透语义不变。

---

## 7. 存储与索引选型（V0 当前决策）

### 7.1 方案

```text
Source of truth = JSONL（03 阶段产物，可人工核验、可调试、可 diff）
稀疏查询层     = SQLite + FTS5（原文 + summary 双字段 BM25）
语义查询层     = 本地文本 embedding + SQLite vectors.db
过滤字段       = work_id / chapter / order
```

`search()` 当前逻辑：

```text
auto: 向量索引可用且与 JSONL 哈希一致
        → order/chapter 强过滤 → cosine 排序 → Evidence
      否则
        → FTS5 BM25 → Python 扫描降级 → Evidence
```

可通过 `retrieval=vector|fts|auto` 显式选择或比较后端，Chatbot 默认使用 `auto`。显式 `vector` 模式在索引陈旧或模型不可用时直接报错，避免把降级结果误认为向量结果；只有 `auto` 执行透明回退。

### 7.2 为什么选它（对比）

| 候选 | 结论 |
|---|---|
| 纯 JSON/JSONL | 作为 source of truth ✓；作为查询层不够（需自实现检索/过滤） |
| SQLite + FTS5 | 保留。专有名词和原文精确措辞有效，但实测自然语言因果问题相关性不足 |
| ChromaDB / ANN 服务 | 当前不上。三篇短篇共数百 unit，没有必要增加独立向量服务和 ANN 复杂度 |
| 本地 embedding + SQLite 精确扫描 | **V0 采用**。实现语义召回，数百向量直接 cosine 全量扫描足够，易调试、易迁移 |
| 混合形态 | JSONL truth + FTS side index + dense side index；Adapter 隔离底层差异 |

### 7.3 向量索引规范

- 默认模型：`BAAI/bge-small-zh-v1.5`，可由 `STORYPIPE_EMBEDDING_MODEL` 或 CLI `--embedding-model` 覆盖，并支持直接加载本地模型目录；当前开发机实测优先使用已有的 `BAAI/bge-m3`；
- 文档向量内容：章节 + summary + characters + locations + key_terms + 原文；
- bge v1.5 query 使用中文检索指令前缀，文档不加指令；bge-m3 不添加指令前缀；
- embedding 归一化后存为 float32，查询用余弦相似度；
- `vectors.db` 记录模型、维度、索引版本、source JSONL SHA-256；source 变化时拒绝使用陈旧索引；
- 模型首次下载后可以离线运行；向量依赖为可选依赖，不影响核心规则管线。

### 7.4 Provenance

每个 unit 记录 `start_line / end_line` + `raw_text`，检索结果**必然可回原文行号**。

---

## 8. StoryMemory Adapter 契约（与 Chatbot 侧冻结）

```python
class StoryMemory:
    def list_works(self) -> list[dict]: ...
    def search(
        self, work_id: str, query: str, *,
        max_order: int | None = None,
        top_k: int = 8,
        filters: dict | None = None,
    ) -> list[dict]: ...
    def get_unit(self, work_id: str, unit_id: str) -> dict | None: ...
```

Adapter 构造器新增可选 `retrieval="auto"`，不改变上述 `search()` 契约。默认优先向量；索引或本地模型不可用时透明回退。

返回统一 Evidence：

```json
{
  "work_id": "wandering_earth",
  "unit_id": "we-0001",
  "order": 1,
  "raw_text": "……原文证据……",
  "summary": "……",
  "score": 0.0,
  "metadata": {"chapter": "上篇 刹车时代", "start_line": 5, "end_line": 11}
}
```

> Chatbot 只依赖 Adapter，不读底层表；将来换存储（增量/实时）不重写 Chatbot。

---

## 9. 检索与验证

### 9.1 《流浪地球》验证集（覆盖工程文档 13 节类型）

| 类型 | 示例 query | 期望命中 |
|---|---|---|
| 事实 | 为什么人类要建造地球发动机？ | 刹车时代早期（发动机一万二千台） |
| 事实 | 发动机共有多少台？ | 同上（1.2 万台） |
| 人物动机 | 爸爸为什么要离开？/ 他为什么去水星？ | 逃逸时代对应段 |
| 人物动机 | 加代子后来为什么离开？ | 逃逸时代 |
| 前文提及 | 前面是不是提过飞船派和地球派？ | 刹车时代末尾（跨节检索，重点验证） |
| 前文提及 | 反物质炸弹前面出现过吗？ | 逃逸时代 |
| 模糊指代 | 他之前不是还反对吗？ | 叛军首领身份反转（离线给对 evidence，指代消解留给 Chatbot） |
| 防剧透 | 结局怎么样了？ | `max_order` 设在中篇中段 → 必须不返回下篇叛乱 |

### 9.2 人工核验（每篇）

- 随机抽 10 个结果：能否回到正确原文行号；summary 与原文一致；
- 顺序：unit 顺序无明显前后倒置/缺失；
- progress filter：中间 order 下不返回后续内容；
- work isolation：同一 query 在三篇中查询不串（三篇齐后统一测）。

### 9.3 Bad case 优先

记录失败案例（找错上下文 / 模糊指代 miss / summary 过度压缩 / spoiler 漏出 / 跨作品串扰），迭代围绕 bad case 而非平均分。

---

## 10. 开发顺序与 DoD

### 10.1 顺序

```text
Phase 0  准备：三篇转码规范化 + 人工通读核验
Phase 1  《流浪地球》单篇闭环：raw → index → search CLI → 验证集
Phase 1.5 向量 MVP：chunk → embedding → vector search → Chatbot Adapter
Phase 2  三篇泛化：《赡养人类》《地火》，确认无单篇写死
Phase 3  冻结设计：检索对比实验后定稿 → STORY_MEMORY_DESIGN.md
Phase 4  与 Chatbot 联调（Adapter 契约已冻结）
```

### 10.2 《流浪地球》本篇 DoD

- [ ] raw → index 全链路可重复执行；
- [ ] vector-index 可重复构建，source 变化时能识别陈旧索引；
- [ ] 检索可回到正确原文行号；
- [ ] `auto/vector/fts` 可对比，语义问题的向量召回优于当前 FTS bad case；
- [ ] 验证集 query 全部命中预期 unit（或记录 bad case 及原因）；
- [ ] `max_order` 防剧透过滤生效；
- [ ] 抽取字段人工抽检通过；
- [ ] 设计结论汇入最终 `STORY_MEMORY_DESIGN.md`。

---

## 11. 待定问题（暂不锁死）

- scene unit 聚合规则：方案 A（长叙述段锚点）vs 方案 B（段落级）——以检索实验定；
- `chapter / scene unit / retrieval chunk` 三者是否合一（一个可检索单元是否等于一个可展示/可定位单元）；
- dense retrieval side index 已加入；是否进一步做 FTS + dense 融合与 reranker，以标注评测集决定；
- 多作品隔离：独立 table vs 共享 table + `work_id` 过滤（V0 以简单稳定为主）；
- 未来增量（实时 Story Reader）迁移路径：Adapter 已预留替换空间，具体存储迁移留待设计文档讨论；
- 《赡养人类》《地火》的切分边界（待读原文后确认是否同样有明显分节）。
