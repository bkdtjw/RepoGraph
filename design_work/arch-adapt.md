# RepoGraph v0.2 架构适配方案（基础设施对账 + 逐机制替代/砍除）

**定位**：v0.2 补充设计稿（`RepoGraph-模糊语义处理设计.md`）在写作时假设了一套并不存在的基础设施——MCP stdio 工具、pgvector 向量层、embedding 双语可达、networkx、进程内会话。本文档逐条核对现状代码，为每个不匹配机制给出**等价替代**（具体到扩展现有哪个函数 / 新增什么纯 stdlib 组件）或**砍除**（理由 + 损失）。本文档只做设计，不改任何现有代码。

> 一切断言均来自对以下真实文件的通读：`src/repograph/retrieve/context.py`、`topic.py`、`impact.py`、`src/repograph/models.py`、`extract/ast_extractor.py`、`config.py`、`docs/RepoGraph-技术设计文档.md`、`C:/Users/nirvana/Desktop/claude-ui/server.py`。

---

## 0. 结论摘要：三分类映射表

| v0.2 机制 | 判定 | 落点（现有函数扩展 / 新增 stdlib 组件） |
|---|---|---|
| §4.1 level-0 仓库卡片 | **平移** | 扩展 `context.build_overview`：确定性字段已全在，追加缓存 + grok 索引期 summary |
| §4.1 level-1 包级概览进"向量层 chunk" | **改造** | 无向量层；改存为 Module 聚合节点属性 `overview`，global 路由直接注入 |
| §4.2 双语实体卡片"embedding 空间双语可达" | **改造** | 无 embedding；grok 生成中文描述 → 写入 Concept.aliases + 扩充 topic.py 语料（Function 卡片入 BM25 语料） |
| §4.2 中文别名入 `symbol_alias` 表 | **改造** | 无该表；写入 Concept 节点 `aliases[]` + 新增 Function/Class 节点属性 `zh_aliases[]`，`link_entities` 扩别名匹配 |
| §4.2 缩写扩展表 | **平移** | 新增 stdlib 常量词表 + `_tokenize` 前置双向扩展 |
| §4.3 `fan_in` / `heat` / `churn` / `blast_radius` / `fix_involvement` | **平移** | 新增 `src/repograph/metrics.py`（索引期，纯 stdlib），复用 `impact._bfs_levels` / `_reverse_adjacency` |
| §4.3 `pagerank`（networkx） | **改造** | networkx 不可用；纯 Python 幂迭代（见 §Q1），`metrics.py` 内实现 |
| §4.3 `cyclomatic` 圈复杂度 | **平移** | `ast_extractor._ScopeVisitor._enter_function` 内顺带计数，`FunctionFacts` 加字段（见 §Q2） |
| §4.4 停用词 / 疑问词 / 代码词元检测器 | **平移** | 扩展 `topic.zh_terms` + `context._tokenize`；代码词元检测器新增小函数供路由复用 |
| §5.1 S0 规范化 | **平移** | 新增 `router.normalize()` 纯函数 |
| §5.2 规则路由器 + `router_rules.yaml` | **改造** | 无 YAML 解析器；规则表用 Python 字面量放 `router.py`（见 §Q3）；LLM 兜底分类在网关侧复用 `_rg_llm` |
| §5.3 S2 改写扩展（flash 一次调用） | **改造** | 复用 `server._rg_llm_select_concepts` 调用骨架，抽公共 `_rg_llm_json`（见 §Q4） |
| §5.3 HyDE 变体 | **砍除** | 依赖 embedding 检索，无向量层，语义为零 |
| §5.4 S3 链接 v2"双语卡片 embedding top-5" | **改造** | 替换为 `link_entities` ∪ 缩写扩展 ∪ BM25-over-实体卡片（topic 语料扩容），三段阈值改按方法档 + BM25 边际（见 §分数标度对账） |
| §5.5 S4 消歧协议 | **平移** | `link_entities` 已产候选 + 分数；新增分带判定纯函数 |
| §5.6 S5 会话焦点栈（进程内） | **改造** | 网关多会话文件持久化，非 stdio 进程内；焦点栈存入 session JSON（见 §Q5） |
| §5.7 S7 前提校验 | **改造** | premises 来自 S2 合并调用；校验用图边存在性（无"向量高分支撑块"这一支，砍半） |
| §5.8 S6 回退阶梯 | **平移** | 现瀑布 L3 概览兜底已是雏形，形式化为显式阶梯 |
| §6 生成期披露约束 | **平移** | 网关注入前缀已按 mode 分；追加 resolved_query / 代理披露段 |
| §7.1 `ask_repo` 响应 schema v2 | **改造** | 无工具返回值；映射为 SSE `repo_context` 事件扩字段 + system 注入披露段 |
| §7.2 新增 `repo_overview` MCP 工具 | **砍除**（能力保留） | 无 MCP；能力由 meta/global 路由的注入直接实现 |
| §7.3 `impact_analysis` 模糊输入行为 | **部分平移** | `impact._resolve_symbol` 已有精确→后缀→歧义；网关注入路径前接链接 v2 |
| §7.1 `ask_repo` 新增 `context: list[str]` 参数 | **砍除**（被超越） | 网关天然持有 `sess["messages"]` 全历史，优于传参 |
| 主文档 §7.1/§7.4/§7.5 pgvector / chunk / embedding 向量层 | **砍除**（已缺席） | 现状四层瀑布已替代；确认不引入 |

一句话：**索引期确定性指标与概览卡片几乎可整体平移**（图结构 + AST 顺带产出）；**所有 embedding/向量/HyDE/MCP-tool 形态必须改造或砍除**；**路由与改写的"廉价 LLM"部分沿网关既有 `_rg_llm` 调用骨架平移**，只是物理落点从"MCP 工具"变为"网关注入 + SSE 事件"。

---

## 1. 现实基础设施对账（v0.2 假设 → 代码实况）

| v0.2 隐含假设 | 代码实况（出处） | 影响 |
|---|---|---|
| MCP stdio 工具形态，Agent 主动调 `ask_repo`/`repo_overview` | `server.py:api_chat` 把上下文注入 `payload["system"]` 后转发上游（`stream:True`），**push 模型，无任何工具** | 工具 → 路由 → 注入；"工具描述即路由提示"这一层不存在，路由必须在网关内部做 |
| pgvector + `chunk` 表 + embedding top-k | 全无。检索是 `link_entities`（词面）+ `topic_recall`（BM25-lite）+ `build_overview`（图统计） | 所有 embedding 位点必须改 BM25/图/LLM 组合或砍 |
| 分数为 embedding 余弦 0–1（τ_hi=0.62…） | `context._SCORE` 是整数档 `{100,80,60,40,30}`；`topic_recall` 是无上界 BM25 分 | 三段阈值必须按"方法档 + BM25 边际"重定义（见专节） |
| networkx 跑 PageRank | 无第三方图库；`impact.py` 是手写 BFS | 幂迭代纯 Python（§Q1） |
| 进程内一会话（stdio）→ 焦点栈挂进程 | `ThreadingHTTPServer` 多会话并发；会话是 `data/sessions/{sid}.json` 文件，`read_session`/`write_session` CRUD | 焦点栈必须持久化进 session 文件（§Q5） |
| `symbol_alias` 关系表（主文档 §6.2 DDL） | 本地后端只有 `GraphStore`（内存 dict + JSON）；无任何 SQL 表 | 别名写节点属性，不建表 |
| 廉价 LLM 是"haiku 档" | `_rg_llm_select_concepts` 读 `cfg["model_haiku"]`——该槽位即 qwen3.6-flash | 改写/兜底分类复用同一 `cfg["model_haiku"]` + `gateway_headers` + `/v1/messages` 非流式 |

**会话数据结构（`server.py` 实测）**，焦点栈设计的地基：
```
sess = {id, title, model, created_at, updated_at, messages: [...]}
messages[i] = {role, content, ts, [model, usage, interrupted]}
```
`api_chat` 内已有两次 `write_session`（追加 user 消息一次、追加 assistant 消息一次），焦点栈读写可无成本挂靠这两个点。

**廉价 LLM 调用骨架（`_rg_llm_select_concepts` 实测）**，S2/兜底分类要复用的：
```
model = cfg.get("model_haiku"); base = gateway_base(cfg)
payload = {model, max_tokens, system, messages:[{role:"user", content:user}]}
POST base+"/v1/messages"（非流式）→ _extract_text(raw) → _loose_json_object / _parse_*
全程 gateway_headers(cfg)，从不记录/回显 token；异常/超时/非200 → 返回空，静默降级
```

---

## 2. 五个重点问题的直接回答

### Q1 — §4.3 PageRank 没有 networkx 怎么办（纯 Python 幂迭代可行性）

**结论：完全可行，索引期毫秒级，无需任何第三方库。** Aider repo map 本质也是纯算法 PageRank，networkx 只是容器。

**图规模**：本项目两仓库量级，Function 节点数百至低千，`CALLS` 边同量级。幂迭代 O(iters ×(N+E))，iters≈30–50（d=0.85，L1 收敛阈 1e-6），总计几十万次浮点运算 → 亚毫秒到毫秒。

**算法（纯 stdlib，写入新模块 `src/repograph/metrics.py`）**：
1. 从 `store.edges("CALLS")` 建正向出邻接 `out[u]=[v...]` 与出度 `outdeg[u]`；节点集 = 全部 `Function`。
2. 反向图用于"重要=被依赖"，等价于对**反向边**跑标准 PageRank，即 `PR[u]` 高表示 u 被很多（且重要的）函数调用。直接在 `CALLS` 上跑标准 PageRank 得到的就是"入向重要性"，与 v0.2"CALLS 入度加权"语义一致，无需显式反转。
3. 初值 `PR[u]=1/N`；迭代：
   `PR_new[u] = (1-d)/N + d/N·(悬挂质量 Σ_{outdeg(v)=0} PR[v]) + d·Σ_{v∈in(u)} PR[v]/outdeg(v)`
   悬挂质量项处理无出边节点（叶子调用者），保证概率守恒。
4. `Σ|PR_new-PR| < 1e-6` 或达 iters 上限停；写入 Function 节点属性 `pagerank`（round 6 位，确定性）。

**关于"CALLS∪IMPORTS 并图"**：`CALLS` 是 Function→Function、`IMPORTS` 是 Module→Module，节点异构，直接并会混层。建议拆两个指标：Function 级 `pagerank`（CALLS，回答"最重要的函数"）与可选 Module 级 `module_pagerank`（IMPORTS，回答"核心模块"），各自独立幂迭代。这比 v0.2 的模糊并图更干净，且都复用同一 `_power_iteration(nodes, out_adj, outdeg)` 纯函数。

`fan_in` 无需迭代：`fan_in[u] = len(reverse_adjacency("CALLS")[u])`，直接复用 `impact._reverse_adjacency`。

### Q2 — 圈复杂度在哪个抽取阶段补

**落点：`ast_extractor.py` 的 `_ScopeVisitor._enter_function`，AST 遍历顺带产出（零额外解析开销）。**

现状：`_enter_function` 已经 `for child in node.body: self.walk(child)` 完整走了函数体（为收集 call_sites）。在同一遍里计数决策点即可。

具体设计：
1. `models.FunctionFacts` 加字段 `cyclomatic: int = 1`。
2. 遍历函数体时对以下 AST 节点 +1（近似 McCabe，v0.2 §4.3 定义"1 + 分支节点数"）：`ast.If`、`ast.For`/`ast.AsyncFor`、`ast.While`、`ast.Try` 的每个 `ExceptHandler`、`ast.BoolOp` 的每个额外 `values`（`a and b and c` 记 +2）、推导式（`comprehension.ifs` 每个 if）、`ast.IfExp`（三元）、`ast.With` 视需要、`ast.match_case`。
3. **关键边界**：嵌套函数拥有独立 `FunctionFacts`（现状 `_enter_function` 递归时会为内层函数新建），计数须只归属**最内层所在函数**，与现有 call_sites 归属逻辑一致——即在进入内层 `FunctionDef` 时不把内层分支计入外层。实现上：分支计数器随 `func_stack` 走，遇到子 `FunctionDef`/`AsyncFunctionDef` 停止向下计入当前函数（子函数自己那份单独算）。
4. `loc` 代理无需新算：`span_end - span_start + 1`，`FunctionFacts` 已有 `span_start/span_end`。
5. `build.py` 落图时把 `cyclomatic` 写进 Function 节点属性；`metrics.py` 不碰它（AST 期已定），只在 level-0 卡片和 C6 谓词排序时读取。

理由：圈复杂度是纯语法量，必须在有 AST 的阶段算；索引期其余指标（fan_in/pagerank/heat）是图/git 量，在 `metrics.py`（图建成后）算。两者分属两个抽取阶段，互不耦合。

### Q3 — §5.2 路由器规则表放哪

**约束冲突**：v0.2 写 `router_rules.yaml`，但硬约束(a)纯 stdlib——**无 YAML 解析器**（`pyyaml` 是第三方）。且路由是查询期、发生在网关，而 `config.py` 用的 `pydantic-settings` 在网关（`server.py` 纯 stdlib，独立代码库）不可用。

**替代设计**：
1. **规则表用 Python 字面量**，新增 `src/repograph/retrieve/router.py`，规则为模块级 `list[dict]`（每条 `{id, label, pattern(编译 re), requires:[...]}`），与 `config.endpoint_patterns` 已是"tuple of 正则"完全同构的先例。需要运行期热改时，改为 `router_rules.json`（stdlib `json` 可读）旁挂，`router.py` 启动载入——**json 替代 yaml，不引依赖**。
2. **物理分层沿用现有 L1/L2 切分**：
   - **确定性规则路由**放 RepoGraph 侧 `router.py`：纯函数 `route(question, linked, has_code_token) -> (label, rule_id|None)`，被 `build_repo_context` 在已经算出的 `link_entities` 结果之上调用（`build_repo_context` 现在就是先跑 `link_entities`），把当前隐式级联（L0符号→meta→L1→L3）显式化为 5 标签（meta/global/entity_local/structural/out_of_scope）。
   - **LLM 兜底分类**放网关 `server.py`：规则全不中且 `semantic_mode=='llm'` 时，复用 `_rg_llm`（见 Q4）向 flash 发固定标签集分类（温度 0、≤30 token、按规范化问题缓存）。这与"L1 确定性在 RepoGraph、L2 LLM 在网关"的既有职责边界完全一致，不新造架构。
3. 置信度 <0.6 归 `global`（误路由代价不对称）、路由决策写结构化日志——这两条纯策略，落网关 `api_chat` 注入块，随 `repo_context` SSE 事件透传 `mode`（现已透传 mode，只需补 `route_source`/`confidence`）。

路由信号 `has_code_token`/`no_linker_hit` 都是现成物料：`has_code_token` = 新增代码词元检测器（§4.4，camelCase/snake_case/点路径/反引号/`#数字`）；`no_linker_hit` = `link_entities` 返回空。二者在 `build_repo_context` 入口一次算出即可。

### Q4 — S2 改写扩展用 flash 走网关的调用路径怎么复用 `_rg_llm` 现有代码

**现有可复用骨架**：`server._rg_llm_select_concepts` 已经把"读 `model_haiku` + `gateway_base` → 组 payload → POST `/v1/messages` 非流式 → `_extract_text` → `_loose_json_object` 容错解析 → 白名单校验 → 静默降级"这条链走通了。S2 改写是**同构的第二次 flash JSON 调用**。

**重构建议（最小改动、最大复用）**：
1. 从 `_rg_llm_select_concepts` 抽出通用底座 `_rg_llm_json(cfg, system, user, timeout=20) -> dict|None`：只负责 model/base 取值、payload、`urlopen`、`_extract_text`、`_loose_json_object`，返回解析后的 dict 或 None。现有 `_rg_llm_select_concepts` 改为调它 + 自己的白名单过滤（行为不变，回归测试可保绿）。
2. 新增 `_rg_llm_rewrite(cfg, question, recent_turns) -> dict|None`：system = 附录 B 改写器骨架（"检索查询改写器，不是回答者"），user = 问题 + 最近若干轮原文（`recent_turns` 直接取自 `sess["messages"]`，见 Q5），调 `_rg_llm_json`，产出 `{queries[], symbol_guesses[], premises[]}`（§5.3/§5.7 合并一次调用）。程序侧约束：`queries` 截 2–4 条、`symbol_guesses` 截 5、`premises` 允许空。
3. 新增 `_rg_llm_route(cfg, question) -> (label, confidence)|None`：system = 固定标签集分类器，走同一 `_rg_llm_json`。
4. **回灌确定性通道**：`_rg_llm_rewrite` 产物**不进答案事实**（防污染，与现有 `symbol_guesses` 隔离规则一致）——`queries` 逐条喂 `context.link_entities` 与 `topic.topic_recall`，`symbol_guesses` 作为 `link_entities` 的额外候选词面。这一步是 RepoGraph 侧纯函数，网关只把 flash 产出的字符串传进去。

调用时机：`api_chat` 现有 L2 触发条件（`mode∈{overview,none}` 且 `semantic_mode=='llm'` 且 `gateway_base` 可用）正是 S2 该介入的点——entity_local 直接链接无命中。把现有"L2 直接选概念"扩成"先 S2 改写 → 二次链接/主题 → 仍无 → 再退回选概念"，复用同一网关往返预算（稳态仍是 1 次廉价调用 + 缓存）。

### Q5 — §5.6 焦点栈的存取点

**v0.2 的前提错了**：它写"服务端进程内维护（stdio 一进程一会话）"。现实是 `ThreadingHTTPServer` 多会话并发、会话是磁盘 JSON 文件。进程内 dict 会在多会话间串味、且进程重启即丢。

**替代设计——焦点栈持久化进 session 文件**：
1. **存储位置**：session JSON 新增键 `sess["rg_focus"] = [{entity_id, label, name, turn}]`（最多 5 条，`turn` 为消息序号，TTL=10 轮按 `turn` 差判定过期）。随会话文件天然隔离、天然持久，零新基础设施。
2. **写入点**：`api_chat` 中 RepoGraph 检索成功锚定实体后（`rg` 返回且 `linked` 非空），把本轮 `linked` 里的强锚（method∈{exact_qualname,suffix_qualname} 或 topic 高分概念）压栈；就近挂在**追加 assistant 消息那次 `write_session(latest)` 之前**（此时 `latest` 已 read，加一行 `latest["rg_focus"]=...` 即可，无额外落盘）。
3. **读取点**：检索**之前**，若问题含显式指代词（它/这个/该/上面，新增小词表）且 `link_entities` 无锚，从 `read_session(sid)` 拿到的 `sess["rg_focus"]` 取最近的类型相容实体作为种子，喂进 `_build_symbol_context` 的锚。消解结果必回显进 `resolved_query`（SSE 事件 + 注入段）。
4. **v0.2 的 `context: list[str]` 参数被网关超越**：网关本就持有 `sess["messages"]` 全历史，`_rg_llm_rewrite` 可直接吃最近 N 轮原文做指代消解（比传参更全）。因此 `ask_repo` 加参数这条**砍除**；焦点栈退化为"免 LLM 的廉价类型相容兜底"，是纵深防御而非主消解（主消解交给 flash 改写吃历史，或上游 Claude Code 自己展开"它"）。

理由：把状态放数据已有的载体（session 文件）而不是进程内存，是这套多会话网关下唯一正确的存法；焦点栈写读各挂一个已存在的 `write_session`/`read_session` 调用，零新增 I/O。

---

## 3. 索引期机制逐条适配（§4）

### §4.1 概览层
- **level-0 卡片 = 平移**。`context.build_overview` 已确定性产出 v0.2 卡片的绝大部分字段：`counts`（stats.modules/classes/functions/commits/concepts）、顶层模块（按 loc）、热点函数（按 MODIFIES 计数）、核心概念（按 IMPLEMENTS 落点）。缺口只有：`entrypoints`（聚合 `is_endpoint` 节点，现成属性，加一段过滤即可）、`hot_functions` 改用 §4.3 `heat`、`summary`（唯一一次 LLM）。
  - **summary 用 grok 索引期生成**（不是查询期 flash）：输入=确定性字段 + README 首段 + Top 概念，输出限 300 字，程序校验专名白名单（禁止出现未在输入中出现的专名）。落点：新增 `build.py` 尾部一步或 `metrics.py` 旁挂，产出 `output/repo_card.json` 缓存。
  - **注入形态**：meta 路由命中时，网关直接把 `repo_card.json` 拼进 system（零检索），复用 `_rg_inject_prefix("overview")` 前缀。
- **level-1 包级概览进"向量层 chunk" = 改造**。无向量层。改存为**顶层包 Module 聚合节点的 `overview` 属性**（120 字，grok 索引期生成），global 路由时直接注入这些属性文本，不进任何"chunk 池竞争"（本就没有池）。

### §4.2 双语实体卡片与中文别名
- **"embedding 空间双语可达" = 改造**。无 embedding，双语可达靠**词面 + BM25**：
  1. grok 为核心函数/类生成 ≤40 字中文描述（索引期）。
  2. 中文描述 → 写入两处让它可被召回：(a) 作为新语料文档进 `topic.py` 的 BM25 语料——现状 `_corpus_nodes` 只收 Concept/Commit/Module 三类，**扩为可选收 Function/Class 的中文卡片**（`_DOC_LABELS` 加档 + `_doc_text` 加分支）；(b) 名词短语抽为 `Concept.aliases` / 新增 Function 属性 `zh_aliases[]`。
  3. `context.link_entities` 的 Concept 分支已匹配 `aliases`（忽略大小写）；只需把 Function/Class 也纳入别名匹配（读 `zh_aliases`）。
- **中文别名入 `symbol_alias` 表 = 改造**。无 SQL 表；写节点属性 `aliases[]`/`zh_aliases[]`，`link_entities` 与 `topic_recall` 均从节点读，无需建表。
- **缩写扩展表 = 平移**。新增 stdlib `dict` 常量（`ctx→context, cfg→config, auth→authentication, db→database, msg→message …`），在 `context._tokenize` 产出候选后做双向扩展（`ctx` 命中也补 `context` 候选，反之亦然）。纯字符串操作。

### §4.3 模糊谓词操作化（指标预计算）
统一落新模块 **`src/repograph/metrics.py`（索引期、纯 stdlib、操作 GraphStore、写节点属性）**，`build.py` 图建成后调一次。全部复用现有图原语：

| 指标 | 实现（复用点） | 写入属性 |
|---|---|---|
| `fan_in` | `impact._reverse_adjacency("CALLS")[u]` 长度 | Function.fan_in |
| `pagerank` | 幂迭代（Q1），新 `_power_iteration` | Function.pagerank |
| `heat` | `commits_all + 2×commits_90d`；commits 来自 MODIFIES 反查 + `Commit.authored_at`。"90d"以**仓库最新提交日**为基准（确定性、可复现），非 wall-clock | Function.heat |
| `cyclomatic` | AST 期已产出（Q2），metrics 只读不算 | Function.cyclomatic |
| `loc`（函数） | `span_end-span_start+1`，现成 | 现算，不落属性 |
| `blast_radius` | `impact._bfs_levels([f], rev_calls, 3)` 闭包大小 × 是否可达 `is_endpoint` | Function.blast_radius |
| `fix_involvement` | FIXES 提交 ∩ MODIFIES 该函数的提交数（两遍扫边 join） | Function.fix_involvement |
| `churn_90d` | 近 90d MODIFIES 计数（同 heat 的时间基准） | Function.churn_90d |
| 死代码 | 主文档附录 A7 查询（无入边非端点），查询期算，非属性 | — |

`metrics.py` 依赖面：只 import `models.GraphStore` 与 `impact` 的 `_reverse_adjacency`/`_bfs_levels`（或复制这两个 10 行纯函数以保持 retrieve 层不反向依赖，二选一，倾向复用）。

### §4.4 词元与停用处理
- **平移**。`topic.zh_terms` 加中文停用/疑问词过滤（怎么/哪个/那块/搞的…）；`context._tokenize` 同表过滤。代码词元检测器抽为独立纯函数 `is_code_token(term)`（camelCase/snake_case/点路径/文件后缀/`#数字`/路由样式正则），**同时服务路由器（Q3 的 `has_code_token`）与链接器**。

---

## 4. 查询期机制逐条适配（§5）

- **§5.1 S0 规范化 = 平移**：全半角统一、保留标识符大小写、反引号内标强代码词元。新增 `router.normalize()`，网关在检索前调一次。
- **§5.2 路由器**：见 Q3。
- **§5.3 S2 改写扩展**：见 Q4。**HyDE = 砍除**（无 embedding 检索，生成假设文本无处可用；v0.2 本就标"默认关闭、A/B 后定"，直接删）。
- **§5.4 S3 链接 v2**：候选来源改为 `link_entities`（别名精确/后缀）∪ 缩写扩展命中 ∪ **BM25-over-实体卡片**（topic 语料扩容后，对原问题与全部改写 query 分别 `topic_recall` 取并），替换"embedding top-5"。三段阈值重定义见下节。
- **§5.5 S4 消歧协议 = 平移**：`link_entities` 已返回带 `score`/`method` 的候选列表且"同 entity 取最高分、并列取更长匹配"。新增纯函数 `disambiguate(candidates) -> {auto|need_disambiguation, resolved, dropped}`：Top-1 方法档领先（exact/suffix 视为强候选）→ 自动选并披露；多合法候选 → 置 `needs_disambiguation=true` 交调用方（此处即上游 Claude Code）。澄清预算 1 次/查询。
- **§5.6 S5 焦点栈**：见 Q5。
- **§5.7 S7 前提校验 = 改造**：premises 来自 S2 合并调用（Q4）。校验逻辑砍掉"向量高分支撑块"这一支（无向量），只保留"实体可链接但图中无支撑边"：premise 实体 `link_entities` 命中但相关边（如断言"用 Redis 锁"→查 IMPLEMENTS/DESCRIBES 概念含"锁/lock"）缺失 → 标 `premise_unverified` 注入生成上下文。损失：纯文本层面的反例（docstring 里其实提过但没成概念边）会漏判为 unverified，属可接受的保守偏差。
- **§5.8 S6 回退阶梯 = 平移**：现瀑布 `build_repo_context` 的 L3 概览兜底（`build_overview`，"永不失联"）已是回退阶梯的核心。形式化为：entity_local 空/低分 → 附 level-1 概览 + BM25 最近实体 Top-3（标 low-confidence）+ 建议问法；仍无 → 概览。裸拒率 0 已由现状"L3 恒兜底"保证。

---

## 5. 生成期与接口契约适配（§6/§7）

- **§6 披露约束 = 平移**：网关 `_rg_inject_prefix` 已按 mode 分前缀。追加三段式边界声明、代理定义披露、`resolved_query` 回显——都是往注入 system 文本里加固定话术，无结构改动。改写产物以 `[检索辅助,非事实]` 段隔离（现有 L2 已有隔离先例）。
- **§7.1 schema v2 = 改造**：无工具返回值。映射为两路：(a) **SSE `repo_context` 事件扩字段**——现发 `{mode, linked, stats}`，补 `resolved_query, needs_disambiguation, candidates[], premise_flags[], degraded, suggestions[]`，前端消费；(b) **system 注入段**——把 `resolved_query`/披露话术写进注入文本，供上游模型据以措辞。v1 兼容天然满足（纯增字段）。
- **§7.2 `repo_overview` MCP 工具 = 砍除，能力保留**：无 MCP 工具形态。该能力由 **meta/global 路由的注入**实现——"你了解这个项目吗"命中 meta 规则 → 注入 `repo_card.json`。损失：上游 Agent 无法"主动按需"拉概览（只能靠路由推），但网关 push 模型下这是必然，且元问题正是路由最容易判准的一类。
- **§7.3 `impact_analysis` 模糊输入 = 部分平移**：`impact._resolve_symbol` 已实现精确→唯一后缀→歧义（附 candidates）。网关注入路径里 `_build_symbol_context` 调 `impact_analysis` 前已用 `link_entities` 消解，符合"确定性工具不吃模糊输入"（P3）。缺口仅"多候选时返回 candidates 且不执行遍历"的显式行为，加一个分支即可。

---

## 6. 分数标度对账（v0.2 阈值不可直接落地的硬点）

v0.2 §5.4 用 `τ_hi=0.62 / τ_lo=0.45 / δ=0.05`，假设分数是 embedding 余弦 ∈[0,1]。**现实两套分数都不是这个标度**：
- `context._SCORE`：整数档 `exact=100, suffix=80, short=60, concept=40, module=30`。
- `topic_recall`：无上界 BM25 分（`_MIN_SCORE=1.0` 起，实测常见 1–10）。

**替代——分带按"方法档 + BM25 边际"而非余弦阈**：
- **自动锚定**（原 `s≥τ_hi`）：`method ∈ {exact_qualname, suffix_qualname}`，或词面精确命中（现有 `link_entities` 已对 exact 恒给最高档）。
- **进消歧**（原中段）：`method ∈ {short_name, concept_name}` 且存在多候选，或 topic Top-2 BM25 分差 < 相对边际（如 `(s1-s2)/s1 < 0.15`，取代绝对 δ=0.05）。
- **判无锚**（原 `s<τ_lo`）：`link_entities` 空 且 `topic_recall` 全部 < `_MIN_SCORE`（现成滤除线）。
- **校准对象随之变化**：§8.4 网格搜索的三元组不再是 `(τ_hi,τ_lo,δ)`，而是 `(topic 相对边际, 短名档是否强候选, min_score)`。校准流程（FZ-dev 网格 + FZ-test 冻结）本身可平移，只是搜索维度替换。

不处理这条，v0.2 的阈值会被当成余弦直接套到整数/BM25 分上，全盘失效——这是最隐蔽的落地陷阱，单列强调。

---

## 7. 新增/扩展清单（供实施排期，均纯 stdlib）

**新增文件**：
- `src/repograph/metrics.py`：§4.3 全部索引期指标 + 幂迭代 PageRank（Q1）。
- `src/repograph/retrieve/router.py`：S0 规范化 + 规则路由 + `is_code_token`（Q3、§4.4、§5.1）。
- （可选）`src/repograph/retrieve/router_rules.json`：规则热改载体（替代 yaml）。
- `output/repo_card.json`：level-0 卡片缓存（grok 索引期产出）。

**扩展现有函数（只读设计，不在本轮改）**：
- `models.FunctionFacts`：加 `cyclomatic`、（落图后）Function 节点加 `fan_in/pagerank/heat/blast_radius/fix_involvement/churn_90d/zh_aliases`。
- `ast_extractor._ScopeVisitor._enter_function`：圈复杂度顺带计数（Q2）。
- `context.build_overview`：补 entrypoints/hot_functions(heat)/summary 注入。
- `context._tokenize`：缩写双向扩展 + 停用过滤。
- `context.link_entities`：Function/Class 纳入 `zh_aliases` 别名匹配。
- `topic._corpus_nodes`/`_doc_text`/`_DOC_LABELS`：可选收 Function/Class 中文卡片进 BM25 语料。
- `topic.zh_terms`：停用/疑问词过滤。
- `server._rg_llm_select_concepts` → 抽 `_rg_llm_json` 公共底座；新增 `_rg_llm_rewrite`/`_rg_llm_route`（Q4）。
- `server.api_chat`：路由分派 + 焦点栈读写（Q5）+ `repo_context` 事件扩字段。
- `server` session schema：加 `rg_focus` 键。

**砍除清单（明确不做）**：HyDE；pgvector/chunk/embedding 向量层（含 level-1 进向量层、实体卡片 embedding、link v2 的 embedding top-5、S2 的 HyDE embedding）；`repo_overview` MCP 工具形态（能力转注入）；`ask_repo` 的 `context: list[str]` 参数（被网关会话历史超越）；`symbol_alias` SQL 表（转节点属性）；`router_rules.yaml`（转 Python 字面量 / json）。

---

## 8. 与 v0.2 实施计划（§11）的衔接提示

v0.2 的 P0–P3 阶段划分基本可沿用，但每阶段的"基础设施依赖"要按本文替换：P0 的 level-0 卡片走 `build_overview` 扩展而非新建向量 chunk；P1 的"双语卡片 embedding"改 BM25 语料扩容 + grok 中文描述；P1 路由器规则表用 Python/json；P2 的指标预计算走 `metrics.py`（PageRank 幂迭代）+ AST 期圈复杂度；P2 焦点栈落 session 文件。P3 阈值校准的搜索维度按 §6 替换。硬指标（幻觉率 0、裸拒率 0）不变——裸拒率 0 现状已由 L3 兜底达成。
