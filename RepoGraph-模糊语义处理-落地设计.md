# RepoGraph 模糊语义处理 · 落地设计

**副标题**：v0.2 落地版——基于补充稿与现有四层瀑布实现的融合设计
**版本**：v0.2-impl（落地版，取代 `RepoGraph-模糊语义处理设计.md` 的实施部分，保留其机制定义与骨架）
**日期**：2026-07-22
**通道变更（2026-07-22 晚）**：grok CLI 订阅到期断供（402 Payment Required，实测确认）。本稿所有「索引期语义调用」改经**阿里网关 HTTP 直连**（新增 `extract/llm_client.py`，模型 `qwen3.8-max-preview`，见 §4.7）；workflow 审查协同改用 **opencode headless**（`opencode run -m qwen/glm-5.2` 或 `qwen/qwen3.8-max-preview`，本机 provider 已配、实测可用）。运行时四层瀑布走网关 flash，本就不依赖 grok，零影响。

> **与原稿的关系（务必先读）**：原 v0.2 补充稿（`docs/archive/RepoGraph-模糊语义处理设计.md`）是在一套**理想架构假设**下写成的——它默认存在 MCP stdio 工具层、pgvector 向量层与 embedding 双语可达、networkx、进程内单会话焦点栈。经逐文件核对（差距矩阵 `design_work/gap-matrix.md`、架构适配 `design_work/arch-adapt.md`），这四类基础设施在当前代码库中**均不存在**：活跃检索路径是 `src/repograph/retrieve/{context,topic,impact}.py` 的纯词面「四层瀑布」，集成形态是 `claude-ui/server.py` 把上下文**注入聊天 system prompt**（push 模型、无工具），会话是 `data/sessions/{sid}.json` 磁盘文件。本落地稿把原稿的每一条机制**映射到 stdlib + 网关注入的现实**：机制的**意图与分类学（§1、§2 八类现象、P1–P5 五原则）整体保留**，机制的**物理落点全部重定位**。凡原稿假设与代码实况冲突处，以本稿为准。原稿仍是「机制为什么这样设计」的权威来源，本稿是「机制在本仓库怎么落地」的权威来源。

---

## 0. 决策概览（TL;DR）

- **决策分布**：对原稿 §4–§7 的 24 个可落地机制，判定为 **原样采纳（平移）10 项**、**改造采纳 10 项**、**砍除 4 项**（HyDE、pgvector/chunk/embedding 向量层、`repo_overview` MCP 工具形态、`ask_repo` 的 `context` 参数）。无一条原稿机制「已完整落地」——差距矩阵逐条核对结论：**已实现 0 条 / 部分实现 11 条 / 未实现 20 条 / 不适用 1 条**，另 6 项基础设施缺失。
- **架构升级一句话**：把 v0.1 藏在 `build_repo_context` 内部的**隐式二元瀑布**（`link_entities` 命中→symbol，否则 meta→overview / topic / overview 兜底）显式化为 **「S1 五路路由器 + 现有四档瀑布」二层结构**——路由器（meta/global/entity_local/structural/out_of_scope）是新增的**前置分诊层**，现有 symbol/topic/llm/overview 四档降为 entity_local 与 global/meta 路由**之下的实现**，不推倒重来。
- **P0 最小可用版**（0.5–1 天）：level-0 仓库卡片（在 `build_overview` 上补 `summary`+`entrypoints`）+ meta 路由（补 `_is_meta_question` 的口语盲区）+ 回退阶梯形式化。验收：「你知道我的代码库吗」返回卡片作答；全链路裸拒率 0（现状 L3 概览兜底已达成，P0 只是把它显式化并补口语覆盖）。
- **决策表范围与本稿承接**：§3 决策表只覆盖原稿 **§4–§7** 的 24 个可落地机制（10 平移 / 10 改造 / 4 砍除，24 行逐行加总可复核）；原稿 **§8 评测 / §9 成本 / §10 风险 / §11 计划** 不进决策表，分别承接进本稿 **§6 / §7 / §8**（成本量级不变：索引期批量廉价调用 + 查询期稳态 0–1 次 flash + 缓存）。

---

## 1. 现状与差距总览

> 本节结论引自差距矩阵（`design_work/gap-matrix.md`），不整段复制；关键锚点已由本稿独立 Read 复核。

### 1.1 v0.1 已有的地基（可复用，不重造）

| 地基 | 位置（已核对） | 复用方式 |
|---|---|---|
| 四层瀑布装配 | `context.py:192 build_repo_context`（L0 符号→元问题→L1 主题→L3 概览兜底） | 作为 entity_local/global 路由下的实现被路由器调度 |
| 词面链接 + 离散打分 | `context.py:88 link_entities` + `_SCORE`（`context.py:31`：exact=100/suffix=80/short=60/concept=40/module=30） | S3 链接 v2 的候选来源之一；分带按方法档重定义 |
| BM25-lite 主题召回 | `topic.py:199 topic_recall`（k1=1.5,b=0.75，`_MIN_SCORE=1.0`，语料 `_DOC_LABELS=(Concept,Commit,Module)`） | S3 的第二候选来源；双语可达靠此扩容而非 embedding |
| 概览兜底 | `context.py:683 build_overview`（真实 counts + 顶层模块按 loc + 热点函数按 MODIFIES 计数 + 核心概念按 IMPLEMENTS 落点） | level-0 卡片的确定性骨架，补 summary/entrypoints 即成 |
| 元问题识别 | `context.py:452 _is_meta_question` + `_META_MARKERS`（`context.py:444`，子串词表） | meta 路由的规则前身，需补口语/错别字覆盖 |
| 三档语义开关 | `server.py:211 _rg_semantic_mode`（off/lexical/llm） | 路由器的运行档位；llm 档承载 LLM 兜底 |
| 廉价 LLM 通道 | `server.py:430 _rg_llm_select_concepts`（读 `model_haiku`、`gateway_base`、POST `/v1/messages` 非流式、`gateway_headers`、静默降级、绝不回显 token） | 抽出 `_rg_llm_json` 底座供 S2 改写/S1 兜底分类复用 |
| 注入前缀 + SSE 事件 | `server.py:525 _rg_inject_prefix`、`server.py:1016 rg_event={mode,linked,stats}`、`server.py:1040 repo_context` SSE | schema v2 的载体：扩字段而非新建工具返回值 |
| 会话文件 CRUD | `server.py:610 read_session` / `:618 write_session`（磁盘 JSON、`atomic_write`） | 焦点栈持久化载体（`rg_focus` 键） |

### 1.2 三大缺口（原稿价值所在）

1. **查询理解子层整体缺失**（S0 规范化 + S1 显式五路路由器 + S2 改写 + S7 前提校验）：现状是 `build_repo_context` 内的隐式二元瀑布 + `semantic_mode` 三档，无 `{meta,global,entity_local,structural,out_of_scope}` 五标签、无改写/前提抽取。这是 P2「路由显式化」的载体，也是「你知道我的代码库吗」失效的根因层。
2. **三大基础设施不存在**：向量层/embedding（`store/ddl.sql` 的 `vector`/`chunk.embedding` 仅 AGE 预留，`store/age.py` 零读写，活跃 `GraphStore` 纯词面）、`symbol_alias`/`zh_alias` 中文别名表（`ddl.sql` 预留、无 zh_alias 值域）、MCP 工具层（`src` 全树无 `@mcp`/`FastMCP`）。令原稿的双语可达、embedding 候选、`repo_overview`/`ask_repo` 工具全部悬空。
3. **模糊谓词指标预计算 + schema v2 诚实回显三件套全缺**：节点无 `fan_in/pagerank/heat/cyclomatic/blast_radius/churn` 任一属性（原料齐备但未算），生成期只有「标注来源」一句，无 `resolved_query/candidates/premise_flags/suggestions`，「答案+边界+出路」三件套与代理披露均未落地。

### 1.3 被原稿假设存在、实则不存在的六项基础设施（落地必须绕开）

向量层/embedding · `symbol_alias`(zh_alias) SQL 表 · MCP 服务 · 六类模糊谓词指标属性 · 增量索引水位 · PageRank 计算（networkx **存在**于 `viz/render.py` 但未对 CALLS/IMPORTS 图计算）。本稿对每一项给出等价替代或砍除（见 §3、§4）。

---

## 2. 五条设计原则（决策依据，逐条决策标注）

原稿 P1–P5 **原样保留**，作为决策表的裁决依据：

- **P1 答案来源前置**：希望系统能答的问题，答案素材必须在索引期显式制造（概览/指标/双语卡片），不指望检索碰巧覆盖。
- **P2 路由显式化**：隐式单分支升级为可日志、可评测的显式多路。
- **P3 确定性工具不吃模糊输入**：`impact_analysis` 等模板工具入参必须已消解为规范 ID；模糊→规范只发生在链接层且对调用方可见。
- **P4 永不裸拒**：任何路径响应含至少一个可行动元素；「不知道」只能以「边界 + 建议问法」形态出现。
- **P5 解释回显**：响应携带 `resolved_query`（含指代消解与消歧选择），让误解可被发现纠正。

---

## 3. 逐机制落地决策表

> 决策 ∈ {**原样采纳**（机制设计不变、落点为扩展现有确定性函数）/ **改造采纳**（因基础设施缺位重定位形态，附具体改法）/ **砍除**（附理由与损失）}。每条标注所依据的原则。

### 3.1 §4 索引期增强

| 原稿机制 | 决策 | 具体改法 / 损失 | 依据 |
|---|---|---|---|
| §4.1 level-0 仓库卡片 | **原样采纳** | 扩 `context.build_overview`：确定性字段（stats/顶层模块/热点/核心概念）已在；补 `entrypoints`（聚合 `Function.is_endpoint`，本图为 0，如实产出空）、`hot_functions` 改用 §4.3 `heat`、`summary`（唯一一次索引期 LLM 调用，**经网关语义通道 `extract/llm_client.py`**（通道变更见文档头与 §4.7），输入=确定性字段+README 首段+Top 概念，≤300 字，程序校验专名白名单，失败降级为纯确定性卡片）。产出 `output/repo_card.json` 缓存。（机制设计不变、summary 沿用原稿既有的单次索引期 LLM 调用形态，故列原样而非改造。） | P1 |
| §4.1 level-1 包级概览「进向量层 chunk」 | **改造采纳** | 无向量层。改存为**顶层包 Module 聚合节点 `overview` 属性**（120 字，索引期经网关语义通道生成），global 路由直接注入属性文本，不进任何 chunk 池（本就无池）。损失：无「chunk 池隔离竞争」语义，但本图规模下顶层包可枚举，注入即可。 | P1 |
| §4.2 双语实体卡片「embedding 空间双语可达」 | **改造采纳** | 无 embedding。双语可达靠**词面+BM25**：索引期语义通道为核心函数/类生成 ≤40 字中文描述→(a) 作新语料进 `topic._corpus_nodes`（`_DOC_LABELS` 加 Function/Class 档、`_doc_text` 加分支）；(b) 名词短语抽为 `Concept.aliases`/新增 `Function.zh_aliases`。损失：跨语言召回从「向量近邻」退化为「BM25 词面命中」，长尾口语仍可能 miss（见 §6 FZ 子集实测）。 | P1 |
| §4.2 中文别名入 `symbol_alias(kind='zh_alias')` 表 | **改造采纳** | 无 SQL 表。写节点属性 `aliases[]`/`zh_aliases[]`；`link_entities` 的 Concept 分支已匹配 `aliases`（`context.py:151`），扩为 Function/Class 也读 `zh_aliases`。不建表。 | P1/P3 |
| §4.2 缩写扩展表（ctx→context…） | **原样采纳** | 新增 stdlib `dict` 常量 + `context._tokenize`（`context.py:55`）产候选后双向扩展。纯字符串操作。 | P1 |
| §4.3 `fan_in`/`heat`/`churn_90d`/`blast_radius`/`fix_involvement` | **原样采纳** | 新增 `src/repograph/metrics.py`（索引期、纯 stdlib、写节点属性），复用 `impact._reverse_adjacency`/`_bfs_levels`。`heat=commits_all+2×commits_90d`，「90d」以**仓库最新提交日**为基准（确定性，非 wall-clock；本图跨度 2026-07-04→07-06）。`fix_involvement`=FIXES 提交 ∩ MODIFIES 该函数的提交数。 | P1 |
| §4.3 `pagerank`（networkx） | **改造采纳** | networkx 不用于此图。**纯 Python 幂迭代**（`metrics._power_iteration`，d=0.85、阈 1e-6、含悬挂质量项）；本图 Function 259/CALLS 352，亚毫秒级。**拆两个独立指标**：Function 级 `pagerank`（CALLS）与可选 Module 级 `module_pagerank`（IMPORTS），不做原稿的「CALLS∪IMPORTS 并图」（节点异构会混层）。 | P1 |
| §4.3 `cyclomatic` 圈复杂度 | **原样采纳** | 落 `ast_extractor._ScopeVisitor._enter_function`，遍历函数体时对 `If/For/While/ExceptHandler/BoolOp 额外值/推导式 if/IfExp/match_case` 计数（1+分支数）；**白名单一次写死、`assert` 按惯例不计**，避免实现分叉；`FunctionFacts` 加 `cyclomatic` 字段；嵌套函数各自归属。`loc` 用现成 `span_end-span_start+1`，不新算。 | P1 |
| §4.4 停用/疑问词 + 代码词元检测器 | **原样采纳** | `topic.zh_terms`（`topic.py:50`）+ `context._tokenize` 加中文停用/疑问词过滤（怎么/哪个/那块/搞的）；代码词元检测抽独立纯函数 `is_code_token`（camelCase/snake_case/点路径/后缀/`#数字`/路由样式），**同时服务路由器与链接器**。 | P2 |

> **§3.1 表外两条附属决策**（原稿 §4 提及、**不计入 24 机制主表**，故不占计数）：① **刷新策略**（原稿「随增量水位重算」）→ **改造采纳**：无增量水位（§1.3），砍增量、改全量——每次全量 `build` 末尾统一重算 metrics 指标 + `repo_card.json` + level-1 属性，`summary` 仅在结构统计变化超阈值（模块数 ±10% 或新增顶层包）时重生成；一致性窗口 = 一次全量 build 间隔（无热更新），已诚实标注（依据 P1）。② **死代码/没人用（A7）** → **砍除（不适用）**：原稿即标「非属性、附录 A7 查询」，本仓无 text2cypher/`query_graph` 承载；可选 v0.3 以 `fan_in=0 ∧ 非 entrypoint` 出报告，损失属查询层缺位的已知边界。

### 3.2 §5 查询期流水线

| 原稿机制 | 决策 | 具体改法 / 损失 | 依据 |
|---|---|---|---|
| §5.1 S0 规范化 | **原样采纳** | 新增 `router.normalize()` 纯函数（全半角统一、保留标识符大小写、反引号内标强代码词元），网关检索前调一次。 | P2 |
| §5.2 规则路由器 + `router_rules.yaml` | **改造采纳** | 无 YAML 解析器（`pyyaml` 是第三方）。规则表用 **Python 字面量**放 `src/repograph/retrieve/router.py`（与 `config.endpoint_patterns` 的「tuple of 正则」同构先例）；需热改则旁挂 `router_rules.json`（stdlib `json`）。**确定性规则**在 RepoGraph 侧 `router.route()`，**LLM 兜底分类**在网关侧复用 `_rg_llm`（Q4 底座）——沿用「L1 确定性在 RepoGraph、L2 LLM 在网关」既有职责边界。置信度<0.6 归 `global`（误路由代价不对称）。 | P2 |
| §5.3 S2 改写与扩展（一次调用出 queries/symbol_guesses/premises） | **改造采纳** | 从 `_rg_llm_select_concepts` 抽公共底座 `_rg_llm_json(cfg,system,user)`（现有函数改为调它，行为不变、回归可保绿）；新增 `_rg_llm_rewrite`。产物**只回灌** `link_entities`/`topic_recall`，`symbol_guesses` **永不进答案事实**（防污染，与现有隔离规则一致）。 | P2/P5 |
| §5.3 HyDE 变体 | **砍除** | 依赖 embedding 检索，无向量层则生成的假设文本无处可用；原稿本就标「默认关闭、A/B 后定」。损失：零（从未启用）。 | — |
| §5.4 S3 链接 v2「双语卡片 embedding top-5」 | **改造采纳** | 候选来源=`link_entities`（别名精确/后缀）∪ 缩写扩展命中 ∪ **BM25-over-实体卡片**（topic 语料扩容后对原问题与全部改写 query 分别 `topic_recall` 取并）。**阈值不可直接落地**（见 §4.6）：原稿 τ_hi=0.62/τ_lo=0.45/δ=0.05 假设 embedding 余弦，本图是整数档+无上界 BM25 分，须按「方法档+BM25 边际」重定义。 | P2/P3 |
| §5.5 S4 消歧协议 | **原样采纳** | `link_entities` 已产带 `score`/`method` 的候选（`context.py:157`）；新增纯函数 `disambiguate(candidates)`：方法档 exact/suffix 领先→自选并披露；多合法候选→`needs_disambiguation=true` 交调用方（上游 Claude Code）。澄清预算 1 次/查询。 | P3/P4/P5 |
| §5.6 S5 会话焦点栈（进程内） | **改造采纳** | 原稿「stdio 一进程一会话」前提错误：现实是 `ThreadingHTTPServer` 多会话 + 磁盘会话文件。焦点栈**持久化进 session JSON** 的 `rg_focus` 键（最多 5 条、`turn` 差判 TTL=10），读写各挂现有 `read_session`/`write_session`（`server.py:610/618`），零新增 I/O。退化为「免 LLM 的类型相容兜底」，主消解交给 S2 改写吃历史或上游展开「它」。 | P5 |
| §5.7 S7 前提校验 | **改造采纳** | premises 来自 S2 合并调用。校验**砍掉「向量高分支撑块」半支**（无向量），只保留「实体可链接但图中无支撑边」：premise 实体 `link_entities` 命中但相关边缺失→标 `premise_unverified` 注入。损失：docstring 提过但未成概念边的反例会漏判为 unverified，属可接受的保守偏差。 | P5 |
| §5.8 S6 回退阶梯 | **原样采纳** | 现瀑布 L3 概览兜底（`build_overview`「永不失联」）已是核心。形式化为显式阶梯：entity_local 空/低分→附 level-1 概览 + BM25 最近实体 Top-3（标 low-confidence）+ 建议问法；仍无→概览。裸拒率 0 现状已达成。 | P4 |

### 3.3 §6 生成期约束 & §7 接口契约

| 原稿机制 | 决策 | 具体改法 / 损失 | 依据 |
|---|---|---|---|
| §6 披露四条（边界三段式/代理披露/消解回显/前提处理） | **原样采纳** | `_rg_inject_prefix`（`server.py:525`）已按 mode 分前缀；追加三段式边界声明、代理定义披露、`resolved_query` 回显——往注入 system 文本加固定话术，无结构改动。改写产物以 `[检索辅助,非事实]` 段隔离（现有 L2 已有先例）。 | P4/P5 |
| §7.1 `ask_repo` 响应 schema v2 | **改造采纳** | 无工具返回值。映射为两路：(a) SSE `repo_context` 事件扩字段（`server.py:1016` 现发 `{mode,linked,stats}`，补 `resolved_query/needs_disambiguation/candidates[]/premise_flags[]/degraded/suggestions[]`）；(b) system 注入段写 `resolved_query`/披露话术。v1 兼容天然满足（纯增字段）。 | P5 |
| §7.2 `repo_overview` MCP 工具 | **砍除（能力保留）** | 无 MCP。能力由 **meta/global 路由的注入**实现——「你了解这个项目吗」命中 meta 规则→注入 `repo_card.json`。损失：上游 Agent 无法「主动按需」拉概览（只能靠路由推），但 push 模型下这是必然，且元问题正是路由最易判准的一类。 | P1/P2 |
| §7.1 `ask_repo` 新增 `context: list[str]` 参数 | **砍除（被超越）** | 网关本就持有 `sess["messages"]` 全历史，`_rg_llm_rewrite` 可直接吃最近 N 轮做指代消解，优于传参。损失：零。 | P5 |
| §7.3 `impact_analysis` 模糊输入行为 | **原样采纳（部分平移）** | `impact._resolve_symbol` 已实现精确→唯一后缀→歧义（附 candidates），**遇歧义在 `_impact_calls` 遍历前即 return `{error:ambiguous,candidates}`（`impact.py:42/47`），P3 行为已满足**；`_build_symbol_context` 调 `impact_analysis` 前已用 `link_entities` 消解。缺口仅「多候选以 `needs_disambiguation+candidates` **响应格式**呈现」（非遍历行为）。原稿的 `query_graph 不变` 在本仓**无对象**——`query_graph`/text2cypher 不存在，属不适用。 | P3 |
| 主文档 pgvector/chunk/embedding 向量层 | **砍除（已缺席）** | 现四层瀑布已替代，确认不引入。损失：无语义近邻召回，长尾口语靠 BM25+改写补，是本图的**已知能力上界**。 | — |

---

## 4. 落地架构：「路由器 + 瀑布」二层结构

### 4.1 二层结构总览

原稿的七阶段流水线（S0–S7）在本图落为**两个物理层**，跨 RepoGraph 与网关：

```text
                       ┌──────────────── 网关 server.py（查询期，push 注入）────────────────┐
用户问题 → S0 normalize │ → S1 路由器 route() ─┬─ meta ───────→ 注入 repo_card.json（零检索）        │
（router.normalize）    │   (router.py 规则     ├─ global ─────→ build_overview + level-1 概览属性     │
                        │    + 网关 _rg_llm     ├─ entity_local → 现四档瀑布（见 4.3）                 │
                        │    兜底分类)          ├─ structural ─→ 降级 entity_local/topic（模板层未建）  │
                        │                       └─ out_of_scope → 界外声明（answer_general 配置）        │
                        │ 任一路径失败 → S6 回退阶梯（build_overview 恒兜底，裸拒率 0）                 │
                        │ 全程：S5 焦点栈（rg_focus）按需介入；S2 改写 + S7 前提校验合并一次 flash 调用 │
                        └──────────────────────────────────────────────────────────────────────────────┘
                              ↓ 结果经 SSE repo_context 事件（v2 扩字段）+ system 注入段 回传前端
```

**关键点**：S1 路由器是**新增的前置分诊层**；现有 `build_repo_context` 的四层瀑布**不消失**，而是降为 `entity_local` 与 `global/meta` 路由**之下的实现被调度**。这是「升级」而非「重写」。

### 4.2 S1 路由器（新增 `src/repograph/retrieve/router.py`）

- **形态**：模块级 `list[dict]` 规则表，每条 `{id, label, pattern(编译 re), requires:[...]}`。纯函数 `route(question, linked, has_code_token) -> (label, rule_id|None)`。信号：`has_code_token`=新增 `is_code_token` 检测器命中；`no_linker_hit`=`link_entities` 返回空；`no_repo_reference`=**组合谓词**（`no_linker_hit ∧ topic_recall 全部 <_MIN_SCORE ∧ 无指代词 ∧ 无反引号代码`，**不是**仅字面无「本项目」——否则「什么是适配层」这类仓库内概念会被 oos-1 误判界外）。**时序（消除 route↔linker 先后歧义）**：`normalize → link_entities`（廉价、确定性，`context.py:217` 现已无条件首跑）+ `has_code_token` 先算出 → 再调 `route(...)`；故 `no_linker_hit`/`no_repo_reference` 均为**链接后**信号，规则 `requires` 合法可判。meta/out_of_scope 命中后仍可零检索作答（弃用链接结果即可，纯内存扫描代价可忽略）。
- **五标签映射到现有 mode**（`_rg_normalize_mode`/`_rg_inject_prefix` 已透传 `symbol/topic/llm/overview/none`）：
  - `meta` → 注入 `repo_card.json`，事件 `mode="meta"`（前端并入 overview 类展示）；**冷启动/卡片缺失或损坏时降级现场 `build_overview` 统计并置 `degraded=true`，绝不因缺文件裸拒**；
  - `global` → `build_overview` + level-1 概览属性，`mode="global"`；
  - `entity_local` → 现四档瀑布（4.3），事件 `mode∈{symbol,topic,llm}`；
  - `structural` → 模板/text2cypher 层**未建**：事件仍报 `route_label=structural`（不被 topic 吞没，保 P2 可评测）、`degraded=true`、`suggestions[]` 指向可答方向；**能确定性计数者（模块/函数/概念总数等）先走 `build_overview` 字段作答**，仅无法精确计数者才降级 topic 模糊召回（损失见 §8 F8）；
  - `out_of_scope` → 界外声明 + **≥1 条本仓库可答的建议问法**（P4 底线，不得裸拒）、`degraded=true`；`answer_general`（`config.json` 布尔项，由 `server.api_chat` 界外分支读取、无新服务）默认 false 时仅声明+建议，true 则附常识作答并标注非仓库知识。
- **确定性规则在 RepoGraph、LLM 兜底在网关**：规则全不中且 `semantic_mode=='llm'` 时，网关用 `_rg_llm_route`（复用 `_rg_llm_json` 底座）发固定标签集分类（温度 0、≤30 token、按规范化问题缓存）。路由决策（规则 ID 或兜底标签+置信度）随 `repo_context` 事件透传 `route_source`/`confidence`，是 §6 路由准确率指标的数据源。
- **首个必修回归点**：`_is_meta_question` 的 `_META_MARKERS`（`context.py:444`）未覆盖「破仓库/干啥/晓得」等口语——实测 L0-02「你晓得我这破仓库是干啥的不」在 v0.1 误路由到 `topic`。meta 规则正则（附录）+ 兜底分类须修此洞。

### 4.3 四档瀑布如何挂到 entity_local 之下

`entity_local` 路由命中后，**沿用 `build_repo_context` 现有级联**（`context.py:216–233`），仅把隐式判定显式化：

```text
entity_local:
  L0 符号  link_entities 命中 → _build_symbol_context（影响面+提交+概念）  mode=symbol
  ├ 无命中 → S2 改写扩展（网关 flash 一次）→ 二次 link_entities / topic_recall
  L1 主题  topic_recall 命中概念 → 沿 IMPLEMENTS/DESCRIBES 展开          mode=topic
  L2 受限  仍空且 semantic_mode==llm → _rg_llm_select_concepts 选概念展开  mode=llm
  L3 兜底  仍空 → build_overview（永不失联）                              → 交回 S6
```

现有网关 L2 触发条件（`server.py:1000`：`mode in ("overview","none") and semantic_mode=='llm' and gateway_base(cfg)`）**正是 S2 该介入的点**。把「L2 直接选概念」扩成「先 S2 改写→二次链接/主题→仍无→再退回选概念」，复用同一网关往返预算（稳态仍 1 次廉价调用+缓存）。

### 4.4 S2 改写 + S7 前提校验合并为一次网关调用

- 新增 `_rg_llm_rewrite(cfg, question, recent_turns) -> {queries[], symbol_guesses[], premises[]}`（附录 B prompt 骨架），`recent_turns` 取自 `sess["messages"]`。**改写与前提抽取合并同一次 flash 调用**（原稿 §5.3/§5.7 即如此设计），控制延迟。
- 程序侧约束：`queries` 截 2–4 条（中英各 ≥1）、`symbol_guesses` 截 5、`premises` 允许空。
- 回灌：`queries`/`symbol_guesses` 喂 `link_entities`+`topic_recall`（RepoGraph 侧纯函数）；`premises` 逐条做图边存在性校验，无支撑→`premise_unverified` 注入生成上下文，生成层强制先纠正后答。

### 4.5 消歧协议与焦点栈的数据流

```text
读取（检索前）：问题含指代词(它/这个/该/上面) 且 link_entities 无锚
              → read_session(sid).rg_focus 取最近类型相容实体作种子 → resolved_query 回显
消歧（链接后）：disambiguate(candidates)
              → 强候选自选：resolved_query 披露「按 X 解读，另 N 个同名已忽略」
              → 多合法候选：needs_disambiguation=true + candidates[]（附 path/doc_head/fan_in）交上游
写入（锚定后）：强锚（method∈{exact,suffix} 或 topic 高分概念）压栈 rg_focus
              → 就近挂在追加 assistant 消息的 write_session 之前，零额外落盘
```

- **`rg_focus` 结构冻结**：`sess["rg_focus"] = [{entity_id, label, turn}]`（最多 5 条）；`turn` = 该实体锚定时的 assistant 消息序号（`len(sess["messages"])` 快照，独立自增）；过期判定 `当前 turn − entry.turn > 10` 即弹出。读写各挂现有 `read_session`/`write_session`（`server.py:610/618`），零新增 I/O。
- **非 llm 档（lexical/off）的 P5 保底**：无 S2 改写时，指代若焦点栈也未命中 → `resolved_query` 原样回显问题 + `suggestions` 提示「请点名具体实体/符号」，**禁止静默错锚**（宁可不消解，也不乱消解，守 P5）。

### 4.6 分数标度对账（最隐蔽的落地陷阱，单列）

原稿 τ_hi=0.62/τ_lo=0.45/δ=0.05 假设 embedding 余弦 ∈[0,1]。**现实两套分数都不是这个标度**：`link_entities._SCORE` 是整数档 `{100,80,60,40,30}`；`topic_recall` 是无上界 BM25 分（`_MIN_SCORE=1.0` 起，实测常见 1–10）。分带**按方法档 + BM25 边际**重定义：

- **自动锚定**（原 s≥τ_hi）：`method∈{exact_qualname, suffix_qualname}` 或词面精确命中；
- **进消歧**（原中段）：`method∈{short_name, concept_name}` 且多候选，或 topic Top-2 相对边际 `(s1-s2)/s1 < 0.15`（取代绝对 δ）；
- **判无锚**（原 s<τ_lo）：`link_entities` 空 且 `topic_recall` 全部 <`_MIN_SCORE`。
- **单一弱候选（补 §3.2 S4 的分带缝隙）**：只有 1 个 `short_name`/`concept_name`/`bm25_card` 候选且无更强档 → autopick 但置 `degraded=true` + `resolved_query` 披露「按 X 解读（弱匹配）」，不进消歧（本图真歧义仅 2 组，over-ask 面小）。
- **BM25-over-实体卡片候选的落地 schema（补 §3.1 双语 / §3.2 S3 的悬空集成）**：`topic_recall` 扩收 Function/Class 后，命中项 `{node_id,label,score,matched_terms}` 映射为链接候选 `{entity_id=node_id, score=BM25 分, method='bm25_card'}`，按 `entity_id` 与 `link_entities` 去重（同 id 时 `link_entities` 方法档优先），优先级恒**低于** exact/suffix；这类候选**只作锚定/消歧输入，不再触发 Concept 的 IMPLEMENTS 展开**（避免与 topic 档重复）。
- **AMB 消歧的整数分差**：`δ_score=20`，使 `recover`(fn 100 vs mod 30) 判 autopick、`invoke`(6×60 并列) 判 disambiguate。
- §8.4 校准三元组随之从 `(τ_hi,τ_lo,δ)` 换为 `(topic 相对边际, 短名档是否强候选, min_score)`；校准流程（FZ-dev 网格→FZ-test 冻结）不变。

**不处理这条，原稿阈值会被当余弦直接套到整数/BM25 分上，全盘失效。**

### 4.7 文件级改动清单（本轮只设计，不改码）

**新增文件**：
- `src/repograph/metrics.py`：§4.3 全部索引期指标 + 幂迭代 PageRank（纯 stdlib，图建成后 `build.py` 调一次）。
- `src/repograph/retrieve/router.py`：`normalize()` + 规则 `route()` + `is_code_token()` + 纯函数 `disambiguate(candidates)`（§3.2 S4）+ `verify_premises(store, premises)`（§5.7 图边存在性校验）+ `merge_link_candidates(...)`（link ∪ 缩写 ∪ bm25_card 去重，§4.6）+ S6 回退阶梯组装函数。
- （可选）`src/repograph/retrieve/router_rules.json`：规则热改载体（替代 yaml）。
- `src/repograph/extract/llm_client.py`：**索引期语义通道（2026-07-22 变更：grok CLI 订阅到期 402 断供，切换为阿里网关 HTTP 直连）**——stdlib `urllib` POST 网关 `/v1/messages`（与 `server.py _rg_llm_json` 同款传输：`anthropic_base_url` + `anthropic_auth_token` 读 claude-ui `config.json`、`anthropic-version` 头、非流式、绝不回显 token），模型 `qwen3.8-max-preview`；原 grok `--json-schema` 的结构化职责改由 **prompt 内嵌 schema + 程序校验**承担（`semantic.py` 既有候选白名单 + quote-substring 校验不变——体系本就不信任模型输出，换模型不降防线）。`grok_client.py` 保留为可选后端并标注断供，不删。
- `output/repo_card.json`：level-0 卡片缓存——由 `build.py` 在图建成后调 `metrics` + `llm_client` 产出并写盘；语义调用失败时只落确定性字段（与 `cli.py:261` 既有「语义层未就绪降级为告警」一致），meta 路由仍可作答。

**扩展现有（只读设计）**：
- `models.FunctionFacts`：加 `cyclomatic`；落图后 Function 节点加 `fan_in/pagerank/heat/blast_radius/fix_involvement/churn_90d/zh_aliases`。
- `ast_extractor._ScopeVisitor._enter_function`：圈复杂度顺带计数。
- `context.build_overview`：补 `entrypoints`/`hot_functions(heat)`/`summary` 注入。
- `context._tokenize`：缩写双向扩展 + 停用过滤。
- `context.link_entities`：Function/Class 纳入 `zh_aliases` 别名匹配。
- `topic._corpus_nodes`/`_doc_text`/`_DOC_LABELS`：可选收 Function/Class 中文卡片进 BM25 语料；`topic.zh_terms` 停用/疑问词过滤。
- `server._rg_llm_select_concepts`→抽 `_rg_llm_json` 底座；新增 `_rg_llm_rewrite`/`_rg_llm_route`。
- `server._rg_normalize_mode`（`_RG_VALID_MODES` 加 meta/global）、`server._rg_inject_prefix`（`server.py:525` 加 meta/global 前缀分支，否则 fall through 到 symbol 前缀语义错配）。
- `server.api_chat`：路由分派 + 焦点栈读写 + `repo_context` 事件扩字段（route_label/route_source/confidence/premise_flags/…）+ premise 纠正闸门 + oos 建议模板。
- `server` session schema：加 `rg_focus` 键（结构见 §4.5）。
- 前端 `web/app.js`：`renderRepoRef` 扩 mode 展示（见 §5.3）。

**砍除清单（明确不做）**：HyDE；pgvector/chunk/embedding 向量层（含 level-1 进向量、实体卡片 embedding、link v2 的 embedding top-5、S2 的 HyDE）；`repo_overview` MCP 工具形态（能力转注入）；`ask_repo` 的 `context` 参数；`symbol_alias` SQL 表（转节点属性）；`router_rules.yaml`（转 Python/json）。

---

## 5. 接口契约：repo_context v2

### 5.1 SSE `repo_context` 事件扩字段

现状 `server.py:1016` 发 `{mode, linked, stats}`。v2 扩为（**全部可选，v1 消费方兼容**）：

```json
{
  "route_label": "meta | global | entity_local | structural | out_of_scope",
  "mode": "meta | global | symbol | topic | llm | structural | overview | out_of_scope | none",
  "route_source": "rule:<id> | llm | fallback:<reason>",
  "confidence": 0.9,
  "resolved_query": "系统对问题的最终解读（含消歧/指代消解）",
  "linked": [{"entity_id": "…", "score": 100, "method": "exact_qualname"}],
  "needs_disambiguation": false,
  "candidates": [{"entity_id": "…", "path": "…", "doc_head": "…", "fan_in": 12}],
  "premise_flags": [{"claim": "…", "status": "unverified"}],
  "degraded": false,
  "suggestions": ["可以问：修改 X 会波及哪些端点"],
  "stats": {"symbols": 0, "topics": 0, "impact_callers": 0, "commits": 0, "concepts": 0}
}
```

- **两个命名空间必须分开（否则评测放水 + 前后端解析分叉）**：`route_label` 是 S1 五分类（meta/global/entity_local/structural/out_of_scope），是 §6.2 路由准确率的**唯一**判定基准（精确匹配，不取等价并集——否则 meta↔global、topic↔llm 互错会被判对而放水）；`mode` 是**事件展示态**（`build_overview` 内部恒返回 `overview`（已核 `context.py:744`），meta/global 路由在网关侧据 `route_label` 改写事件 `mode`，entity_local 落为 `symbol/topic/llm`，兜底为 `overview`，off/无上下文为 `none`）。落地须同步扩 `server._rg_normalize_mode` 的 `_RG_VALID_MODES`（现仅 symbol/topic/llm/overview/none，`server.py:534/539`）与 `_rg_inject_prefix`（`server.py:525` 现只分 overview/topic·llm/symbol 三支，须补 meta/global 前缀，否则 fall through 到 symbol 前缀语义错配）。前端可用等价集合（overview 类={meta,global,overview}）合并视觉，但**不参与准确率判定**。
- `resolved_query`/披露话术**同时**写进 system 注入段（供上游模型据以措辞）与事件（供前端标注），双通道。

### 5.2 system 注入段（`_rg_inject_prefix` 之上追加）

按 mode 前缀后追加固定话术块：三段式边界声明（最接近信息 + 明确边界 + 建议问法）、代理定义披露（凡用 §4.3 指标——把「核心/热点/危险」等模糊谓词的度量定义交还用户，这一条等价于原稿把指标释义单列的「第五条披露」，此处并入代理披露不另设）、`resolved_query` 首句体现解读、`premise_unverified` 前提先纠正。改写产物以 `[检索辅助,非事实]` 段隔离，引用规则禁引。

- **out_of_scope 的 P4 底线（不得退化为裸拒）**：界外响应不得是孤立的「这超出仓库范围」，必须＝界外声明 + ≥1 条本仓库可回答的建议问法（指向确定性能答的方向），并置 `degraded=true`、`suggestions[]` 非空；纳入 §6.2 裸拒率扫描的合法例外白名单。
- **premise_unverified 的程序化闸门（非仅靠 prompt 自觉）**：前提校验结果**必然**落 `premise_flags` 事件（P5 可观测）；网关在 `premise_flags` 非空时注入固定前缀「⚠ 前提未获图谱证据：<claim>；据图谱实际：…」，要求先纠正后答、不放行自由生成。§6.2 的 PP 幻觉率硬门禁以此结构 + 程序负样式扫描为准（见 §6.2），不单靠 LLM 裁判。

### 5.3 前端 `app.js` mode 展示扩展

`renderRepoRef`（现按 5 mode 渲染）扩展：
- `meta`/`global` 复用 overview 视觉档，标签文案区分「仓库卡片」/「全局概览」；
- `needs_disambiguation=true` 时渲染候选列表卡片（path + doc_head + fan_in），提示上游可继续追问；
- `premise_flags` 非空时高亮「前提待核实」；`degraded=true` 显示 low-confidence 标记 + `suggestions` 建议问法；
- `resolved_query` 作为「系统这样理解你的问题」一行回显，是 P5 的前端落点。

---

## 6. 评测方案（并入 `design_work/eval-design.md` 真实题目）

> **判定分两类，严禁混为一谈**：① **程序门禁集**（`route_label`/`mode`、anchor hit@k、`needs_disambiguation`、`premise_flags`、裸拒/幻觉负样式）——纯 stdlib 脚本对真实 `output/graph.json` 可断言、可复现，进硬门禁；② **裁判报告集**（答案准确率、前提纠正质量）——LLM 裁判，仅作报告与人工复核、**不进硬门禁**（避免裁判漂移误杀发布）。全部题目、gold 已对真实 `output/graph.json` 实跑核对。语料：单仓库 `multi-agent-orch`。**语料真实统计**：Module 22 · Class 15 · Function 259 · Commit 75 · Concept 139；CALLS 352 · MODIFIES 533 · IMPLEMENTS 390 · DESCRIBES 104；design_decision 81/domain_concept 40/constraint 18。

### 6.1 四子集真实题目（共 48 题，全部落到真实图谱）

| 子集 | 题数 | 构造要点（实测约束） | 判定 |
|---|---|---|---|
| **L0 元问题** | 10 | C1 变体含口语+错别字（「你晓得我这破仓库是干啥的不」「你对这个带码库熟悉么」）；覆盖能力/规模/总览三问 | 纯程序：`route_label∈{meta,global}` 且**注入上下文（repo_card / build_overview 文本）**命中 `L0_FACTS` 规范事实表 ≥3（如 22/15/259/75/139、`_dispatch_group`、`适配层`）——判在**来源侧**而非自由生成答案侧，规避约数/措辞漂移致假阴；答案侧 `facts_hit` 仅作抽检报告 |
| **FZ 口语指称** | 20（dev/test 各 10） | **刻意与目标实体名零词面重合**（`zh_terms` 校验 20/20 通过）；gold 均为已核对真实 id/概念名 | `anchor hit@1/hit@3`（程序，放宽到 1 跳 IMPLEMENTS/DESCRIBES 概念展开）+ 答案准确率（裁判） |
| **AMB 歧义** | 10（3 消歧 + 7 自选） | **真歧义极稀缺**：全图函数短名碰撞组**仅 2 个**——`invoke`×6（适配器，唯一有意义者）、`__init__`×9（构造器）。**原稿假设的 `run`×9 在本图证伪**（run 短名唯一） | 程序（对 gold_behavior，非自定义重言）：系统「预测消歧」=`needs_disambiguation ∧ len(candidates)≥2`，「预测自选」=有 anchor 且非消歧；**过问率**=（预测消歧 ∧ gold 自选）/gold 自选数，**漏问率**=（预测自选 ∧ gold 消歧）/gold 消歧数，行为一致率=(TP+TN)/n |
| **PP 错误预设** | 8（6 技术缺席 + 2 结构矛盾） | **技术栈纯 stdlib**（sqlite3+http.server），无 Redis/FastAPI/PostgreSQL/Docker/Celery/React；反证充足。结构量：看门狗**三级**（非五）、混沌**50 轮**（非 100） | 裁判判「指出并纠正」；顺着预设答=幻觉，单列 |

**当前基线注记（衡量 v0.2 增量的核心）**：L0-02、FZ-d01（`_handle_terminate`）、FZ-d02、PP-01/02/06 在 v0.1 实测**均不达标**——L0 口语误路由到 topic、FZ 召回到邻近非 gold 概念、PP 只给概览不主动纠错。这些正是 meta 路由、§4.2 双语卡片 + §4.4 改写（`_rg_llm_rewrite`）、§3.2/§4.4 前提校验（S7）要拉升的点。**Concept 别名近乎全空**（139 里仅 1 个带 alias），故 FZ 召回压力几乎全压在 BM25，是天然难集。

### 6.2 指标门禁

| 指标 | 目标 | 归属 | 性质 |
|---|---|---|---|
| 路由准确率（`route_label` 五分类**精确**匹配人工标注，**不取等价并集**） | ≥ 0.90 | 全集 | 回归门禁 |
| 概览能力命中率（{meta,global,overview} 并集，仅诊断非门禁） | 报告 | L0 | 附属 |
| L0 事实达标率（`route_label∈{meta,global}` 且注入上下文命中 `L0_FACTS`≥3，见 §6.1） | ≥ 0.90 | L0 | — |
| anchor hit@1 / hit@3 | test 实测报告，dev 上优化 | FZ | 报 v0.1→v0.2 增量 |
| 过问率 / 漏问率 | ≤ 0.20 / ≤ 0.10 | AMB | — |
| 预设纠正率（裁判·报告） / **预设幻觉率（程序负样式）** | ≥ 0.75 / **0** | PP | **幻觉率=0 硬门禁**：程序扫描答案是否肯定断言缺席技术栈（`Redis/FastAPI/PostgreSQL/Docker/Celery/React` 词表）或错误结构常量（看门狗五级 / 混沌 100 轮）；命中即幻觉。裁判仅辅助 |
| **裸拒率**（程序负样式：assistant 全文既无最近信息又无建议，且 `repo_context.suggestions` 空、`degraded` 未提供出路；oos 合法模板列例外白名单） | **0** | 全集 | **硬门禁**（现状 L3 兜底已达成） |
| 澄清开销（平均每题消歧触发） | ≤ 0.15 | 全集 | — |

主表增「v0.2（本设计）」列与 v0.1 同题对比；L1/L2/L3 老集允许持平，**下降 >3pt 视为回归、阻断发布**。

### 6.3 阈值校准

FZ-dev 10 题网格搜 `(topic 相对边际, 短名档强候选判定, min_score)`（§4.6 替换后的维度），目标 max hit@1、约束 过问率≤0.2；冻结后跑 FZ-test 与 AMB，校准/测试严格不混用；网格全表 + `_SCORE 带→分带` 映射入 `eval/calibration.md`。

---

## 7. 实施分期 P0–P3

> 工作量按 stdlib+网关注入现实重估（原稿按理想架构估，此处调整）。插入主计划 W3 之后、W4 评测之前。

| 阶段 | 内容 | 工作量 | 验收标准 |
|---|---|---|---|
| **P0** 最小可用 | level-0 卡片（`build_overview` 补 summary/entrypoints）+ meta 路由（补 `_is_meta_question` 口语盲区）+ 回退阶梯形式化 | 0.5–1 天 | 「你知道我的代码库吗」返回卡片作答；全链路裸拒率 0；L0-02 等口语题不再误路由到 topic |
| **P1** 路由与双语 | `router.py` 全量（规则+网关兜底分类）+ S2 改写扩展（`_rg_llm_json` 底座+`_rg_llm_rewrite`）+ 双语卡片（网关语义通道中文描述→BM25 语料扩容+`zh_aliases`）+ 缩写表 + level-1 包概览属性 | 2–3 天 | 附录规则单测全过；**FZ-dev hit@3 ≥ 0.8**（不达标先修卡片/语料再动阈值）；路由准确率 dev 达标 |
| **P2** 指标与消歧 | `metrics.py`（幂迭代 PageRank + fan_in/heat/blast_radius/fix_involvement/churn）+ AST 期圈复杂度 + 消歧协议 + 焦点栈（`rg_focus`）+ schema v2 事件扩字段 | 2 天 | AMB-dev 行为一致率 ≥ 0.8；`impact_analysis` 模糊输入符合 §3.3；谓词排序题能披露代理定义 |
| **P3** 评测与校准 | 四子集入 `eval/dataset.jsonl` + 阈值校准（`calibration.md`）+ 报告并入主表 v0.2 列 | 1.5 天 | §6.2 全指标出数；**硬门禁（幻觉率=0、裸拒率=0）达标，否则阻断 v0.2 tag** |

依赖：P1 依赖 P0 路由骨架；P2 与 P1 后半可并行；P3 依赖全部。

---

## 8. 风险表

> 继承原稿 F1–F7 中仍适用者，注明因架构差异**消失**或**新增**的风险。

| # | 风险 | 状态 | 对策 |
|---|---|---|---|
| F1 | 改写产物污染答案（检索辅助词被当事实） | 继承 | 改写只进检索通道 + `[检索辅助,非事实]` 隔离段 + 引用禁引；PP 幻觉率=0 为门禁 |
| F2 | 中文卡片/概览 summary 幻觉 | 继承 | 语义通道输出专名白名单校验（禁止出现未在输入中的专名）+ 抽检 30 条 ≥85% 可信；索引幂等，不达标改 prompt 重跑 |
| F3 | 路由规则腐化（新问法绕过规则打错分支） | 继承（更突出） | 规则表 Python/json 配置化 + 路由日志周期回看 + L0/路由准确率入回归门禁；口语盲区已由 L0 实测暴露，须持续补正则 |
| F4 | 过度澄清损害体验 | 继承（**减弱**） | 本图真歧义仅 2 组（`invoke`/`__init__`），过度澄清面天然极小；澄清预算 1 次/查询 + 过问率≤0.2 压制 |
| F5 | 焦点栈误消解 | 继承（**变形**） | 从「进程内串味」变为「session 文件隔离」，多会话串味风险**消失**；仅显式指代词触发 + 必回显 + 上游历史优先 |
| F6 | 模糊谓词代理被当客观结论传播 | 继承 | 披露话术为生成层硬规则；裁判 rubric 中「未披露代理」记 partial |
| F7 | 阈值在小校准集上过拟合 | 继承（**维度变更**） | 校准维度按 §4.6 换为「方法档+BM25 边际」；校准/测试严格分离，网格全表公开，声称范围限「本仓库语料」 |
| **F8（新增）** | **structural 路由降级损失**：模板/text2cypher 层未建，结构化计数题（「列出所有端点」）降级到 topic，精度损失 | 新增 | 能定量的计数题**先走 `build_overview` 确定性字段**（不进 topic），仅无法精确计数者降级 topic 且事件保 `route_label=structural`+`degraded=true`；**已知折中并披露**：structural 无索引期答案素材（局部让渡 P1）、降级路径与路由标签**分离**回显（守 P2 可评测）；text2cypher 列入 v0.3，不阻断 v0.2 |
| **F9（新增）** | **无向量层的召回上界**：长尾中文口语在 BM25+改写后仍可能 miss（FZ 天然难集，Concept 别名近乎全空） | 新增 | §4.2 双语卡片入 BM25 语料 + `zh_aliases` 是主要补偿；FZ hit@k 如实报告 v0.1→v0.2 增量，不承诺向量级召回 |

---

## 附录 A：路由规则初始表（Python 字面量形态，替代 `router_rules.yaml`）

> 落 `src/repograph/retrieve/router.py` 模块级常量，按序首中即出；全不中 → 网关 `_rg_llm_route` 兜底分类。正则须补 `_is_meta_question` 暴露的口语盲区。

```python
ROUTER_RULES = [
    {"id": "meta-1",   "label": "meta",
     "pattern": r"(你|您)?(知道|了解|认识|熟悉|清楚|晓得|懂|读得?懂)\s*(我的|这个|这)?(破)?(代码库|仓库|项目|工程|repo|codebase)"},
    {"id": "meta-2",   "label": "meta",
     "pattern": r"你(是谁|能(干|做)(什么|啥|嘛)|有(什么|哪些)(功能|能力))|怎么用你|帮我看(代码|仓库)"},
    {"id": "struct-1", "label": "structural", "requires": ["has_code_token"],
     "pattern": r"(最|前\s*\d+|多少|几个|统计|列出(所有|全部)|排(序|名))"},
    {"id": "entity-1", "label": "entity_local", "requires": ["has_code_token"]},
    {"id": "global-1", "label": "global", "requires": ["no_code_token", "no_linker_hit"],
     "pattern": r"(整体|总体|全局|大概|架构|介绍|讲讲|是(干|做)(什么|嘛|啥)|干啥|质量|难点|亮点|风格|规模|多大)"},
    {"id": "oos-1",    "label": "out_of_scope", "requires": ["no_repo_reference"],
     "pattern": r"(是什么意思|什么是)\s*(?!.*(本项目|这个项目|仓库))"},
]
# 规则表每次增删必须附对应回归用例（F3）。
```

**规则语义补注（落地必读，回应两处过宽/误判风险）**：
- `entity-1` 无 `pattern`、仅 `requires:[has_code_token]`，是**含代码词元问题的默认桶**（按序在 struct-1 后、global-1 前，故「含符号的总览/界外」会先落 entity_local）——**有意设计**：误落总览题由 entity_local 自身的「无锚→S2 改写→仍空→`build_overview`」兜底纠偏（§4.3），误落界外题由链接空→概览兜底，均不违 P4。须附回归用例：`Foo 模块整体架构` 期望 entity_local 或 global 之一即算过。
- `oos-1` 的 `requires:[no_repo_reference]` **必须**按 §4.2 的**组合谓词**（`no_linker_hit ∧ topic_recall 全 <_MIN_SCORE ∧ 无指代 ∧ 无反引号`）判定，**不能**只靠 `什么是/是什么意思` 字面 + 无「本项目」——否则「什么是适配层」（`适配层` 是本仓 `L0_FACTS` 概念）会被误判界外。正则只作触发候选，最终由组合谓词把关。

## 附录 B：改写与前提抽取 prompt 骨架（`_rg_llm_rewrite` 用，冻结版随代码入库）

要点：角色（**检索查询改写器，不是回答者**）→ 输出 JSON schema（`queries` 2–4 条、中英各 ≥1；`symbol_guesses` ≤5；`premises` 抽取「问题所断言的事实」，无则空）→ 三条规则（不回答问题本身；口语词必须给技术域同义扩展；专名尽量取自仓库既有词汇——但**不硬性依赖模型自知「不可能存在」**：`symbol_guesses` 只作链接候选、链不上即弃、永不进答案事实，`premises` 走 §5.7 图边校验，故即便发明专名亦无害；可选把 Top-N 符号/概念白名单注入 prompt 做约束，与 `semantic.py` 现有白名单同法）→ 2 个 few-shot（一 C3 口语题、一 C7 含错误预设题）。走网关 `_rg_llm_json` 底座（`model_haiku`/flash、`gateway_headers`、`/v1/messages` 非流式），异常静默降级、绝不回显 token。

## 附录 C：mode 与回退关系速查

```text
meta          → 注入 repo_card.json                缺卡 → 现场 build_overview + degraded（不裸拒）
global        → build_overview + level-1 属性        空 → 卡片 + 建议问法
entity_local  → 三档检索(symbol→topic→llm)+概览兜底   无锚 → S2 改写二次链接 → 概览+近邻+建议
structural    → route_label=structural + degraded     能定量→build_overview；否则降级 topic → 概览兜底
out_of_scope  → 界外声明 + ≥1 建议问法 + degraded      P4 不裸拒；answer_general=true 附常识
```
