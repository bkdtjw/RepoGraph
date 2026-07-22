# RepoGraph v0.2 差距矩阵

**基线**：v0.1「四层瀑布」模糊语义处理（L0 符号 → L1 主题 → L2 LLM 受限概念链接 → L3 概览兜底），落在 `src/repograph/retrieve/{context,topic,impact}.py` + `claude-ui/server.py` 的 `_rg_*` 注入链 + `web/app.js` 的 `renderRepoRef`。
**对照**：v0.2 补充设计稿（`RepoGraph-模糊语义处理设计.md`）。
**状态取值**：已实现 / 部分实现 / 未实现 / 不适用。所有断言均经 Read/Grep 核对真实文件。

---

## 0. 基础设施现状（v0.2 假设存在、实则不存在者，硬事实）

| 基础设施 | v0.2 依赖点 | 真实状态 |
|---|---|---|
| 向量层 / embedding | §4.1 level-1 进向量、§4.2 双语卡片 embedding 可达、§5.4 S3 embedding top-5 候选、schema v2 `method='embedding'` | **不存在于活跃路径**。`store/ddl.sql:10,20,22` 声明 `vector` 扩展 + `chunk.embedding vector(1024)` + HNSW 索引，但明确标注为「AGE 部署形态预留」；`store/age.py` 对 embedding/chunk/vector **零读写**（grep 无匹配）；活跃后端 `models.GraphStore`（graph.json）无任何向量字段。检索层 `topic.py` 是纯词面 BM25-lite，非向量。 |
| `symbol_alias` 表（中文别名 `kind='zh_alias'`） | §4.2 中文别名入表、§5.4 别名候选 | **不存在**。仅 `ddl.sql:26-32` 作 AGE 预留 schema，`kind` 值域为 `exact\|suffix\|concept_alias`（`ddl.sql:29`），**无 zh_alias**；`age.py` 无读写。最接近现存物：`Concept.aliases` 字段（grok 概念对齐产物，多为英文/设计术语，`context.py:150,653` 读取），仅覆盖概念、不覆盖符号中文俗称。 |
| MCP 服务 | §7.2 `repo_overview`、§7.3 `ask_repo`/`impact_analysis`/`query_graph` 工具框架、"工具描述即路由提示" | **完全不存在**。`src` 全树无 `@mcp`/`FastMCP`/`import mcp`（grep 无匹配）。现实集成形态是 `claude-ui/server.py` 直接 import repograph 函数并注入聊天 system prompt（`server.py:981-1024`），HTTP 端点 `GET /api/repograph/status`（`server.py:558,926`），非 MCP 工具。 |
| networkx（PageRank 依赖） | §4.3 pagerank | **存在**（`viz/render.py:24` 已 import），基础设施可用，但未对 CALLS∪IMPORTS 图计算 PageRank。 |
| 模糊谓词指标属性 | §4.3 全部 | **全无**。节点无 `fan_in/pagerank/heat/cyclomatic/blast_radius/churn` 任一属性。可用原料：CALLS 边（`models.py:21`）、MODIFIES 边（`:22`）、FIXES 边（`:24`，Commit→Issue）、`Commit.authored_at`、`Module.loc`（`:131`）、`Function.is_endpoint`（`:95`）。 |
| 增量索引水位（§6.5） | §4.1 刷新策略 | **未实现**。`ddl.sql:35 index_meta` 为预留；活跃形态是离线全量 build graph.json，`server.py:304` 按 `(path,mtime)` 整体热失效重载。 |

---

## 1. §4.1 概览层

| 机制 | 状态 | 代码位置 / 证据 | 四层瀑布对应物 | 差距 |
|---|---|---|---|---|
| level-0 仓库卡片 | 部分实现 | `context.py:683 build_overview`：仓库名 + `counts()` 统计（模块/类/函数/提交/概念）+ 顶层模块（loc 降序 `:704`）+ 热点函数（MODIFIES 计数 `:716`）+ 核心概念（IMPLEMENTS 落点 `:728`） | L3 概览兜底 | 缺 `summary`（150-300 字 LLM 一次调用 + 专名白名单校验）；缺 `entrypoints`（is_endpoint 字段在但未聚合入口）；`stats` 缺 issues/time_span/call_resolved_rate/at_commit/generated_at；产出为拼装文本非 JSON 卡片；非常驻注入 system，仅在兜底档惰性生成。 |
| level-1 包级概览 | 未实现 | — | 无（build_overview 列的是模块级 docstring 首行直取，非包摘要） | 无顶层包 120 字 LLM 摘要；无 `source_type='overview'` chunk（**依赖不存在的向量层**）；无 Module 聚合节点属性；C2 检索空间隔离不成立。 |
| 刷新策略 | 未实现 | `server.py:304`（整体 mtime 热失效重载） | 换图谱即全量 reload | 无「随增量水位重算确定性字段」；无「summary 仅结构变化超阈值重生成」；增量水位本身（§6.5）v0.1 未落地。 |

---

## 2. §4.2 双语实体卡片与中文别名

| 机制 | 状态 | 代码位置 / 证据 | 四层瀑布对应物 | 差距 |
|---|---|---|---|---|
| 实体卡片中文功能描述（≤40 字 LLM 生成） | 未实现 | `context.py:330-351` 命中实体块仅用真实 qualname/signature/docstring 首行 | 无 | 无 LLM 中文描述；embedding 双语可达**依赖不存在的向量层**。 |
| 中文别名入表 `symbol_alias(kind='zh_alias')` | 未实现 | `ddl.sql:26-32`（预留 schema，kind 无 zh_alias）；`age.py` 无读写 | `Concept.aliases`（仅概念，非符号中文别名） | 无 symbol_alias 表落地；无核心模块/关键类中文俗称批量入表。 |
| 缩写扩展表（ctx→context 等，链接前双向扩展） | 未实现 | `context.py:55 _tokenize` 仅做点分后缀 + 1..3-gram，无缩写词表 | 无 | 完全缺失。 |

---

## 3. §4.3 模糊谓词指标预计算

| 触发词→代理 | 状态 | 代码位置 / 证据 | 四层瀑布对应物 | 差距 |
|---|---|---|---|---|
| 核心/重要 → `fan_in`,`pagerank` | 未实现 | CALLS 边可算入度（`models.py:21`）；networkx 在（`render.py:24`）但未算 PageRank | 无（无触发词路由） | 无预计算属性、无触发词→指标映射。 |
| 热点/改得最多 → `heat = commits_all + 2×commits_90d` | 部分实现 | `context.py:716-725` 按 MODIFIES 计数排热点函数（≈commits_all） | build_overview 热点函数 | 无 90d 窗口加权（authored_at 在但未做时间窗聚合）；无触发词映射；结果不落节点属性。 |
| 复杂/难懂 → `cyclomatic`,`loc` | 部分实现 | `loc` 是 Module 真实字段（`models.py:131`，build_overview 已用于排序）；cyclomatic 无 | loc 排序 | cyclomatic（1+分支节点数）完全未实现——v0.2 稿自标「P2 补入抽取器」，即承认未落地；无触发词映射。 |
| 危险/风险大 → `blast_radius`,`fix_involvement` | 部分实现 | `impact.py:126 impact_analysis` 按需沿反向 CALLS 求 ≤depth 闭包 + affected_endpoints（`:176`）——正是 blast_radius 的按需计算 | impact 反向闭包（单符号、查询期） | 不预存属性、不做全图排名、无触发词映射；fix_involvement 未实现（FIXES 是 Commit→Issue `models.py:24`，需 FIXES∩MODIFIES 关联，无此计算）。 |
| 没人用/死代码（A7） | 不适用 | — | 无 | v0.2 自标「非属性、主文档附录 A7 查询」；当前无 text2cypher/模板查询层承载。 |
| 不稳定/老在改 → `churn_90d` | 未实现 | MODIFIES 边 + authored_at 均在，但无 90d 聚合 | 无 | 无窗口聚合、无属性、无触发词映射。 |
| 披露话术模板（生成层强制） | 未实现 | `server.py:165-176` 注入前缀仅「优先据此回答并标注来源」 | 注入前缀 | 无「按 <代理定义> 排序…」强制话术；且指标本身不存在，无定义可披露。 |

---

## 4. §4.4 词元与停用

| 机制 | 状态 | 代码位置 / 证据 | 四层瀑布对应物 | 差距 |
|---|---|---|---|---|
| 中文停用/疑问词表（怎么/哪个/那块/搞的） | 未实现 | `topic.py:50 zh_terms` 对 CJK 全量 2/3/4-gram 滑窗，不剔疑问词 | 靠 IDF 低权 + `_MIN_SCORE=1.0`（`topic.py:36`）间接滤除，非显式停用表 | 疑问词照样成 gram 进 BM25；无显式停用/疑问词表。 |
| 代码词元检测器（camelCase/snake_case/点路径/后缀/#数字/路由样式） | 部分实现 | `context.py:48 _IDENT` 识别标识符链 + 点路径；`_tokenize` 兼容后缀/空格拼接 | `_IDENT`/`_tokenize`（链接用） | 无 `#数字`(#issue)、无路由样式(/api/x) 检测；只服务链接器，**不服务路由器（路由器不存在）**。 |

---

## 5. §5 查询期七阶段

| 阶段 | 状态 | 代码位置 / 证据 | 四层瀑布对应物 | 差距 |
|---|---|---|---|---|
| S0 规范化 | 未实现 | `context.py:96` 直接对原始 question 跑正则；Concept 匹配 `lower()`（`:154`） | 分散的大小写处理 | 无全半角统一、无反引号强代码词元标记、无集中去噪阶段。 |
| S1 路由器（规则表 + LLM 兜底 + 5 标签集 + 置信度<0.6→global + 路由日志） | 部分实现 | `server.py:211 _rg_semantic_mode`（off/lexical/llm 三档）+ build_repo_context 内部瀑布顺序（符号→meta→主题→概览 `context.py:216-233`）+ `context.py:452 _is_meta_question`（子串词表 `:444`） | semantic_mode 三档 + 瀑布 + meta 检测 | 隐式二元非显式多路（**P2 未达成**）；无 router_rules.yaml（附录 A）；无 `{meta,global,entity_local,structural,out_of_scope}` 分类；无 LLM 兜底分类；无置信度回退；无结构化路由日志；无 structural/out_of_scope 分支。 |
| S2 改写与扩展（queries/symbol_guesses/premises 合并调用） | 未实现 | — | L2 `_rg_llm_select_concepts`（`server.py:430`，功能是「选概念名」非「改写+猜符号+抽前提」） | 无改写 LLM 调用；无中英各一约束；无 HyDE；无 LRU 缓存；附录 B prompt 骨架未落地。 |
| S3 链接 v2（三段阈值带 τ_hi/τ_lo/δ + embedding/缩写/别名候选合并） | 未实现 | `context.py:31 _SCORE` 离散整数打分（100/80/60/40/30）取 top_k；`impact.py:22 _resolve_symbol` 有精确/唯一后缀/歧义判定 | link_entities 离散打分 + impact 歧义判定 | 无 τ_hi/τ_lo/δ 阈值带；无 embedding top-5 候选（**无向量层**）；无缩写扩展候选；无自动锚定/消歧/无锚三分带。 |
| S4 消歧协议（needs_disambiguation + 区分性 candidates + 唯一强候选自选披露 + 澄清预算） | 部分实现 | `impact.py:42,47` 多候选返回 `{"error":"ambiguous","candidates":[...]}` 不猜 | impact ambiguous 报错 | link_entities 反而直接取 top_k 全返回不消歧；无 needs_disambiguation 字段；candidates 无区分性元数据（路径/docstring/fan_in）；无 δ 分差触发；无澄清预算；无自选 resolved_query 披露。 |
| S5 会话焦点栈（最近 5 实体/TTL10 + 指代词触发 + 回显 + context 参数） | 未实现 | `server.py:986` 每轮用最新 user content 独立无状态检索 | 无 | 无焦点栈；无指代词（它/这个/该/上面）触发；无 resolved_query 回显；无 ask_repo `context` 参数（无 ask_repo）。跨轮指代完全依赖调用方展开。 |
| S7 前提校验（premises 验证 + premise_unverified 标记 + 生成层纠正） | 未实现 | — | 无 | 无 premises 抽取（S2 不存在）；无图/向量支撑校验；无标记；生成层无纠正规则；C7 错误预设当前无任何处理。 |
| S6 回退阶梯（永不裸拒 + level-1/近邻 Top-3/建议问法 + 二次链接 + oos 声明） | 部分实现 | `context.py:192,230` 瀑布兜底（符号空→meta→主题→L3 概览永不失联）；`server.py:1000` L2 在 overview/none 补一层 | 四层瀑布兜底 + L2 补齐 + mode=none | 「永不裸拒」P4 部分达成（overview 兜底）；缺 level-1 概览、embedding 最近实体 Top-3、suggestions 建议问法；无 out_of_scope 分支/answer_general 配置；无二次链接（改写不存在）。 |

---

## 6. §6 生成期四条约束

| 约束 | 状态 | 代码位置 / 证据 | 四层瀑布对应物 | 差距 |
|---|---|---|---|---|
| 边界声明三段式（最接近信息 + 明确边界 + 可行动建议） | 未实现 | `server.py:1011` mode=none 时干脆不注入，由基础模型自由发挥 | 注入前缀 | 无三段式强制约束。 |
| 代理披露（内联披露代理定义） | 未实现 | — | 无 | 无指标故无可披露；无硬规则。 |
| 消解回显 `resolved_query`（首句体现解读） | 未实现 | 响应事件 `{mode,linked,stats}`（`server.py:1016`）无 resolved_query | 无 | 无指代消解/消歧自选/改写回显。 |
| 前提处理（premise_unverified 先纠正后答） | 未实现 | — | 无 | 依赖不存在的 S7。 |
| 改写产物隔离段 `[检索辅助,非事实]` | 未实现 | — | 无 | 无改写产物，故无隔离段。 |
| （现存）来源标注要求 | 部分实现 | `server.py:165-176` 三种注入前缀均要求「在引用处标注来源」 | 注入前缀 | 是 §6 精神的最小雏形，但非「答案+边界+出路」三件套。 |

---

## 7. §7 接口契约变更

| 机制 | 状态 | 代码位置 / 证据 | 四层瀑布对应物 | 差距 |
|---|---|---|---|---|
| 7.1 响应 schema v2 | 部分实现 | `server.py:1016` repo_context SSE 事件 = `{mode,linked,stats}`；`app.js:386 renderRepoRef` 按 5 mode 渲染（`:478,488,498,506,525`） | mode+linked+stats | mode 值域不同（symbol/topic/llm/overview/none vs meta/global/entity_local/structural/vector_only/out_of_scope）；linked≈anchors 但字段不同、无 `method∈{alias,zh_alias,embedding,focus_stack}`；缺 answer/resolved_query/needs_disambiguation/candidates/premise_flags/citations/degraded/suggestions。 |
| 7.2 `repo_overview` MCP 工具 | 未实现 | `build_overview`（`context.py:683`）逻辑在，但**无 MCP 层**；HTTP `GET /api/repograph/status`（`server.py:558,926`） | build_overview 函数 + status 端点（非工具） | 无 MCP 工具形态；无 repo 参数选仓库；「工具描述即路由提示」不成立（**依赖不存在的 MCP 层**）。 |
| 7.3 既有工具模糊输入行为 | 部分实现 | `impact.py:126,146` 精确/唯一后缀命中→执行并回显 `resolved_symbol`（`:181`）；多候选→ambiguous 不执行（符合 P3） | impact `_resolve_symbol` 三态 | 无「唯一强候选走链接 v2 + 别名解读」（仅 qualname 精确/后缀，不接 embedding/别名）；多候选是 error 形态非 needs_disambiguation+candidates；query_graph/text2cypher 不存在。 |

---

## 8. §8 评测扩展

| 机制 | 状态 | 代码位置 / 证据 | 四层瀑布对应物 | 差距 |
|---|---|---|---|---|
| 8.1 四子集（L0 元问题 10 / FZ 口语 20 / AMB 歧义 10 / PP 错误预设 8） | 未实现 | `tests/` 仅 `test_context.py`/`test_topic.py`（功能单测） | 无 | 无 eval/dataset.jsonl；无 type 字段；无 gold_entity/gold_behavior 标注。 |
| 8.2 新增指标（路由准确率/anchor hit@1,3/过问率漏问率/预设纠正率幻觉率/裸拒率/澄清开销） | 未实现 | — | 无 | 现有是 pass/fail 断言，非指标脚本。 |
| 8.3 主表扩展（v0.2 对比列） | 未实现 | — | 无 | 无对比矩阵。 |
| 8.4 阈值校准（网格搜索 τ/δ + calibration.md） | 未实现 | — | 无 | 阈值带本身不存在，无从校准。 |

---

## 9. 汇总

- 机制总数（逐条）：**约 33 条**（含基础设施 6 项另计）。
- **已实现（完整达成 v0.2 该条）：0 条**——v0.2 是增量设计，凡现有瀑布提供雏形/对应物者一律记「部分实现」，无一条已完整落地。
- **部分实现：11 条**——level-0 卡片、heat、cyclomatic/loc、blast_radius/fix_involvement、代码词元检测器、S1 路由器、S4 消歧、S6 回退阶梯、生成期来源标注、schema v2、§7.3 模糊输入行为。
- **未实现：20 条**——level-1、刷新策略、双语卡片、中文别名、缩写表、fan_in/pagerank、churn、披露话术、中文停用词、S0、S2、S3、S5、S7、生成期前四约束、repo_overview 工具、评测 8.1/8.2/8.3/8.4。
- **不适用：1 条**——死代码 A7（v0.2 自标非属性，依赖不存在的查询层）。
- 另：**6 项基础设施缺失**（向量层/embedding、symbol_alias/zh_alias、MCP 服务、指标属性全无、增量水位；networkx 存在）。多条「未实现」的子部分实际依赖前四者，属不适用成分。

## 10. 最大的三个缺口

1. **查询理解子层整体缺失（S0 规范化 + S1 显式路由器 + S2 改写 + S7 前提校验）**——v0.2 的核心骨架与 P2「路由显式化」的载体。现状是 build_repo_context 内部的隐式二元瀑布 + semantic_mode 三档，无 router_rules.yaml、无 `{meta,global,entity_local,structural,out_of_scope}` 五标签、无改写/前提抽取。缺它则 C1/C2/C3/C7 的分流与「解释回显」无入口，正是原始「你知道我的代码库吗」失效的根因层。

2. **三大基础设施不存在（向量层/embedding + `symbol_alias`/zh_alias 中文别名 + MCP 工具层）**——v0.2 的双语可达（§4.2）、level-1 进向量、S3 embedding top-5 候选、`repo_overview`/`ask_repo` 工具框架全部悬空。`ddl.sql` 里的 vector/symbol_alias 仅是 AGE 部署预留 schema，`age.py` 零读写，活跃 GraphStore 路径纯词面无向量、无 MCP。这是设计稿假设与现实落差最大处。

3. **模糊谓词指标预计算 + 响应 schema v2 的诚实回显三件套（§4.3 + §6 + resolved_query/消歧/premise/披露）**——C5 消歧、C6 模糊谓词、P5「解释回显」全缺。节点无 fan_in/pagerank/heat/cyclomatic/blast_radius/churn 任一属性（原料齐备但未算），生成期只有「标注来源」一句、无 resolved_query/candidates/premise_flags/suggestions，「答案+边界+出路」三件套与代理披露均未落地。
