# V3 概念对齐 blocking 替代实测报告（Phase B · V3 / D-P2）

**日期**：2026-07-22　**语料**：单仓库 `multi-agent-orch`（`output/graph.json` 510 节点，139 Concept，tag `rebaseline-20260723`）
**补跑说明**：本报告由 Phase B 收口 agent 补跑——前序 V3 agent 未产出任何 blocking 工件（全仓 grep「blocking/jaccard/同义」无 V3 实验产物）。为守「禁止臆造实验数据」，D-P2 不得据空报告转生效，故就地补做真实可复现实验。
**实验脚本/产物（可复现，全部落盘 `design_work/`）**：`v3_blocking_eval.py`（`dump`/`probe`/`eval` 三子命令）→ `v3_concepts_dump.txt`(139 概念名)、`v3_pairs_ranked.txt`(全对 Jaccard≥0.15)、`v3_blocking_eval.json`(指标全表)。
**安全**：探针读取 `claude-ui/config.json` 网关鉴权令牌仅入请求头，脚本内正则二次掩码，全程 `sk-****`，不落任何产物。

---

## 1. 实验对象与生产现状

概念对齐 blocking = 抽取期把「表面名不同但实为同一概念」的候选对送入 matching 层（LLM 裁决合并与否）之前的**粗筛**。生产现状（`src/repograph/extract/semantic.py:310 _align` + `:344 _norm_key`）：blocking = **精确 norm_key**（小写、去空格与连字符后**完全相等**才并组）。`output/align_audit.jsonl` 实测 139 组中几乎全部 `merged:false`、单成员——精确键 blocking 对近义/子集重名概念**近乎零召回**。

V3 对比两个替代 blocking（matching 层 LLM 裁决**两案均不变**，仅粗筛策略变）：
- **(a) 词面 blocking**：`norm_key` 归一 + 字符 bigram Jaccard ≥ τ。纯 stdlib。
- **(b) 索引期一次性离线 API embedding blocking**：概念名向量化、余弦 ≥ τ。需 embeddings 端点。

**判定规则（计划书 §3 V3）**：召回差 ≤10% 取 (a)；否则取 (b)。

---

## 2. Gold 集（20 对，取自真实 139 概念名；收口 agent 语义标注已披露）

正样本 15 对（同一底层概念的不同表面名，blocking 应召回）+ 负样本 5 对（词面高相似但**不同**概念，测误纳）。正样本刻意混入表面相异/跨语言/改述难例，避免偏向词面案。**判断为收口 agent 人工语义标注，非机器产物；概念名 100% 真实存在于 `output/graph.json`（脚本 `evaluate()` 已校验，缺一即 abort）。**

**正样本逐对 bigram Jaccard**（降序，`exact_key` 全 false→生产 blocking 全 miss）：

| # | 概念 A | 概念 B | Jaccard | 难度 |
|---|---|---|--:|---|
| 1 | 多线程 workspace 运行 | 多线程workspace | 0.846 | 易 |
| 2 | worktree 隔离 | worktree 隔离装配 | 0.818 | 易 |
| 3 | 玻璃感 Web 控制台 | 玻璃感 Web 控制台网关 | 0.800 | 易 |
| 4 | 用户界面 CLI | 用户界面 CLI 子集 | 0.750 | 易 |
| 5 | 验证钩子 | 验证钩子模块 | 0.600 | 易 |
| 6 | CLI §12 typer 骨架命令子集 | CLI §12 子集 | 0.353 | 中 |
| 7 | 异步作业 | 长作业真异步 | 0.333 | 中 |
| 8 | events端点 artifacts/bb_ops 投影 | events端点投影补全 | 0.296 | 中 |
| 9 | ChaosHarness mock层与注入点 | 混沌 harness | 0.286 | 难（跨语言）|
| 10 | 50 轮硬门槛测试 | 混沌 50 轮 100% 硬门槛 | 0.286 | 中 |
| 11 | render四层视图组装 | 视图组装 | 0.273 | 中 |
| 12 | 启动时对线程机械执行崩溃恢复 | 崩溃恢复算法 | 0.200 | 难（改述）|
| 13 | async核心环 | 异步版核心环 | 0.200 | 难 |
| 14 | 适配层 | 适配层统一 invoke 接口 | 0.167 | 难 |
| 15 | 停机重启 | 停机-重启-approve-terminate控制流 | **0.143** | 最难（表面重合最低）|

负样本（词面近但异概念，blocking 会纳入、matching 层负责剔除）：ApiAdapter‖CliAdapter(0.700)、CliAdapter‖FakeCliAdapter(0.692)、ApiAdapter‖FakeApiAdapter(0.667)、FakeApiAdapter‖FakeCliAdapter(0.667)、M3冻结接口契约‖M4冻结接口契约(0.556)。

---

## 3. 结果

### 3.1 arm (b) embedding：**as-built 不可运行**（真探针）

`v3_blocking_eval.py probe` 实打网关两个候选端点：

```
/v1/embeddings → HTTP 404
/embeddings    → HTTP 404
```

网关为 Anthropic 兼容 `/v1/messages` 形态，**无 embeddings 端点**；索引期原可选后端 grok CLI 已 402 断供（D-N4）。故 **(b) 索引期 API embedding blocking 在当前 as-built 无载体、不可运行**。此为实测事实，非推断。

### 3.2 arm (a) 词面 bigram Jaccard vs 生产精确键（真实 recall/成本）

| blocking | 正样本召回 | 负样本纳入 | 候选对数 | 候选占比(/9591) |
|---|--:|--:|--:|--:|
| **生产：精确 norm_key** | **0.000 (0/15)** | 0/5 | — | — |
| 词面 Jaccard τ=0.15 | **0.933 (14/15)** | 5/5 | 83 | 0.87% |
| 词面 Jaccard τ=0.20 | 0.867 (13/15) | 5/5 | 60 | 0.63% |
| 词面 Jaccard τ=0.25 | 0.733 (11/15) | 5/5 | 38 | 0.40% |
| 词面 Jaccard τ=0.30 | 0.467 (7/15) | 5/5 | 25 | 0.26% |
| 词面 Jaccard τ=0.35 | 0.400 (6/15) | 5/5 | 20 | 0.21% |

- **生产精确键 blocking 召回 = 0**：15 对真实近义/子集重名概念，精确 norm_key **一对都抓不到**——量化确认「blocking 近乎失效」。
- **词面 Jaccard τ=0.15 召回 0.933**，候选仅占全对 0.87%（83/9591）→ 送 matching 层的 LLM 裁决量极小。唯一漏的是 #15（`停机重启`‖`停机-重启-approve-terminate控制流`，Jaccard 0.143<0.15），正是表面重合最低的改述难例。
- 负样本 5/5 全被纳入候选——**符合 blocking 设计**：blocking 只需高召回，精度由 matching 层（LLM）负责剔除（计划书「matching 层两案不变」）。候选量 0.87% 说明「过纳」成本可忽略。

### 3.3 关键实测发现：本语料真同义对**几乎都表面相似**

15 对真同义中 14 对 Jaccard≥0.15，仅 1 对<0.15。根因：139 概念名由**同一代码库**自动抽取，同一概念的不同表面名高度共享词汇（`用户界面 CLI`/`用户界面 CLI 子集`、`验证钩子`/`验证钩子模块`）。**真正表面相异的跨语言/改述同义对在本语料极稀缺**（≈1/15）——即 embedding 理论优势区（表面不可达同义）在本图近乎不存在。这与 FZ 口语指称集（query↔concept，天然表面相异，BM25 难）是**两个不同问题**：概念对齐是 concept↔concept，双侧均为抽取期规范概念名，同源同词。

---

## 4. 裁定 D-P2（blocking 方案）

**裁定：选 (a) 词面 bigram Jaccard blocking（τ=0.15–0.20），守 stdlib。** 依据：

1. **(b) 在 as-built 不可运行**（embeddings 端点 404 + grok 402），无载体可比；「召回差 ≤10% 取 (a)」的比较前提坍缩为「(a) 是唯一可运行的 stdlib 选项」。
2. **(a) 相对生产精确键是净提升**：真实近义概念召回 0→0.933（τ=0.15），候选成本仅 0.87% 全对，matching 层裁决量可忽略。
3. **embedding 的理论优势区在本语料近乎为空**：真同义对 14/15 表面相似，唯一表面相异难例（#15，Jaccard 0.143）落在 10% 召回带内（1/15=6.7%<10%），即便 embedding 可用也未必稳抓该改述对。故「切 (b) 换取召回」在本图不划算，与全局 stdlib 取向（D-22/D-N4/D-P1）一致。

**依赖损失声明：无**（未引入任何第三方/embedding 依赖，运行时与索引期均维持纯 stdlib）。

**落地绑定（供 Phase C）**：blocking 由生产的「精确 norm_key 相等」升级为「`norm_key` 归一 + 字符 bigram Jaccard ≥ 0.15 即入候选」，matching 层（LLM 合并裁决）不变；τ 默认 0.15（偏高召回），Phase C 概念对齐上线后可在 align_audit 上复核精度/成本再微调。**注**：这是 blocking 粗筛策略变更，`_align` 当前是「同键即合并、无 matching 裁决层」，Phase C 需同时补 matching 层（LLM 裁决）方能安全采纳低阈值高召回候选——本裁定只定 blocking 策略，matching 层建设属 Phase C 落地。

**复审触发**：Phase C 概念对齐上线后于 `align_audit.jsonl` 复核 blocking 精度/候选成本；若未来引入 embeddings 端点或图规模致概念表面相异同义显著增多，重评 (b)。

---

## 5. 结论摘要（交编排器）

- 前序 V3 无产物 → 收口 agent 补跑真实实验，禁止臆造。
- embedding arm (b)：网关 embeddings 端点 **404**、grok **402** → **as-built 不可运行**（实测）。
- 生产精确键 blocking 召回 **0/15**；词面 bigram Jaccard τ=0.15 召回 **14/15 (0.933)**、候选成本 0.87%。
- 本语料真同义对 14/15 表面相似，embedding 优势区近空。
- **D-P2 = 词面 Jaccard blocking（τ=0.15–0.20），守 stdlib，依赖损失=无**。
