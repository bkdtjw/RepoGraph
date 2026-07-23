# RepoGraph

> 面向编码 Agent 的**代码知识图谱 + 混合检索系统**：把一个 Python 仓库的「结构」（AST / 调用链 / git 历史）与「语义」（commit / docstring / 设计决策）统一抽进一张属性图，让 Agent 不只知道「符号在哪」，还能回答「改这里会波及谁」和「这套设计是怎么演化来的」——并把这份图谱以**两种形态**喂给上游模型：网关 system-prompt 注入（push）与 **MCP stdio 工具**（pull，Claude Code 即插即用）。

RepoGraph 处于编码 Agent 的**上下文供给层**：`grep` / 读文件只能定位符号，而多跳影响面分析与设计溯源需要沿调用链、导入链、提交历史做跨文件图遍历——这正是本项目补的能力。**运行时零第三方依赖**（检索 / 门禁 / MCP 全走 Python 标准库），克隆即跑。

---

## 核心数字一览

> 全部来自真跑，评测树 `src/repograph`（检索/评测相关模块 `retrieve/*` + `models.py` 等）+ `output/graph.json` @ `c971078`——**评测路径代码与图谱自 D1 提交 `7df38d4` 起逐字未变**（`git diff 7df38d4 HEAD -- src output` 仅新增 Phase E 薄适配器 `mcp_server.py`，不在评测路径、不影响任何数字），tag **`phase-d-20260723`**。主叙事 = **两组对比**：组 A `BM25-only 无图谱基线` vs 组 B `图谱混合`，单一自变量 = 图谱（D-R2）。所有检索/准确率数字为 `semantic_mode=lexical` **下界**（S2 改写 / L2 LLM 增益未计入）。逐项来源见 [§5 评测](#5-评测两组三层--48-题终态--f9-定量)。

| 能力 | 组 A（纯 BM25） | 组 B（图谱混合） | 读数 | 来源 |
|---|---|---|---|---|
| **L2 反向调用闭包** c+p | `0.167` | **`0.867`** | **Δ+0.70** — 影响面闭包是图谱独有能力 | `report.md §1` |
| **L2 correct** | `0.00` (0/30) | **`0.533`** (16/30) | 纯 BM25 结构性为 0 | `report.md §1` |
| **L1 符号/模块定位** correct | `0.25` (5/20) | **`0.65`** (13/20) | Δ+0.40 | `report.md §1` |
| **L0 元问题通过率** | `0.0` (0/10) | **`1.0`** (10/10) | 「你了解这个项目吗」类 | `gate_report.json` |
| **AMB 消歧行为一致率** | `0.0` (0/10) | **`1.0`** (10/10) | 过问率 0 / 漏问率 0 | `gate_report.json` |
| **PP 错误预设纠正**（端到端在线） | — | **`0.875`** (7/8) | 0 顺预设作答 | `report.md §4` |
| **裸拒率**（硬门禁） | `0` | **`0`** | 永不裸拒 | `gate_report.json` |
| **L3 设计溯源** correct | **`0.90`** (9/10) | `0.50` (5/10) | **诚实负结果 Δ−0.40**，路由过泛化真实缺陷 | `report.md §6.6` |

图谱规模（`multi-agent-orch` 单仓库快照）：**510 节点 / 1698 边** —— Module 22 · Class 15 · Function 259 · Commit 75 · Concept 139；CALLS 352 · MODIFIES 533 · IMPLEMENTS 390 · DESCRIBES 104。

---

## 1. 这是什么 / 为什么

编码 Agent 面对一个陌生仓库时，`grep` 与文件读取能回答「符号 `X` 出现在哪几行」，但答不出两类真正影响改动决策的问题：

1. **「改 `X` 会波及谁」** —— 需要沿 `CALLS` 反向 BFS 求调用方闭包，跨文件、含间接调用，`grep` 的词面命中 ≠ 调用边。
2. **「这套设计是怎么来的」** —— 需要把 docstring / commit message / 设计决策抽成概念节点，沿 `IMPLEMENTS` / `DESCRIBES` 汇集实现函数与提交历史。

RepoGraph 在**索引期**把这两类素材显式制造进图谱，在**查询期**用「路由器 + 四档瀑布」把自然语言问题（含中文口语、错别字）落到真实图谱证据上，再把结果喂给上游模型。实证差异见 [`design_work/e2_acceptance.md`](design_work/e2_acceptance.md)（动作 C：同一个「改 `_handle_terminate` 影响面」任务，关 MCP 只能 grep 到「在哪」，开 MCP 给出调用方闭包 / 受影响模块 / 闭包是否穷尽）。

---

## 2. 架构总览

四层抽取—检索栈，两种消费形态。**结构层确定性、语义层概率、检索层「路由器 + 四档瀑布」**，两条消费路径（网关注入 push / MCP 工具 pull）复用同一套检索函数。

```text
┌──────────────────────── 消费形态（两条路径，复用同一检索层）─────────────────────────┐
│  (push) claude-ui 网关         │  (pull) MCP stdio 工具  ←── Claude Code / 任意 MCP 客户端 │
│  server.py 把上下文注入          │  mcp_server.py（纯 stdlib JSON-RPC 2.0，D-N8）           │
│  聊天 system prompt（无工具）    │  ask_repo / impact_analysis / repo_overview（D-14/D-23） │
└───────────────────────────────┬──────────────────────────────────────────────────────┘
                                 ▼
┌──────────────────────── 检索层：路由器 + 四档瀑布（纯 stdlib）────────────────────────┐
│  S0 normalize → S1 路由器 route() ─┬─ meta ────────→ 注入 repo_card.json（零检索）        │
│  (retrieve/router.py 规则表        ├─ global ──────→ build_overview + level-1 概览属性     │
│   + 网关 _rg_llm 兜底分类)         ├─ entity_local → 四档瀑布 ↓                            │
│                                    ├─ structural ──→ 确定性计数 / 降级 topic               │
│                                    └─ out_of_scope → 界外声明 + ≥1 建议问法（P4 不裸拒）    │
│  entity_local 四档瀑布：L0 符号 link_entities → L1 主题 BM25 topic_recall                  │
│                         → L2 受限 LLM 选概念 → L3 概览兜底 build_overview（永不失联）       │
│  横切：S2 改写扩展 · S4 消歧协议 disambiguate · S5 焦点栈 · S7 前提校验 verify_premises     │
└───────────────────────────────┬──────────────────────────────────────────────────────┘
                                 ▼
┌──────────────────────── 索引期抽取（重建图谱时才需第三方依赖）──────────────────────────┐
│  结构层（确定性）  ast 结构抽取 │ 调用图解析（只落可静态判定的边）│ git diff→函数跨度映射   │
│  指标层（确定性）  metrics.py：fan_in / heat / pagerank(幂迭代) / blast_radius / cyclomatic │
│  语义层（概率）    LLM 概念抽取（evidence+confidence）→ 词面 Jaccard blocking → LLM 对齐    │
│  双语卡片（索引期）zh_desc / zh_aliases 入 BM25 语料（无向量层，靠词面桥接口语，D-11/D-22） │
└──────────────────────────────────────────────────────────────────────────────────────┘
        存储：本地 GraphStore（内存 dict + JSON，output/graph.json）——非 PG/AGE（D-N3）
```

- **结构层不存在实体对齐问题**：规范 ID（`{repo}::{relpath}::{qualname}`）即 MERGE 合并键，重跑幂等，对齐工作被压缩到概念层。
- **确定性与概率隔离**：概念边（LLM 产物）**永不参与 `impact_analysis`**——确定性工具不掺概率数据（P3）。
- **零第三方依赖的边界**：检索 / 门禁 / MCP 只 import `models` + `retrieve/*`，两者纯 stdlib（已实测：屏蔽 `pydantic/git/networkx/matplotlib` 后离线路径仍可 import 并跑出 `functions=259`）。第三方依赖（GitPython / networkx / matplotlib / pydantic）**只在重建图谱**（`index`/`semantic`/`viz`）时需要。

图谱 Schema（六类节点 · 九类边）、质量指标（`call_resolved_rate` / `modifies_coverage` / `dangling_modifies` …）详见 [§7 图谱 Schema 与质量指标](#7-图谱-schema--质量指标)。

---

## 3. 快速开始（5 分钟验收线）

> **验收目标**：陌生人 clone 后 5 分钟内看到门禁真实红绿 + MCP 工具真实跑绿，**全程零第三方依赖、零密钥、零在线调用**。仓库自带一份已建好的演示图谱 `output/graph.json`（`multi-agent-orch` 快照，510 节点），无需自己先建图。

### 3.1 克隆

```bash
git clone https://github.com/bkdtjw/RepoGraph
cd RepoGraph
```

Python ≥ 3.12（本机 3.14 实测）。**到这一步即可跑门禁与 MCP**——它们只用标准库，不需要 `pip install`。

### 3.2 看门禁红绿（离线、纯 stdlib、约数秒）

```bash
python eval/gate.py
```

对仓库自带的 `output/graph.json` 离线逐题跑 `build_repo_context`（不经网关、不调 LLM），48 题分子集断言，产出 `eval/gate_report.json` 并打印红绿摘要。**预期看到**：L0 元问题 `10/10`、AMB 一致率 `1.0`、裸拒率 `0`（绿）；FZ-dev hit@3 `0.7`（B-2 仍红，如实呈现，见 [§5](#5-评测两组三层--48-题终态--f9-定量)）。

复现 F9（无向量层召回代价归因）：`python design_work/d3_f9.py`（同样离线、纯 stdlib）。

### 3.3 MCP 工具真测（子进程真实 stdio JSON-RPC）

```bash
python tests/test_mcp_server.py
```

把 `python -m repograph.mcp_server` 拉起为**子进程**，走真正的换行分隔 JSON-RPC 2.0 会话（`initialize → tools/list → tools/call`），对真实图谱断言真实检索值（如 `impact_analysis(_handle_terminate)` → 3 直接调用方；`invoke` → 歧义 6 候选；`repo_overview` → 259/139/22/15/75）。**预期末行**：`ALL 16 MCP TESTS PASSED`。

### 3.4 接入 Claude Code（三步）

1. 复制模板到项目根：`cp .mcp.json.example .mcp.json`
2. 把 `.mcp.json` 里两个占位路径改成**你的克隆绝对路径**（`PYTHONPATH` 指向 `.../RepoGraph/src`，`REPOGRAPH_GRAPH` 指向 `.../RepoGraph/output/graph.json`）：

   ```json
   {
     "mcpServers": {
       "repograph": {
         "command": "python",
         "args": ["-m", "repograph.mcp_server"],
         "env": {
           "PYTHONPATH": "/ABSOLUTE/PATH/TO/RepoGraph/src",
           "REPOGRAPH_GRAPH": "/ABSOLUTE/PATH/TO/RepoGraph/output/graph.json"
         }
       }
     }
   }
   ```

3. 在项目根启动 Claude Code，`/mcp` 面板确认 `repograph` 已连接、列出三工具 `ask_repo / impact_analysis / repo_overview`。逐步演示与录屏手册见 [`design_work/e2_acceptance.md`](design_work/e2_acceptance.md)。

> 若已 `pip install -e .`（把 `repograph` 装进环境），可省去 `env` 整块——`REPOGRAPH_GRAPH` 缺省即解析到仓库 `output/graph.json`。工具全离线读图谱，屏幕上不会出现任何 token/host。

### 3.5 （可选）重建图谱 —— 需第三方依赖 + 自配 LLM 通道

上面全部功能（检索 / 门禁 / MCP）都跑在仓库自带的演示图谱上，**不需要**这一步。只有当你要对**另一个仓库**重新建图时，才需要安装依赖并配置语义层 LLM：

```bash
pip install -e .                                   # GitPython / networkx / matplotlib / pydantic
repograph all --repo <目标仓库路径> --name <名>     # 结构层 + git 层 → 语义层 → 可视化
```

- **结构层 + 指标层**（`index`）纯确定性，无需 LLM，产出 `graph.json` / `stats.json`。
- **语义层**（`semantic`：概念抽取 + 双语卡片 + 对齐）**需自配 LLM 通道**——原 grok CLI 订阅已断供（402，D-N4），现以标准库 `urllib` 直连 **Anthropic 兼容 `/v1/messages` 网关**（`src/repograph/extract/llm_client.py`，默认模型 `qwen3.8-max-preview`）。参考实现从同级 `claude-ui/config.json` 读取网关地址（`anthropic_base_url`）与令牌（`anthropic_auth_token`）——换成你自己的兼容网关即可；**令牌只入内存请求头，绝不打印 / 写盘 / 入库，文档中一律 `sk-****` 占位**（配置字段见 `extract/llm_client.py` 顶部注释）。不配置语义层，仍可用**全部检索 / 评测 / MCP** 功能（演示图谱已含语义层产物）。

子命令一览（`repograph <cmd> --help`）：`index` / `semantic` / `viz` / `impact` / `stats` / `all`。

---

## 4. Design vs Reality（设计 vs 现实：一条如实的演化链）

RepoGraph 的技术设计文档（`docs/archive/`）以一套**理想架构**写成；本仓库是它逐条落到「Python 标准库 + 网关注入 + MCP」现实的 **as-built**。这一节如实记录差距与每一步裁定——**不粉饰，红的照红**。

### 4.1 v0.1 理想设计 → as-built 差距矩阵

设计文档目标形态：**PostgreSQL 16 单实例三引擎**（Apache AGE 属性图 + pgvector 向量 + 关系表）+ MCP 工具层 + networkx PageRank + 进程内单会话焦点栈。逐文件核对（`design_work/gap-matrix.md`）结论：原稿机制**已完整落地 0 条 / 部分实现 11 条 / 未实现 20 条 / 不适用 1 条**，另有**六项基础设施**（向量层·embedding、`symbol_alias` SQL 表、MCP 服务、六类模糊谓词属性、增量索引水位、对 CALLS/IMPORTS 图算的 PageRank）在 v0.1 代码库中**均不存在**。

as-built 的现实是：活跃检索路径是 `retrieve/{context,topic,impact,router,repo_card}.py` 的**纯词面「路由器 + 四档瀑布」**；存储是本地 `GraphStore`（内存 dict + JSON，`output/graph.json`）；集成先是网关把上下文注入 system prompt（push），Phase E 复归 **MCP stdio 工具**（pull）。

### 4.2 每一条偏离都有裁定（DECISIONS.md，33 具名条目）

架构级偏离一律登记进 [`DECISIONS.md`](DECISIONS.md)，commit 引用 D-编号方可合并（治理规则 R2）。骨架 = spec §3 决策表 **24 项机制**（10 平移 / 10 改造 / 4 砍除）+ 复议横切层 **D-R2 / D-N1..N8**（共 33 具名条目）+ Phase B/D 验证配套 D-P1..P4。要点：

| 裁定 | 内容 | 显式损失 |
|---|---|---|
| **D-22** 砍向量层 | pgvector/embedding 砍除，510 节点规模无收益、守零依赖 | 无语义近邻召回，长尾口语靠 BM25+双语卡片（代价由 F9 定量，见 §5） |
| **D-N3** 存储 stdlib | 追认本地 `GraphStore`（JSON），PG+AGE 降为 Roadmap | 无常驻图库的多仓/并发写（单仓场景不需要） |
| **D-11** 双语可达 | 无 embedding，靠索引期中文卡片 `zh_desc`/`zh_aliases` 入 BM25 | 跨语言召回退化为词面命中（FZ 天然难集） |
| **D-N8** MCP=stdlib | 官方 `mcp` SDK 可用仍主动选纯 stdlib JSON-RPC，守「clone 即跑、免 pip install」 | 不享 SDK 协议自动跟随（3 方法规模可忽略） |
| **D-N7** 三工具 | 交付 `ask_repo`/`impact_analysis`/`repo_overview`；`query_graph`(text2cypher) 推迟 v0.4 | 上游无法自然语言跑精确结构查询（死代码类仍缺口） |
| **D-14/D-23** MCP 复归 | `repo_overview` 从「meta 路由注入」复归为「按需工具」；push 模型的「Agent 无法主动拉概览」损失消除 | — |

### 4.3 前置验证复议（V0–V5：实测推翻了部分理想假设）

Phase B 用真实图谱跑前置验证，**结论直接改写了 spec**（R1 以实测为准）：

- **V0 分带校准**：FZ-dev 32 单元网格**零可行单元**（hit@1 恒 0 / 消歧触发率全 >0.2 上限）→ **拒绝冻结任何 topic 分带阈值**，续用过渡规则「仅方法档 ≥80 自动锚定」（D-N1）。根因是召回被卡片输入天花板卡住，非分带。
- **V1 中文分词**：n-gram 与 jieba 在裁定集 hit@3 完全并列（均 0.1）→ 选 **n-gram 守 stdlib**，jieba 不破例（D-P1）。
- **V3 概念对齐 blocking**：精确 `norm_key` 相等对 15 对真实近义概念召回 **0/15**；改词面 bigram Jaccard（τ=0.15）召回 **14/15 = 0.933**、候选成本仅 0.87% → 选词面 Jaccard 守 stdlib（D-P2）。索引期 API embedding blocking **as-built 不可运行**（网关 `/v1/embeddings` 实测 404、grok 402）。
- **V5 F9 定量**：见 §5，无向量层净代价 = FZ-test 词面不可达 `2/10 = 0.20`（D-P4）。

### 4.4 终态：三红两绿（冻结数字，全部可复现）

数字冻结在 tag `phase-d-20260723`。**硬门禁全绿**（裸拒率 = 0、PP 端到端 0 顺预设作答 / 0 泄漏），下面是五项载荷结论的红绿台账——**两绿为图谱决胜，三红照实记录、不进冻结数字粉饰**：

| | 结论 | 读数 | 来源 |
|---|---|---|---|
| 🟢 | **L1 图谱决胜** | correct `0.25→0.65`（Δ+0.40） | `report.md §1` |
| 🟢 | **L2 图谱独有能力** | correct `0.00→0.533`（Δ+0.533）、c+p `0.167→0.867`（Δ+0.70） | `report.md §1` |
| 🔴 | **L3 诚实负结果** | correct `0.90→0.50`（Δ−0.40）——路由把部分「为什么…」问句过泛化为 meta→overview，概览缺该概念设计理由 | `report.md §6.6` |
| 🔴 | **B-2 未闭合** | FZ-dev hit@3 `0.7 < 0.8`（差 1 题：d06/d09/d10） | `gate_report.json` |
| 🔴 | **路由准确率** | `0.8542 < 0.9`——7 处 mismatch 全为 PP「why」问句（判 entity_local，同 L3 过泛化家族） | `gate_report.json` |

同时，三个 v0.1 锁定失败（`rebaseline-20260723` 起 RED）追踪至终态：**B-1**（L0 口语元问题误路由）RED→**GREEN**；**B-2**（FZ-dev<0.8）RED→**RED**（如实）；**B-3**（前提校验能力缺失）RED→**GREEN**。来源 `gate_report.json → locked_failures`。

---

## 5. 评测（两组三层 + 48 题终态 + F9 定量）

> 判定分两类，严禁混为一谈：**程序门禁集**（纯 stdlib 对真实图谱可断言、可复现，进硬门禁）与**裁判报告集**（LLM 裁判，仅报告与人工复核、不进硬门禁）。主叙事 = 组 A `BM25-only 无图谱` vs 组 B `图谱混合`，同分词器 / 同倒排语料，**单一自变量 = 图谱编排能力**（非稻草人对照，D-R2）。全部 `semantic_mode=lexical` 下界。

### 5.1 主表：主集 60 题 × 三层 × 两组（在线裁判 grok-4.5）

主集 60 题（L1=20 / L2=30 / L3=10，D1 补建、gold 对真实图谱 0 失败）。`c+p` = correct_or_partial。来源 `eval/d2_results.json → segment2_answer_accuracy` / `eval/report.md §1`，tag `phase-d-20260723`。

| 层 | 任务 | 组A correct | 组B correct | **Δcorrect** | 组A c+p | 组B c+p | Δc+p |
|---|---|---|---|---|---|---|---|
| **L1** | 符号/模块定位 | 0.25 (5/20) | **0.65** (13/20) | **+0.40** | 0.40 | 0.75 | +0.35 |
| **L2** | 反向调用闭包 | 0.00 (0/30) | **0.533** (16/30) | **+0.533** | 0.167 | **0.867** | **+0.70** |
| **L3** | 设计溯源 | **0.90** (9/10) | 0.50 (5/10) | **−0.40** | 1.00 | 0.90 | −0.10 |

**程序断言段**（离线确定性、无 LLM，与在线裁判同向，来源 `report.md §2`）：L1 上下文含 gold 模块路径 hit@min1 组A `0.60` / 组B **`0.80`**；L2 反向闭包∩上下文 hit@min1 组A `0.267` / 组B **`0.90`**；L3 gold 来源召回 hit@min1 组A `1.00` / 组B `0.90`。

> **L2 为 depth=2 下界**：组 B 用默认 `impact_depth=2`，L2 gold 闭包按 depth=3 生成（未调参、未改冻结 src），depth=3 预期更高。

### 5.2 48 题冻结集终态（组 B = 完整 v0.3 / 组 A = BM25-only）

来源 `eval/gate_report.json`（@HEAD 复跑）与 `eval/d2_results.json → set48`，两套 harness 逐项交叉一致。

| 指标 | 组A | 组B | 说明 |
|---|---|---|---|
| L0 元问题通过率 | 0.0 (0/10) | **1.0 (10/10)** | 组 A 无 repo_card/overview 路由，元问题恒答不出 |
| FZ_dev hit@3 | 0.7 | 0.7 | 纯检索，两组同源同分（诚实平局；B-2 仍红） |
| FZ_test hit@3 | 0.8 | 0.8 | 同上（冻结留出集） |
| AMB 行为一致率 | 0.0 (0/10) | **1.0 (10/10)** | 组 A 无消歧协议；过问率 0 / 漏问率 0 |
| PP 泄漏率 | 0.0 | 0.0 | 图中本无 redis/postgres 等技术名 |
| 裸拒率（硬门禁） | 0 | 0 | 均不裸拒 |

**PP 前提校验（组 B 端到端在线，8 题错误预设）**：主动纠正 correct **0.875 (7/8)**、c+p **1.0 (8/8)**、顺预设作答 **0**、调用失败 0（来源 `report.md §4`）。S7 前提闸门在线生效。

### 5.3 F9：无向量层的召回代价（冻结 FZ-test，D-P4）

D-22 砍除向量层的定量代价，以最终配置在**冻结留出集 FZ-test（10 题）**上归因：`topic_recall(min_score=0, top_k=∞)` 得全量排名——某文档「不在排名中」⇔「与查询词元零 n-gram 交集」⇔ BM25 **结构性不可达**。来源 `design_work/d3_f9.json` / `report.md §5`。

| 子集 | hit@3 | 失败题 | 词面不可达 | 排序失利 | 向量层净代价 |
|---|---|---|---|---|---|
| **FZ-test（冻结, n=10）** | 0.8 | 2 (t03/t04) | **2** | 0 | **2/10 = 0.20** |
| FZ-dev（调参, n=10）交叉核验 | 0.7 | 3 (d06/d09/d10) | 2 | 1 | 2/10 = 0.20 |

FZ-test 失败 **100% 归因词面不可达、0 排序失利**：t03「喊了暂停」↔`stop 标志消费`、t04「打个记号」↔`标记`，gold 卡片虽经富化仍与查询**零 2-gram 交集**（仅单字重叠不成词元）——BM25 无论重排/调阈都够不到，**只有语义近邻能召回**。这 `2/10` 即向量层缺失的净代价，n=10 小样本如实标注。

### 5.4 口径注记（诚实性辩护）

- **lexical 下界**：全程 `semantic_mode=lexical`；S2 改写（同义改述二次召回）与 L2 LLM 需阿里网关，本轮因额度告急**未消耗**。所有数字为 lexical 档下界，真实上线（含 S2/L2）预期更高，本报告不据此推断、只报下界实测。
- **裁判自评偏置**：生成与裁判**同模型 grok-4.5**，存在自评偏置。缓解论证：偏置对两组**同向作用**，故**组间相对差 Δ 有效**（主叙事建立在 Δ 上，非绝对值）；程序断言段（无 LLM）与在线裁判同向，独立佐证。本轮 **256 次在线调用（128 生成 + 128 裁判）0 失败**（`report.md §6.7`）。
- **单一自变量 = 图谱**：两组同分词器 / 同倒排语料，组 A 剥离的仅是「路由 / 符号链接 / impact 闭包 / IMPLEMENTS 展开 / repo_card / premise 校验」等图谱编排能力。L3 组 A 反超即拉平口径下基线的真实优势区，进一步证明**非稻草人**。
- **评测通道**：在线生成 / 裁判走用户提供的 grok-4.5 中转站（Anthropic 兼容 `/v1/messages`，`x-api-key`）；host / api_key 只入内存请求头，绝不落盘 / 入 commit（`eval/.judge_config.json` 已 gitignore，D-N5）。
- **无 v0.1 主集对照**：V4 核实 v0.1 主集从未物化（`eval/` 原无任何 L1/L2/L3 集）；门禁「较 v0.1 下降 ≤3pt」条降级为「v0.2 首测基线记录」（D-P3），本表即首测基线，非 pass/fail。

复现：`python eval/gate.py`（48 题离线门禁）、`python design_work/d3_f9.py`（F9 归因）——均确定性、纯 stdlib、不碰网关。

---

## 6. 限制与 Roadmap

**静态调用图盲区**：动态分发、`functools.wraps` 之外的装饰器包裹、猴子补丁、字符串驱动的调度表等无法由静态 AST 判定，对应 `CALLS` 边缺失，影响面分析可能**漏报**。对策：`call_resolved_rate` 如实公布；`impact --mode imports` 以模块级导入链兜底给出更粗粒度影响面；本项目明确**不做**运行时插桩，静态盲区作为已知限制披露、不试图消除。概念层为概率产物，**概念边永不参与 `impact_analysis`**。

**模糊语义检索的召回上界（F9）**：不引入向量 / embedding 层（D-22），跨语言召回由 BM25 词面 + 双语卡片承载，存在结构性天花板——当用户口语与 gold 实体**零 n-gram 交集**时 BM25 够不到。已定量：FZ-test 净代价 `2/10 = 0.20`（见 §5.3）。

**已知缺陷（列入后续修，不在冻结数字内粉饰）**：

| 项 | 现状 | 方向 |
|---|---|---|
| **L3「why」问句路由过泛化** | `build_repo_context` 把部分「为什么…」判为 meta→overview，概览缺设计理由（L3 correct Δ−0.40、路由准确率 0.8542） | 收窄 meta 规则、为「why」问句保留 topic/溯源路径（D3/D4 修，v0.4） |
| **B-2 FZ-dev hit@3 = 0.7 < 0.8** | 剩 d06/d09/d10：2 词面不可达 + 1 排序失利 | 词面不可达需向量层；排序失利 d10 需重排调参 |
| **F9 向量层** | 无语义近邻召回 | 引 pgvector / embedding 为 Roadmap（F9 复审触发 = 词面不可达占比过高时重议 D-21/D-22） |
| **structural / text2cypher** | 精确结构查询（「改动最多的前 10 个函数」「死代码」）无载体，`query_graph` 推迟 | v0.4 补 text2cypher 模板层（D-N7；死代码类可先 `fan_in=0 ∧ 非 entrypoint` 出报告） |
| **PostgreSQL + AGE 后端** | 本地 `GraphStore`（JSON），单仓够用 | 节点 >5k / 多仓常驻 / 并发写时复审迁移（D-N3；`store/` 已留 Cypher 切换点） |

Roadmap 触发条件均写入 `DECISIONS.md` 各条「复审触发」，非拍脑袋排期。

---

## 7. 图谱 Schema 与质量指标

**节点（六类）**：`Module` / `Class` / `Function` / `Commit` / `Issue` / `Concept`（`Concept.ctype ∈ {design_decision, domain_concept, constraint}`，本图 81 / 40 / 18）。

**边（九类）**：结构 6 类由确定性抽取产出——`CONTAINS` / `IMPORTS` / `CALLS`（只落可静态判定的边，不猜测）/ `MODIFIES`（git diff → 函数跨度求交）/ `TOUCHES`（文件级兜底）/ `FIXES`；语义 3 类由 LLM 抽取产出——`PROPOSES` / `DESCRIBES` / `IMPLEMENTS`（目标符号经存在性校验）。

**质量指标（`repograph stats`，实测为准不预设数值）**：`call_resolved_rate`（已解析调用点 / 全部调用点，直接度量调用图完整度）、`modifies_coverage`、`dangling_modifies`（diff 命中但 HEAD 已不存在的函数，图中不留幽灵节点）、`parse_skips`、`blob_cache_hit_rate`、`semantic_extracted` / `extraction_reject_by_reason`。「你如何度量图谱质量」的答案就是这张表。

**输出物**：`output/graph.json`（全量图谱，`GraphStore.load()` 复原）、`output/stats.json`（质量指标 + 规模计数）、`output/repo_card.json`（level-0 概览卡片，MCP `repo_overview` 缓存）、`output/*.png`/`*.svg`（可视化）。

---

## 8. 仓库导览

| 路径 | 内容 |
|---|---|
| `src/repograph/retrieve/` | 检索层（路由器 + 四档瀑布，纯 stdlib，**冻结**） |
| `src/repograph/mcp_server.py` | MCP stdio 服务器（纯 stdlib JSON-RPC，D-N8） |
| `src/repograph/extract/` | 结构层 AST / git 抽取 + 语义层 LLM 通道（`llm_client.py`） |
| `src/repograph/metrics.py` | 索引期确定性指标（幂迭代 PageRank / fan_in / heat / cyclomatic …） |
| `eval/gate.py` · `eval/report.md` | 48 题离线门禁 · Phase D 评测报告（数字冻结） |
| `eval/d2_results.json` · `eval/gate_report.json` | 两组评测全量结果 · 门禁报告（README 数字来源） |
| `tests/test_mcp_server.py` | MCP 端到端真测（16 用例，子进程真实 stdio） |
| `docs/RepoGraph-v0.3-spec.md` | v0.3 唯一实施事实源（spec 母本，R1） |
| `DECISIONS.md` | 决策台账（33 具名条目，架构偏离登记册） |
| `design_work/e2_acceptance.md` | MCP 三验收动作实测 + Claude Code 录屏手册 |
| `docs/archive/` | v0.1 理想设计文档（Design vs Reality 的「Design」侧） |

---

## License

[MIT](LICENSE) © 2026 bkdtjw
