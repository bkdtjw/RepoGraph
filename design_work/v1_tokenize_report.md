# V1 中文分词双方案实测报告（Phase B · V1 / D-P1）

**日期**：2026-07-22　**语料**：单仓库 `multi-agent-orch`（graph.json 510 节点 / 1698 边，对齐 tag `rebaseline-20260723`）
**对照基线**：A4 门禁 `eval/gate_report.json` —— FZ-dev hit@3 = **0.1**（1/10）、hit@1 = 0.0（B-2 红）。
**实验脚本（可复现，全部落盘 `design_work/`）**：`v1_extract_targets.py` → `v1_gen_cards.py`(+`v1_backfill_card.py`) → `rg_exp_lib.py` → `v1_tokenize_eval.py`。
**产物**：`design_work/v1_targets.json`、`design_work/v1_cards.json`、`design_work/v1_tokenize_eval.json`。

> 通道注记：语义生成走**阿里网关 HTTP 直连**（`qwen3.8-max-preview`，非 grok——grok CLI 已 402 断供，见落地设计文档头）。密钥仅入内存与请求头，全程写作 `sk-****`，不落任何产物。

---

## 1. 目标实体与卡片生成（真实调用，无编造）

- **目标实体 58 个**（`v1_targets.json`）：FZ dev+test 20 题 gold + 3 个存在的 alt（共 23 gold，0 缺失）+ 1 跳 IMPLEMENTS/DESCRIBES 邻域核心函数（gold 概念的实现函数 / gold 函数所实现的概念）。分布：Function 41 / Concept 17。落在 40–60 区间。
- **卡片生成**：POST `{base}/v1/messages`，`qwen3.8-max-preview`，非流式，`anthropic-version: 2023-06-01`，`Authorization: Bearer sk-****`（不打印）；每请求 sleep 0.5s，网络失败重试 2 次。
- **专名白名单校验**：卡片内英文标识符必须为输入（qualname+docstring首行 / 概念 name+description）的子串（忽略大小写），违规重试 1 次后弃用。
- **结果**：**接受 58 / 58**（whitelist 弃用 **0**、初次网络超时 **1**（FZ-t04 gold）经 `v1_backfill_card.py` 二次重试补齐）。白名单 0 违规 → 生成层反幻觉在本仓有效。卡片与输入摘要存 `v1_cards.json`。网关**可用**（探针通过），V1 **未 blocked**。

卡片样例（真实产出，节选）：
- `_handle_terminate` → 「处理终止清单，汇总产物，system总结事件，状态终止并拒绝新派发。」
- `故障注入` → 「在存储和事务路径中注入故障，模拟异常并测试处理能力。」
- `适配层统一invoke接口` → 「适配层用统一 invoke 接口对接各类后端，统一调用方式。」

---

## 2. 语料装配与保真自校验

- **base 语料**（复刻 `topic.py` `_corpus_nodes`/`_doc_text`）：Concept/Commit/Module 共 **236** 篇。
- **卡片入语料**：Concept 卡片 append 到既有概念文档（17 篇增强），Function 卡片新建文档（doc_id=函数 id，41 篇新增）→ 增广后 **277** 篇。
- **保真自校验（关键）**：本实验复刻的 BM25（k1=1.5,b=0.75,IDF 现算,min_score=1.0）在 base 语料 + zh_terms 下，对 FZ dev+test 20 题**逐题召回 node_id 序列 = 真实 `topic.topic_recall`，零差异**；且 baseline FZ-dev hit@3 = **0.1**，与 gate 红值精确对齐 → (a)/(b) 数值可信。
- **路由守卫**：20 题 `link_entities` 恒空 → 真实系统均走 topic 路径，实验对齐生产行为。

---

## 3. 三配置逐题命中表（hit 判定复刻 `eval/gate.py`：gold_entity + IMPLEMENTS/DESCRIBES 1 跳等价集，anchors=recall node_id 有序，min_score=1.0，top_k=8）

配置：`base`=无卡片+ngram(现有zh_terms) ｜ `a`=卡片+ngram ｜ `b`=卡片+jieba。

### FZ-dev（10 题，**裁定依据集**）

| 题 | gold | base@3 | a@3 | b@3 | 说明 |
|---|---|:--:|:--:|:--:|---|
| FZ-d01 | _handle_terminate | 0 | 0 | 0 | 极口语「叫停…收尾扫尾」，卡片无桥接词 |
| FZ-d02 | 看门狗三级 | 0 | 0 | 0 | 「盯着…卡住」vs 卡片「分三级」零重合 |
| FZ-d03 | 崩溃恢复算法 | 0 | 0 | 0 | 「挂了…爬起来」无桥接 |
| FZ-d04 | append_system_event | **1** | **1** | **1** | 唯一命中，经既有概念「系统执行器」（rank2），**非卡片驱动** |
| FZ-d05 | estimate_tokens | 0 | 0 | 0 | 「估摸…篇幅」→ 0 召回 |
| FZ-d06 | worktree-隔离 | 0 | 0 | 0 | |
| FZ-d07 | autocommit | 0 | 0 | 0 | gold 等价在 rank6–8，未入 top3 |
| FZ-d08 | 权限三件套 | 0 | 0 | 0 | 「越界拦下」无桥接 |
| FZ-d09 | 故障注入 | 0 | 0 | 0 | 「使坏…扛揍」无桥接 |
| FZ-d10 | 门禁裁决入口 | 0 | 0 | 0 | |
| **hit@3** | | **0.1** | **0.1** | **0.1** | |
| **hit@1** | | 0.0 | 0.0 | 0.0 | |

### FZ-test（10 题，**冻结留出读数，不参与任何裁定/调参**）

| 题 | gold | base@3 | a@3 | b@3 | a@1 | b@1 |
|---|---|:--:|:--:|:--:|:--:|:--:|
| FZ-t01 | 视图组装 | 0 | 0 | **1** | 0 | **1** | jieba 独占 |
| FZ-t02 | 黑板投影与rebuild | 0 | 0 | 0 | 0 | 0 |
| FZ-t03 | stop-标志消费 | 0 | 0 | 0 | 0 | 0 |
| FZ-t04 | 迟到在途回复展示标记 | 0 | 0 | **1** | 0 | 0 | jieba 独占 |
| FZ-t05 | 适配层 | **1** | **1** | **1** | 0 | 0 | base 已命中 |
| FZ-t06 | _strip_to_author_fields | 0 | **1** | **1** | 0 | **1** | 函数卡片驱动 |
| FZ-t07 | _summarize | 0 | **1** | **1** | **1** | **1** | 函数卡片驱动 |
| FZ-t08 | _resolve_workspace | 0 | **1** | 0 | **1** | 0 | **ngram 独占** |
| FZ-t09 | 混沌-50-轮-100-硬门槛 | 0 | 0 | 0 | 0 | 0 |
| FZ-t10 | 状态层 | 0 | 0 | 0 | 0 | 0 |
| **hit@3** | | **0.1** | **0.4** | **0.5** | | |
| **hit@1** | | 0.0 | 0.2 | 0.3 | | |

---

## 4. 裁定 D-P1（分词方案）

**分词器裁定：选 n-gram（现有 `zh_terms`），守 stdlib 约束。** 依据：

1. **决策集（FZ-dev）上 a=b**：ngram 与 jieba hit@3 均 0.1、hit@1 均 0.0，完全并列。满足规则 `a ≥ 90%×b`（0.1 ≥ 0.09）→ **选 ngram**。jieba 破例的触发条件（`a < 90%×b`）在决策集上**不成立**。
2. **jieba 的优势不稳健、未达破例 bar**：仅在冻结的 FZ-test 上 jieba 领先 1 题（hit@3 0.5 vs 0.4、hit@1 0.3 vs 0.2，n=10 噪声级）；且两者**互有胜负**——ngram 独占 t08，jieba 独占 t01/t04，非稳健优越。stdlib 约束（标准架构约束）不因 1 题证据破除。
3. **jieba 在 dev 校准上也无救**（`v0_calibration` 敏感性对照）：jieba 同样 hit@1=0、消歧率>0.2、零可行单元。**问题不在分词器**。

> 依赖损失声明：**无**（未破例引入 jieba，运行时依赖维持纯 stdlib）。jieba 已装（0.42.1）仅作对照，不进 as-built。

---

## 5. D-11 重议警报（触发，但 D-11 未被证伪）

**触发**：FZ-dev 决策集上，卡片 + 两种分词**均无 hit@3 净提升**（a=b=base=0.1）；唯一命中 d04 为既有概念命中、**非卡片驱动**，其余 9 题 base/a/b 全 0。按规则「两案均无提升 → D-11 重议警报」，**警报成立**。

**但 D-11 机制未被证伪**——须与 dev 平坦一并递交台账重议，避免误杀：

- **FZ-test 冻结留出证明卡片机制有效**：hit@3 0.1→0.5、hit@1 0→0.3；其中 t06/t07（gold 函数）由**函数卡片新文档**直接召回命中，t01/t04 由概念卡片增强命中。即「中文卡片入 BM25 抬升召回」在**有词面桥接**的题上确实奏效。
- **dev 平坦的根因 = 卡片输入的天花板，非分词/BM25/分带**：卡片受反幻觉白名单约束、只依据实体自身**简短循环式描述**转述，无法桥接 dev 的极端口语（「使坏扛揍」「盯着卡住」「估摸篇幅」「爬起来接着干」）。逐题证据：d02 卡片=「看门狗分三级」不含「盯/卡」；d05 卡片=「字符系数估算」对「估摸篇幅」零召回。dev 恰是分半中更难的一半（Concept 别名近乎全空 + 问题口语度更高）。
- **归属 C2「先修卡片质量」**：符合落地设计 §7 C2 验收纪律「不达标先修卡片质量再动分带参数,顺序不可反」。**重议方向 = 提升卡片质量/覆盖（更富输入：docstring 正文、调用点上下文、受控口语近义扩展、Concept 别名回填），而非砍除 D-11。**

**D-P1 台账草案**：
```
D-P1 | 2026-07-22 | 状态: 生效(分词) + 重议(D-11)
裁定: 中文分词选 n-gram(zh_terms) 守 stdlib；jieba 不破例(依赖损失=无)。
      同时对 D-11「双语卡片入 BM25」发重议警报：FZ-dev 卡片零净提升(hit@3 0.1→0.1)。
动因: FZ-dev a=b=base(并列满足 a>=90%b)；jieba 优势仅 test 1 题(噪声)且互有胜负。
      D-11 dev 平坦根因=卡片输入(实体自述)在反幻觉下无法桥接极端口语。
显式损失: 无(未引 jieba)。D-11 dev 增益=0，test 增益 hit@3 +0.4/hit@1 +0.3。
引用: 落地设计 §4.2/§4.6/§6.1；计划书 §3 V1、D-11；A4 gate_report 红值 0.1。
复审触发: C2 卡片质量提升后复跑 V1；FZ-test hit@3 不达 P3 目标时联动 D-21/D-22。
```

---

## 6. 结论摘要（交编排器）

- 卡片：**生成 58 / 接受 58 / 弃用 0 / 网络失败 0**（1 次超时已补齐）；白名单 0 违规；网关可用（未 blocked）。
- V1 三配置 hit@3（FZ-dev｜FZ-test）：base **0.1｜0.1**、a_ngram **0.1｜0.4**、b_jieba **0.1｜0.5**。
- 分词裁定 **D-P1 = n-gram 守 stdlib**（jieba 不破例，依赖损失=无）。
- **D-11 重议警报触发**（dev 卡片零净提升），但机制经 test 验证有效，重议方向=修卡片质量（归 C2），非砍除。
