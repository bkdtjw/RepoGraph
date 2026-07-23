# RepoGraph v0.3 · Phase D 评测报告（v2 · 数字冻结）

> **性质**：v0.3 检索/编排能力的定量收口。主叙事 = **两组对比**（组 A `BM25-only 无图谱` vs 组 B `图谱混合 build_repo_context`），单一自变量 = 图谱（D-R2）。所有数字来自真跑，原始请求/响应落盘可回溯、程序断言确定性可复现。
>
> **评测树（代码+图谱）**：`src/repograph` + `output/graph.json` @ `c971078`（510 节点 / 1698 边）——自 D1 提交 `7df38d4` 起**逐字未变**（`git diff 7df38d4 HEAD -- src output` 为空），本 Phase D 提交只新增评测产物/文档，不改 src/graph。
> **本报告提交 tag**：`phase-d-20260723`。
> **在线通道**：grok-4.5 中转站（Anthropic 兼容 `/v1/messages`；host/api_key 不入任何产物，见 D-N5）。
> **检索档位**：`semantic_mode=lexical`——S2 改写 / L2 LLM 需阿里网关，本轮**未消耗**（用户额度告急）；**所有召回/准确率数字为 lexical 下界，llm 档增益未计入**。
> **数字来源标注**：`d2_results.json`=在线两组评测；`gate_report.json`=48 题离线门禁（本轮 @HEAD 复跑）；`d3_f9.json`=F9 归因。

---

## 0. 结论（TL;DR）

图谱混合（组 B）在**需要图结构的任务上决定性胜出**、在**纯检索任务上诚实平局**、并**暴露一处真实路由缺陷**（非稻草人对照）：

- **L1（符号→模块定位）**：correct **0.25→0.65（Δ+0.40）**，c+p 0.40→0.75。
- **L2（反向调用闭包）**：correct **0.00→0.533（Δ+0.533）**，c+p 0.167→0.867（**Δ+0.70**）——纯 BM25 结构性为 0，影响面闭包是图谱独有能力。
- **L3（设计溯源）**：correct **0.90→0.50（Δ−0.40）**，c+p 1.00→0.90（Δ−0.10）——**诚实负结果**：L3 是「概念卡片即答案」的检索任务，纯 BM25 稳定命中；组 B 路由把部分「为什么…」问句过泛化为 meta→overview（L3-01），概览缺该概念的设计理由。**真实缺陷，如实记录。**
- **48 题**：L0 元问题（0.0→1.0）、AMB 消歧一致率（0.0→1.0）为图谱编排独有，基线恒 0；FZ 词面召回两组同分同源（图谱在纯检索上无增益，诚实平局）。
- **PP 前提纠正（组 B 端到端在线）**：correct **0.875（7/8）**、c+p 1.0、错误预设 0 顺答。
- **F9（无向量层代价，冻结 FZ-test）**：hit@3=0.8，2 失败题**全部「词面不可达」**、0 排序失利 ⇒ 向量层缺失净代价 = **2/10 = 0.20**。

---

## 1. 主表：主集三层 × 两组 · 答案准确率（在线裁判 grok-4.5）

主集 60 题（L1=20 / L2=30 / L3=10，D1 补建、gold 对真实图谱 0 失败）。每组每题：组内生成答案 → grok-4.5 裁判判 correct/partial/wrong。`c+p` = correct_or_partial。

| 层 | 任务 | 组A correct | 组B correct | **Δcorrect(B−A)** | 组A c+p | 组B c+p | Δc+p |
|---|---|---|---|---|---|---|---|
| **L1** | 符号/模块定位 | 0.25 (5/20) | 0.65 (13/20) | **+0.40** | 0.40 | 0.75 | +0.35 |
| **L2** | 反向调用闭包 | 0.00 (0/30) | 0.533 (16/30) | **+0.533** | 0.167 | 0.867 | **+0.70** |
| **L3** | 设计溯源 | 0.90 (9/10) | 0.50 (5/10) | **−0.40** | 1.00 | 0.90 | −0.10 |

> 来源：`eval/d2_results.json` → `segment2_answer_accuracy.main_by_layer` / `group_delta`；生成+裁判各 60 次/组，在线 grok-4.5，`eval/d2_runs/{gen,judge}_main_{A,B}.jsonl` 逐条可回溯。@评测树 `c971078` / tag `phase-d-20260723`。
> **L2 为 depth=2 下界**：组 B 用 `build_repo_context` 默认 `impact_depth=2`，而 L2 gold 闭包按 depth=3 生成（未调参、未改冻结 src）；depth=3 预期更高。
> **L3 诚实负结果**：见 §6.6。

---

## 2. 程序断言段（主集三层 × 两组 · 离线确定性）

不依赖 LLM 裁判的可复现硬指标：对每题的**检索上下文**（组 A=BM25 top-8 拼装；组 B=`build_repo_context`）程序化断言 gold 是否被召回。

| 层 | 断言定义 | 组A hit@min1 | 组B hit@min1 | 组A mean_recall | 组B mean_recall |
|---|---|---|---|---|---|
| **L1** | 上下文含 gold 模块路径（min_hit 1） | 0.60 (12/20) | **0.80 (16/20)** | 0.517 | **0.760** |
| **L2** | gold 反向调用闭包∩上下文（gold_n=6/题） | 0.267 (8/30) | **0.90 (27/30)** | 0.083 | **0.733** |
| **L3** | gold 来源(概念+DESCRIBES 提交)召回率 | 1.00 (10/10) | 0.90 (9/10) | 0.883 | 0.900 |

> 来源：`eval/d2_results.json` → `segment1_program_assertions.main_program_assertions`；明细 `eval/d2_runs/program_metrics.json`、上下文 `eval/d2_runs/ctx_main_{A,B}.jsonl`。离线确定性、无网关。
> 程序断言与在线裁判**同向**：L1/L2 组 B 大幅领先、L3 两组接近（程序层组 A 100% vs 组 B 90%，与裁判层 L3 组 A 反超同因——见 §6.6）。

---

## 3. 48 题全指标终值（两组）+ v0.1 失败基线 B-1/B-2/B-3 红→终态

48 题冻结集（L0 10 / FZ_dev 10 / FZ_test 10 / AMB 10 / PP 8）。组 B = `build_repo_context` 完整 v0.3；组 A = BM25-only 基线。

| 指标 | 组A | 组B | 说明 |
|---|---|---|---|
| L0 元问题通过率 | 0.0 (0/10) | **1.0 (10/10)** | 组 A 无 repo_card/overview 路由，元问题恒答不出 |
| FZ_dev hit@3 | 0.7 | 0.7 | 纯检索，两组同源同分（诚实平局） |
| FZ_test hit@3 | 0.8 | 0.8 | 同上（冻结留出集） |
| AMB 行为一致率 | 0.0 (0/10) | **1.0 (10/10)** | 组 A 无消歧协议，autopick/disambiguate 判定恒不一致 |
| PP 泄漏率 | 0.0 | 0.0 | 图中本无 redis/postgres 等技术名，两组均不泄漏 |
| 裸拒率 | 0 | 0 | 均不裸拒 |

> 来源：`eval/d2_results.json` → `segment1_program_assertions.set48_existing_assertions.{A,B}`；组 B 与独立门禁 `eval/gate_report.json`（@HEAD `c971078` 复跑）**逐项一致**（L0 1.0 / FZ_dev 0.7 / FZ_test 0.8 / AMB 1.0 / leak 0 / bare 0），两套 harness 交叉验证。
> **PP 说明**：离线 `PP_suspect_correction`（否定词+真值锚共现代理，组 A 0.5 / 组 B 0.0）是**弱代理非真指标**——真实 PP 纠正只在端到端在线测（组 B 0.875，§4）；组 A 无生成层前提闸门、无在线 PP 跑，故不以离线代理作组间 PP 结论。

### v0.1 失败基线（B-1/B-2/B-3）· 红 → 终态

三个 Phase A 锁定的 v0.1 已知失败（tag `rebaseline-20260723` 起 RED），追踪至终态：

| 锁定失败 | 描述 | v0.1/基线 | **终态 @HEAD** | 修复相 / 证据 |
|---|---|---|---|---|
| **B-1** | L0-02 口语元问题误路由 topic / 事实<3 | RED | **GREEN** | C1（meta 路由+repo_card）；L0-02 overview、整题通过；组 B L0 10/10 |
| **B-2** | FZ-dev hit@3 < 0.8 | RED | **RED（0.7，差 1 题）** | C2 富化卡片 0.1→0.7；剩 d06/d09/d10 未闭合（**如实红**，见 §5/D-11） |
| **B-3** | premise_flags 前提校验能力缺失 | RED | **GREEN** | C3（S7 闸门）；能力落地、PP 端到端纠正 0.875（§4） |

> 来源：`eval/gate_report.json` → `locked_failures`（@HEAD 复跑：B-1 is_red=false / B-2 is_red=true / B-3 is_red=false）。**B-2 终态仍红如实呈现，不粉饰**。

---

## 4. PP 前提校验 · 端到端在线（组 B）

8 题错误预设（Redis 锁/FastAPI/PostgreSQL/Docker-K8s/Celery/React/看门狗五级/混沌100轮），组 B 走 `build_repo_context` + S7 前提闸门 → 在线生成 → grok-4.5 裁判是否**主动纠正错误预设且不顺预设作答**：

| 指标 | 值 |
|---|---|
| correct（主动纠正） | **0.875 (7/8)** |
| c+p | **1.0 (8/8)** |
| wrong（顺预设作答） | **0** |
| error（调用失败） | 0 |

> 来源：`eval/d2_results.json` → `segment2_answer_accuracy.pp_correction_groupB`；生成 `eval/d2_runs/gen_pp_B.jsonl`、裁判 `eval/d2_runs/judge_pp_B.jsonl`。S7 前提闸门在线生效、错误预设 0 顺答。

---

## 5. F9 定量：无向量层的召回代价（冻结 FZ-test）

D-22 砍除向量层的定量代价，以最终配置在**冻结留出集 FZ-test（10 题）**上归因。方法（严格对齐真实 BM25 打分器）：`gold_equiv = gold + IMPLEMENTS/DESCRIBES 1 跳`（同 gate.judge_fz）；`topic_recall(min_score=0, top_k=∞)` 得全量排名——某文档「不在排名中」⇔「与查询词元零 n-gram 交集」⇔ BM25 **结构性不可达**。

| 子集 | hit@3 | 失败题 | 词面不可达 | 排序失利 | 其他 | 向量层净代价 |
|---|---|---|---|---|---|---|
| **FZ-test（冻结, n=10）** | 0.8 | 2（t03/t04） | **2** | 0 | 0 | **2/10 = 0.20** |
| FZ-dev（调参, n=10）交叉核验 | 0.7 | 3（d06/d09/d10） | 2 | 1 | 0 | 2/10 = 0.20 |

- **FZ-test 失败 100% 归因词面不可达、0 排序失利**：t03「喊了暂停」↔`stop 标志消费`、t04「打个记号」↔`标记`，gold 卡片虽经 C2 富化（`zh_aliases`：消费停止信号/晚到回复打时钟标…）仍与查询**零 2-gram 交集**（仅单字重叠不成词元）。BM25 无论重排/调阈都够不到，**只有语义近邻（embedding）能召回** ⇒ 这 2/10 即向量层缺失的净代价。
- **FZ-dev 交叉核验与 D-11 C2 归因逐题一致**：d09/d06=词面不可达；d10=排序失利（gold 实现 `apply_gate_decision` 命中「放行」但 **rank-4** 被 n-gram「关卡」灼热竞品挤出 top3）——独立 BM25 全量排名复现了 C2 手工归因的「rank-4」。
- **口径**：n=10，如实标注小样本；数字为 **lexical 下界**（S2 改写/L2 未计入）。

> 来源：`design_work/d3_f9.json`（`design_work/d3_f9.py` 可复现）；@评测树 `c971078` / tag `phase-d-20260723`。承 D-P4（本轮转生效）。

---

## 6. 口径注记（诚实性辩护）

### 6.1 lexical 下界
全程 `semantic_mode=lexical`。S2 改写（同义改述二次召回）与 L2 LLM 受限链接需阿里网关，本轮因用户额度告急**未消耗**。故 FZ hit@k、主集准确率、F9 均为 **lexical 档下界**，llm 档增益未计入——真实上线（含 S2/L2）预期更高，但本报告不据此推断，只报下界实测。

### 6.2 裁判自评偏置
生成与裁判**同模型 grok-4.5**，存在自评偏置。缓解论证：两组答案均由 grok-4.5 生成、grok-4.5 裁判，偏置**对两组同向作用**，故**组间相对差（Δ）有效**，绝对水平可能整体偏高/偏低但不影响 A/B 排序。主叙事建立在 Δ 上（L1 +0.40 / L2 +0.533），非绝对值。程序断言段（§2，无 LLM）与在线裁判**同向**，独立佐证 Δ 方向。

### 6.3 语料拉平口径（单一自变量 = 图谱）
组 A 与组 B 用**同一分词器**（`topic.zh_terms`）、**同一倒排语料**；组 A 剥离的仅是「路由/符号链接/impact 闭包/IMPLEMENTS 展开/repo_card/premise 校验」等**图谱编排能力**，检索底座相同。故两组之差纯粹归因于图谱，非分词器/语料差异——非稻草人对照。L3 组 A 反超（§6.6）即拉平口径下基线的真实优势区，进一步证明非稻草人。

### 6.4 评测通道（D-N5）
在线生成/裁判一律走用户提供的 grok-4.5 中转站（Anthropic 兼容 `/v1/messages`，`x-api-key` 鉴权）。阿里网关本 Phase 禁用在线调用（额度告急）。限速 0.4s/失败重试 2 次/单题失败标 error 不伪造。本轮 **256 次在线调用（128 生成+128 裁判）0 失败**（`online_call_stats`）。api_key/host 只入内存请求头，绝不落盘/入 commit（`.judge_config.json` 已 gitignore）。

### 6.5 主集较 v0.1 门禁 → 降级为「v0.2 首测基线记录」
门禁 §5「主集三层准确率较 v0.1 下降 ≤3pt」**本轮无 v0.1 对照可比**：V4 核实 v0.1 主集**从未物化**（`eval/` 原无目录、全仓无任何 L1/L2/L3 集，唯一 `.jsonl` 为对齐审计产物；D-P3）。故该门禁条**降级为「v0.2 首测基线记录」**——本表（§1/§2）即首测基线，非 pass/fail 判定。`gate_report.json` 同步一致表述。

### 6.6 L3 诚实负结果根因（非稻草人证据）
L3 组 A（0.90）反超组 B（0.50）：实测 L3 是「概念卡片即答案」的纯检索任务，BM25 稳定命中精确概念文档；组 B 路由把部分「为什么这个项目…」判为 meta→overview（本轮 L3 模式分布 `{overview:1, topic:9}`），overview 概览缺该概念的**设计理由与溯源提交**，故 L3-01 反而答不出。c+p 近平（1.0 vs 0.9）。这既是**非稻草人对照的证据**，也**暴露 `build_repo_context` 路由在 L3「why」问句上的过泛化**——真实缺陷，列入后续修（不在本轮冻结数字内粉饰）。

### 6.7 在线调用统计
| 桶 | 生成 ok/n | 裁判 ok/n |
|---|---|---|
| main_A | 60/60 | 60/60 |
| main_B | 60/60 | 60/60 |
| pp_B | 8/8 | 8/8 |
| **合计** | **128/128** | **128/128** |

> 来源：`eval/d2_results.json` → `online_call_stats`。0 error，无伪造。

---

## 7. 产物与可复现

| 产物 | 内容 |
|---|---|
| `eval/dataset_main.jsonl` | 主集 60 题 gold（D1，L1 20/L2 30/L3 10） |
| `eval/dataset.jsonl` | 48 题冻结集（L0/FZ/AMB/PP） |
| `eval/baseline_bm25.py` | 组 A BM25-only 基线（同分词器/语料） |
| `eval/run_d2.py` | 两组评测编排（生成+裁判+程序断言） |
| `eval/d2_results.json` | D2 全量结果（§1/§2/§4/§6.7 来源） |
| `eval/d2_runs/*.jsonl` | 逐题上下文/生成/裁判原始记录（可回溯） |
| `eval/gate.py` / `eval/gate_report.json` | 48 题离线门禁（§3 来源，@HEAD 复跑） |
| `design_work/d3_f9.py` / `design_work/d3_f9.json` | F9 归因（§5 来源，可复现） |

复现：`python eval/gate.py`（48 题离线门禁）、`python design_work/d3_f9.py`（F9 归因）——均确定性、纯 stdlib、不碰网关。在线两组评测经 grok-4.5 中转站，记录已落盘。
