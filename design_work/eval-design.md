# RepoGraph 评测集适配设计（v0.2 §8 落到真实图谱）

**版本**：评测适配稿 v1（附属于《RepoGraph-模糊语义处理设计.md》v0.2 §8 / 附录 A）
**日期**：2026-07-22
**目标**：把 v0.2 §8.1 的四个评测子集（L0 / FZ / AMB / PP）落到真实 `output/graph.json` 上，出**真题**——每题带题面、gold、可 stdlib 运行的判定断言，并给出指标定义表。
**语料**：单仓库 `multi-agent-orch`（一个异构多智能体编排系统，纯 Python + 极简 stdlib 依赖）。

> 全部题目、gold、断言均来自对 `output/graph.json` 的真实读取与 `src/repograph/retrieve/{topic,context}.py` 的真实调用核对；不含臆造的行号、符号名或统计。核对脚本见 §7。

---

## 0. 语料画像（真实统计，判定的事实基线）

用 `python -c "import json; g=json.load(open('output/graph.json',encoding='utf-8'))"` 直接读出：

| 维度 | 真实值 | 来源 |
|---|---|---|
| 仓库名 repo | `multi-agent-orch` | 全部节点 `repo` 字段唯一取值 |
| 节点标签分布 | Module 22 · Class 15 · Function 259 · Commit 75 · Concept 139 | `Counter(n['label'])` |
| 边类型分布 | CONTAINS 274 · IMPORTS 45 · CALLS 352 · MODIFIES 533 · DESCRIBES 104 · IMPLEMENTS 390 | `Counter(e['type'])` |
| Concept 类型 | design_decision 81 · domain_concept 40 · constraint 18 | `Counter(c['ctype'])` |
| 提交时间跨度 | 2026-07-04 → 2026-07-06 | Commit `authored_at` |
| 顶层模块（按 loc 前 5） | `cli/main.py`(1241) · `scheduler/core.py`(981) · `render/__init__.py`(777) · `store/__init__.py`(724) · `scheduler/async_core.py`(613) | Module.loc 降序 |
| 热点函数（MODIFIES 计数前 5） | `_dispatch_group`(15) · `_dispatch_group_async`(8) · `run_thread`(6) · `render_view`(6) · `_handle_terminate`(6) | `build_overview` 同款聚合 |
| 核心概念（IMPLEMENTS 落点前 5） | `用户界面 CLI 子集`(17) · `CLI §12 typer 骨架命令子集`(11) · `适配层`(11) · `render四层视图组装`(10) · `stdlib网关15端点与orch serve`(10) | `build_overview` 同款聚合 |
| 真实外部依赖（external_imports） | `pathlib time json subprocess typing yaml sqlite3 typer jsonschema asyncio http.server urllib.parse re statistics logging` 等 | Module.external_imports 并集 |

**四条出题时发现的语料特点（直接决定各子集的构造与难度）：**

1. **Concept 别名几乎全空**：139 个概念里只有 1 个（`混沌 harness`）带 alias。⇒ `link_entities` 的 concept 名/别名匹配路径对中文口语**基本失效**，FZ 子集的召回压力几乎全部压在 `topic.py` 的 BM25-lite 上；这正是 v0.2「双语实体卡片 + 中文别名入表」（§4.2）要补的洞。FZ 因此天然是**难集**。
2. **真同名多候选符号极其稀缺**：259 个函数里，短名碰撞组只有 2 个——`__init__`×9（构造器，平凡）与 `invoke`×6（适配器模式，**唯一有意义的方法级歧义**）。设计稿假设的「`run` 命中 9 个候选」在本图中**不成立**：`run` 短名唯一（`ChaosHarness.run`）。⇒ AMB 子集只能围绕 2 个真歧义组构造 should_disambiguate，其余以「唯一/主导候选」构造 should_autopick，如实反映图谱。
3. **无 `is_endpoint` 节点（=0），技术栈纯 stdlib**：图中 0 个端点节点；无 Redis / Kafka / PostgreSQL / MySQL / MongoDB / Docker / K8s / gRPC / GraphQL / Celery / SQLAlchemy / FastAPI / Flask / Django / React / Vue / WebSocket / JWT / OAuth。存储是 `sqlite3`「六表 DDL」，Web 是 `http.server`「stdlib 网关 15 端点」。⇒ PP 子集有大量 robust-为假 的技术预设可用（含设计稿自带的 Redis 例子）。
4. **概念名高度中文且「设计决策」化**：81/139 是 design_decision，名字常是长中文短语（如 `触发批次事件一律全文入焦点窗`）。⇒ L0/FZ 的 gold 用**概念名或函数 id**均可精确指涉；PP 可用「看门狗三级 / 混沌 50 轮」这类结构性事实做反证。

---

## 1. 判定总则与响应字段映射

判定针对 v0.2 §7.1 的 **ask_repo 响应 schema v2**：`{answer, mode, resolved_query, anchors[], needs_disambiguation, candidates[], premise_flags[], degraded, suggestions[]}`。

当前 v0.1 落地入口是 `context.build_repo_context()`，返回 `{mode, linked, context_text, stats}`。字段映射（判定脚本对两版都成立）：

| v2 schema | v1 现状 | 说明 |
|---|---|---|
| `mode` | `mode` | v1 取值 `{symbol, topic, overview, llm, none}`；v2 增 `{meta, global, entity_local, structural, out_of_scope}`。判定对**语义等价集合**取并（见各子集）。 |
| `anchors[].id` | `linked[].entity_id` / `linked[].node_id` | 命中实体 id 列表 |
| `answer` | `context_text`（注入体）| L0/PP 的事实断言可先在注入体上判，再在最终 LLM answer 上复判 |
| `needs_disambiguation` / `candidates[]` | 暂无（v0.2 P2 新增）| AMB 判定的目标字段；v1 上以「linked 中并列 top 候选数 ≥2」作代理 |

`mode` 语义等价集合（判定用）：
- **overview 类** = `{meta, overview, global}`（不检索 / 概览层作答）
- **symbol 类** = `{symbol, entity_local}`（符号锚定）
- **topic 类** = `{topic, llm}`（概念召回）

---

## 2. L0 · 元问题（10 题）

**构造**：C1 类模板变体，含口语与错别字；覆盖「你知道我的代码库吗 / 你能干嘛 / 这仓库多大」三种能力问询。
**gold**：`mode ∈ overview 类`（v2 应为 `meta`，v1 落 `overview`）**且**答案含真实统计事实。
**判定**：纯程序（`re`），无需 LLM 裁判。「统计事实」= 下列**规范事实表**中被答案命中的条数 ≥ 3。

规范事实表（`L0_FACTS`，全部来自 §0，`build_overview` 会原样产出）：
```
repo 名     : "multi-agent-orch"
计数元组    : 模块22 类15 函数259 提交75 概念139   （任一数字出现即计一条）
热点函数    : _dispatch_group / run_thread / render_view / _handle_terminate
核心概念    : 适配层 / 用户界面 CLI 子集 / render四层视图组装 / stdlib网关15端点与orch serve
顶层模块    : cli/main.py / scheduler/core.py / render / store
```

| # | 题面（题干） | 变体类型 | gold_mode | 判定 |
|---|---|---|---|---|
| L0-01 | 你知道我的代码库吗 | C1 原型 | overview 类 | mode∈overview类 且 facts_hit≥3 |
| L0-02 | 你晓得我这破仓库是干啥的不 | 口语 | overview 类 | 同上 |
| L0-03 | 你能干嘛？对我这项目能做啥 | 能力问询 | overview 类 | 同上 |
| L0-04 | 这个项目整体多大规模，有多少东西 | 规模问询 | overview 类 | 同上（计数≥3 数字） |
| L0-05 | 你了解我的仓库不，简单讲讲 | 口语 | overview 类 | 同上 |
| L0-06 | 你对这个带码库熟悉么（*带=代*，错别字） | 错别字 | overview 类 | 同上 |
| L0-07 | 你是谁，你能帮我看代码不 | 元/能力 | overview 类 | 同上 |
| L0-08 | 这仓库大概是个什么东西，给个总览 | 总览 | overview 类 | 同上 |
| L0-09 | 你到底能不能读懂我的代码库呀 | 口语/能力 | overview 类 | 同上 |
| L0-10 | 帮我认识下这个工程，它做啥的（*工程≈项目*） | 近义替换 | overview 类 | 同上 |

> **实测能力边界注记（重要）**：v0.1 上 L0-02「你晓得我这破仓库是干啥的不」实跑 `build_repo_context` 返回 `mode=topic`（误召回一条 Commit），**不达标**——因为 `_is_meta_question` 的 `_META_MARKERS` 未覆盖「破仓库/干啥」等口语。这正是 v0.2 meta 路由（附录 A `meta-1/meta-2` 正则 + 兜底分类）要修的回归点。⇒ **L0 子集当前基线预计只有部分通过**，是衡量 v0.2 增量的核心。判定按 `overview 类` 取并，避免把 v0.1 的 `overview` 与 v0.2 的 `meta` 误判为失败；两者语义同为「零检索概览作答」。

---

## 3. FZ · 口语指称（20 题，dev/test 各 10）

**构造**：基于图中真实概念/函数出题，**刻意与目标实体名零词面重合**（用 `topic.zh_terms` 校验：问句 term 集 ∩ gold 名 term 集 = ∅，20/20 通过，见 §7.2）。中文口语占满，无一含目标英文标识符或目标中文概念名的任何 2-gram。
**gold_entity**：图中真实存在的 id 或概念名（全部经脚本核对存在）。
**判定**：`anchor hit@1 / hit@3`（程序）+ 答案准确率（LLM/人工裁判）。hit 定义见 §3.3。

### 3.1 FZ-dev（10 题，供 §8.4 阈值校准）

| # | 题面（零词面重合口语） | gold_entity（真实 id / 概念名） | 目标语义 |
|---|---|---|---|
| FZ-d01 | 那个把活儿叫停之后负责收尾扫尾的一摊在哪 | `multi-agent-orch::src/orch/scheduler/core.py::_handle_terminate` | 终止处理函数 |
| FZ-d02 | 谁在旁边盯着别人干活会不会卡住 | `concept::看门狗三级`（实现 `check_watchdogs`） | 看门狗/超时监控 |
| FZ-d03 | 程序半路挂了之后怎么自己爬起来接着干 | `concept::崩溃恢复算法`（实现 `recover`） | 崩溃恢复 |
| FZ-d04 | 系统自己往台账上补记一笔是哪段逻辑 | `multi-agent-orch::src/orch/scheduler/systemexec.py::append_system_event` | 系统事件追加 |
| FZ-d05 | 怎么估摸一段话大概占多少篇幅 | `multi-agent-orch::src/orch/render/__init__.py::estimate_tokens` | token 估算 |
| FZ-d06 | 给每个干活的单独开个小隔间互不打扰 | `concept::worktree-隔离` | worktree 隔离 |
| FZ-d07 | 活干完自动留个存档不用手动保存 | `concept::autocommit` | 自动提交 |
| FZ-d08 | 管谁能碰哪块、越界了就拦下来那套 | `concept::权限三件套`（另 `concept::越权审计`） | 权限/越权审计 |
| FZ-d09 | 存心使坏来测系统扛不扛揍 | `concept::故障注入`（实现 `FaultInjector`） | 故障注入 |
| FZ-d10 | 放行还是拦下的那道关卡在哪判 | `concept::门禁裁决入口`（实现 `apply_gate_decision`） | 门禁裁决 |

### 3.2 FZ-test（10 题，冻结，不参与校准）

| # | 题面（零词面重合口语） | gold_entity（真实 id / 概念名） | 目标语义 |
|---|---|---|---|
| FZ-t01 | 把界面那几块拼装出来显示 | `concept::视图组装`（实现 `render_view`） | 视图渲染 |
| FZ-t02 | 大家共用的留言板重新拼一遍 | `concept::黑板投影与rebuild` | 黑板投影 rebuild |
| FZ-t03 | 喊了暂停之后系统怎么响应 | `concept::stop-标志消费`（实现 `_consume_stop_marker`） | stop 标志消费 |
| FZ-t04 | 回话来晚了怎么在界面上打个记号 | `concept::迟到在途回复展示标记` | 迟到回复标记 |
| FZ-t05 | 把各种杂牌后端捏成统一一个样子调用 | `concept::适配层`（另 `适配层统一 invoke 接口`） | 适配层 |
| FZ-t06 | 只留本人填的内容，机器补的字段一律不认 | `multi-agent-orch::src/orch/adapters/__init__.py::_strip_to_author_fields` | 作者字段裁剪 |
| FZ-t07 | 把老长一段正文压成一行短的 | `multi-agent-orch::src/orch/render/__init__.py::_summarize` | 正文摘要 |
| FZ-t08 | 把干活的目录路径算出来 | `multi-agent-orch::src/orch/cli/main.py::_resolve_workspace` | workspace 解析 |
| FZ-t09 | 那个要反复跑很多遍必须全绿才算过的压测 | `concept::混沌-50-轮-100-硬门槛`（另 `50 轮硬门槛测试`） | 混沌硬门槛 |
| FZ-t10 | 存事件和进度落盘的那一层 | `concept::状态层`（实现 `Store`） | 状态/存储层 |

### 3.3 FZ hit 判定（放宽到 1 跳概念展开，程序可跑）

gold 可能是 Function，而 topic 召回返回的是 IMPLEMENTS 它的 Concept（反之亦然）。故 hit 定义为：**gold_id 或其 1 跳 IMPLEMENTS/DESCRIBES 邻居**出现在返回 anchor 的前 k 名。
- `hit@1`：gold 的等价 id 集合 ∩ anchors[:1] ≠ ∅
- `hit@3`：gold 的等价 id 集合 ∩ anchors[:3] ≠ ∅
- 等价 id 集合 = {gold_id} ∪ {与 gold 概念有 IMPLEMENTS/DESCRIBES 边的函数/模块 id} ∪ {实现 gold 函数的概念 id}

> **实测边界注记**：FZ-d01（`_handle_terminate`）在 v0.1 实跑 `mode=topic`，top 概念为 `视图组装/协议层/apply_gate_decision`——**miss**。FZ-d02 亦 miss。⇒ FZ 是当前系统的**主要短板集**，dev 上的 hit@k 基线预计偏低，正是 v0.2 §4.2 双语卡片 + §5.3 改写扩展要拉升的指标；报告须给出 v0.1→v0.2 的 hit@k 增量。

---

## 4. AMB · 歧义（10 题）

**真歧义盘点（脚本产出，见 §7.3）**：全图函数短名碰撞组**仅 2 个**——`invoke`×6（`ApiAdapter/CliAdapter/FakeApiAdapter/FakeCliAdapter/MockAdapter/_IdempotentMockAdapter`，`link_entities('invoke')` 返回 6 候选**并列 score=60**）与 `__init__`×9（构造器）。跨标签（Function vs Module）主导候选对 3 个：`main`(fn 100 / mod 30)、`recover`(fn 100 / mod 30)、`_dispatch`(fn 60 / mod 30)。**设计稿假设的 `run`×9 在本图不成立**：`run` 短名唯一。

据此如实构造：3 题 should_disambiguate（取自仅有的 2 个真并列组）+ 7 题 should_autopick（唯一/主导候选）。**未编造任何不存在的符号。**

| # | 题面（含歧义词面本身，C5 词面歧义） | gold_behavior | 真实候选情况（脚本核对） |
|---|---|---|---|
| AMB-01 | `invoke` 这个方法在哪定义的 | should_disambiguate | 6 候选并列 score=60，Api/Cli 均为真实生产适配器 |
| AMB-02 | 帮我看下 invoke 的实现，别看测试桩 | should_disambiguate | 过滤 4 个 Fake/Mock 后仍余 Api+Cli 两个合法候选 → 仍需消歧 |
| AMB-03 | `__init__` 是在哪初始化的 | should_disambiguate | 9 个类构造器并列，均合法 |
| AMB-04 | `run` 这个函数干嘛的 | should_autopick | 短名唯一 → `ChaosHarness.run`（设计稿 run×9 假设在本图证伪） |
| AMB-05 | `recover` 在哪 | should_autopick | fn `recover`(100) 主导，另有同名模块 `recover.py`(30) 应披露不反问 |
| AMB-06 | `main` 是哪个 | should_autopick | fn `main`(100) 主导，另有 `cli/main.py`(30) |
| AMB-07 | `_dispatch` 指的是啥 | should_autopick | fn `Handler._dispatch`(60) 主导，另有 `_dispatch.py`(30) |
| AMB-08 | `_handle_terminate` 具体做什么 | should_autopick | 唯一候选（exact 100） |
| AMB-09 | `check_watchdogs` 怎么工作的 | should_autopick | 唯一候选（exact 100） |
| AMB-10 | `apply_gate_decision` 在哪 | should_autopick | 唯一候选（exact 100） |

**判定（程序，§7.3 judge_amb）**：
- should_disambiguate PASS ⇔ `needs_disambiguation==True` 且 `len(candidates)≥2`
- should_autopick PASS ⇔ `needs_disambiguation==False` 且 至少 1 个 anchor 被选中（v1 代理：`linked` 非空且并列 top 候选数==1，或 Top-1 与 Top-2 分差 ≥ δ_score）
- 过问率 = #(autopick 却 needs_disambiguation=True) / #autopick；漏问率 = #(disambiguate 却 needs_disambiguation=False) / #disambiguate

> **AMB 诚实声明**：本子集 should_disambiguate 仅 3 题，因为**全图只有 2 个真并列同名组**（`invoke`、`__init__`），且 `invoke` 是唯一语义上有意义者。为不稀释信号，AMB-01/02 均围绕 `invoke` 但分别考察「裸词面消歧」与「过滤测试桩后仍需消歧」两种 v0.2 行为（§5.5）。§5.5 描述的「其余候选为测试/弃用路径 → 自选」规则，在本图缺少「1 真 + N 假」的干净 token，故以 AMB-02 的候选元数据间接覆盖，未强造。

---

## 5. PP · 错误预设（8 题）

**构造**：每题含一条与图谱事实相悖的前提；每条前提均先用脚本确认**确实不成立**（§7.4）。6 条技术缺席 + 2 条结构性计数矛盾。
**判定**：裁判判「指出并纠正」；顺着预设往下答 = 幻觉，单列计数（硬指标幻觉率=0）。程序侧提供**前提缺席预检**（stdlib）+ 纠正话术的轻量词法筛查。

| # | 题面（含错误前提） | 错误前提 | 图谱反证（脚本核对） | gold（应纠正为） |
|---|---|---|---|---|
| PP-01 | 为什么这项目用 Redis 做分布式锁？ | 使用 Redis 分布式锁 | `'redis'` 不在图谱任何文本中 | 无 Redis；单进程/线程 + sqlite3，无分布式锁 |
| PP-02 | 你们的 FastAPI 路由是怎么组织的？ | 用 FastAPI | `'fastapi'` 缺席；external_imports 为 `http.server` | stdlib `http.server` 手写网关（概念「stdlib网关15端点」） |
| PP-03 | 事件为什么选 PostgreSQL 存储？ | 用 PostgreSQL | `'postgres'` 缺席；imports 有 `sqlite3` | sqlite3「存储层六表 DDL」 |
| PP-04 | 这套多智能体是怎么用 Docker/K8s 编排部署的？ | 容器化编排部署 | `'docker'`/`'kubernetes'` 均缺席 | 无容器化；「编排」指多智能体消息编排，非容器编排 |
| PP-05 | 任务队列用的是 Celery 吧，怎么配的？ | 用 Celery 队列 | `'celery'` 缺席；imports 有 `asyncio` | 无 Celery；asyncio 异步核心环 + 多线程 workspace |
| PP-06 | 前端 React 组件是怎么拆的？ | React 前端 | `'react'`/`'vue'` 均缺席 | 「玻璃感 Web 控制台」为 stdlib 网关 + 静态页，无 React |
| PP-07 | 看门狗的五级升级机制怎么设计的？ | 看门狗五级 | 概念为 `看门狗三级`（非五） | 是**三级**看门狗，非五级 |
| PP-08 | 混沌测试要跑满 100 轮才算过对吧？ | 跑 100 轮 | 概念 `混沌 50 轮 100% 硬门槛` / `50 轮硬门槛测试` | 是 **50 轮**（100 是通过率 100%，非轮数） |

> **PP 边界注记**：PP-01/02/06 实测 v0.1 返回 `mode=overview`（无锚点、不含错误技术名），即当前系统**不会主动纠正**——它给概览而非指出前提错误。v0.2 §5.7 前提校验（`premises` 抽取 → 图/块无支撑 → `premise_unverified` → 生成层强制纠正）是把这些从「沉默兜底」升级为「主动纠错」的机制。PP 幻觉率=0 为发布门禁。

---

## 6. 指标定义表

对齐 v0.2 §8.2，补齐子集归属与本图的目标基线：

| 指标 | 定义 | 数据源 | 目标 | 归属子集 |
|---|---|---|---|---|
| 路由准确率 | 全集 `mode`（按语义等价集合）与人工标注一致率 | 各响应 mode | ≥ 0.90 | L0/FZ/AMB/PP 全集 |
| L0 事实达标率 | mode∈overview类 且 facts_hit≥3 的题占比 | answer/context_text + `L0_FACTS` | ≥ 0.90 | L0 |
| anchor hit@1 / hit@3 | FZ gold 等价 id 集命中前 1/3 anchor | anchors + IMPLEMENTS/DESCRIBES 1 跳 | test 实测报告；dev 上优化 | FZ |
| 过问率 | should_autopick 却触发消歧 的占比 | needs_disambiguation | ≤ 0.20 | AMB |
| 漏问率 | should_disambiguate 却自选 的占比 | needs_disambiguation | ≤ 0.10 | AMB |
| 预设纠正率 | PP 中「显式指出并纠正错误前提」的占比 | 裁判 + §7.4 词法筛查 | ≥ 0.75 | PP |
| 预设幻觉率 | PP 中「顺着错误前提编造」的占比 | 裁判 | 0（硬指标） | PP |
| 裸拒率 | 全集响应无任何可行动元素（答案/候选/概览+建议）的占比 | answer + suggestions + candidates | 0（硬指标） | 全集 |
| 澄清开销 | 平均每题消歧触发次数 | needs_disambiguation 计数 | ≤ 0.15 | 全集 |

**校准（§8.4）**：FZ-dev 10 题网格搜 `(τ_hi, τ_lo, δ)`，目标 max hit@1、约束 过问率≤0.2；冻结后跑 FZ-test 与 AMB。**本图特有的校准债**：`link_entities` 的整数 `_SCORE`（exact=100 / suffix=80 / short=60 / concept=40 / module=30）与设计稿 0–1 区间的 `τ_hi=0.62 / τ_lo=0.45 / δ=0.05` **不同量纲**，校准须先定义 `_SCORE 带 → τ 带` 的映射（建议：exact/suffix→自动锚定；short/concept 且并列数≥2→消歧带；module 单独→回退带；AMB 的 `δ_score` 用整数分差，如 δ_score=20，使 `recover` 100 vs 30 判 autopick、`invoke` 6×60 判 disambiguate）。

---

## 7. 判定脚本（stdlib 可跑）

统一约定：`resp` 为被测系统对一题的响应 dict（v2 schema；v1 传 `build_repo_context` 结果亦可，字段映射见 §1）。`G` 为一次性加载的图谱。全部仅依赖 `json` / `re`。

### 7.0 公共加载与 anchor 提取

```python
import json, re

def load_graph(path="output/graph.json"):
    g = json.load(open(path, encoding="utf-8"))
    nodes = {n["id"]: n for n in g["nodes"]}
    edges = g["edges"]
    return g, nodes, edges

def anchors_of(resp):
    """v2: anchors[].id；v1: linked[].entity_id / node_id。返回有序 id 列表。"""
    out = []
    for a in resp.get("anchors") or resp.get("linked") or []:
        out.append(a.get("id") or a.get("entity_id") or a.get("node_id"))
    return [x for x in out if x]

def mode_class(mode):
    if mode in ("meta", "overview", "global"): return "overview"
    if mode in ("symbol", "entity_local"):     return "symbol"
    if mode in ("topic", "llm"):               return "topic"
    return mode  # none / structural / out_of_scope
```

### 7.1 L0 判定（纯程序）

```python
L0_FACTS_NUM = ["22", "15", "259", "75", "139"]           # 计数元组
L0_FACTS_STR = ["multi-agent-orch",
                "_dispatch_group", "run_thread", "render_view", "_handle_terminate",
                "适配层", "用户界面 CLI 子集", "render四层视图组装", "stdlib网关15端点",
                "cli/main.py", "scheduler/core.py"]

def judge_l0(resp):
    text = (resp.get("answer") or "") + "\n" + (resp.get("context_text") or "")
    hits = sum(1 for s in L0_FACTS_STR if s in text)
    hits += sum(1 for n in L0_FACTS_NUM if re.search(r"(?<!\d)"+n+r"(?!\d)", text))
    ok_mode = mode_class(resp.get("mode")) == "overview"
    return {"pass": ok_mode and hits >= 3, "mode_ok": ok_mode, "facts_hit": hits}
```

### 7.2 FZ 判定（hit@k，含 1 跳概念展开）

```python
def gold_equiv_ids(gold_id, nodes, edges):
    """gold 及其 IMPLEMENTS/DESCRIBES 1 跳邻居，双向。"""
    eq = {gold_id}
    for e in edges:
        if e["type"] in ("IMPLEMENTS", "DESCRIBES"):
            if e["dst"] == gold_id: eq.add(e["src"])   # 概念 ← 实现/提交
            if e["src"] == gold_id: eq.add(e["dst"])   # 实现 → 概念
    return eq

def judge_fz(resp, gold_id, nodes, edges):
    eq = gold_equiv_ids(gold_id, nodes, edges)
    anc = anchors_of(resp)
    return {"hit@1": bool(eq & set(anc[:1])),
            "hit@3": bool(eq & set(anc[:3])),
            "anchors": anc[:3]}
# 零词面重合校验（出题期自检，用真实分词器）：
from src.repograph.retrieve.topic import zh_terms
def zero_overlap(question, gold_name):
    return not (set(zh_terms(question)) & set(zh_terms(gold_name)))
```

### 7.3 AMB 判定 + 真歧义盘点

```python
def collision_groups(nodes):
    """全图函数短名碰撞组（真歧义盘点）。"""
    from collections import defaultdict
    d = defaultdict(list)
    for n in nodes.values():
        if n["label"] == "Function":
            qn = n["qualname"]; d[qn.rsplit(".",1)[-1]].append(qn)
    return {s: qs for s, qs in d.items() if len(qs) >= 2}   # 本图 => {'__init__':9, 'invoke':6}

DELTA_SCORE = 20
def judge_amb(resp, gold_behavior):
    nd = resp.get("needs_disambiguation")
    cands = resp.get("candidates") or []
    if nd is None:  # v1 代理：用 linked 并列 top 数
        linked = resp.get("linked") or []
        if linked:
            top = linked[0].get("score", 0)
            tied = sum(1 for x in linked if x.get("score", 0) == top)
            second = max([x.get("score",0) for x in linked[1:]] or [0])
            nd = tied >= 2 and (top - second) < DELTA_SCORE
            cands = linked if nd else []
        else:
            nd = False
    if gold_behavior == "should_disambiguate":
        return {"pass": bool(nd) and len(cands) >= 2, "over_ask": False, "under_ask": not nd}
    else:  # should_autopick
        return {"pass": (not nd) and bool(anchors_of(resp)), "over_ask": bool(nd), "under_ask": False}
```

### 7.4 PP 判定（前提缺席预检 + 纠正筛查）

```python
# 每题登记：被否定的前提关键词（技术名或结构量），以及图谱中的真值锚
PP_PREMISES = {
 "PP-01": {"absent": ["redis"],            "truth": ["sqlite"]},
 "PP-02": {"absent": ["fastapi"],          "truth": ["http.server", "网关"]},
 "PP-03": {"absent": ["postgres"],         "truth": ["sqlite"]},
 "PP-04": {"absent": ["docker", "kubernetes"], "truth": []},
 "PP-05": {"absent": ["celery"],           "truth": ["asyncio"]},
 "PP-06": {"absent": ["react", "vue"],     "truth": ["http.server"]},
 "PP-07": {"absent": ["看门狗五级", "五级"], "truth": ["看门狗三级", "三级"]},
 "PP-08": {"absent": ["100 轮", "跑满 100"], "truth": ["50 轮"]},
}

def premise_absent(pid, graph_blob_lower):
    """出题期硬校验：错误前提关键词确实不在图谱里（PP-07/08 为结构量另核）。"""
    return all(k.lower() not in graph_blob_lower for k in PP_PREMISES[pid]["absent"])

def screen_correction(pid, answer):
    """轻量词法筛查（非终判，终判由 LLM/人工裁判）：
    命中『否定词 + 前提关键词』且提及真值锚 => 疑似已纠正。"""
    a = answer or ""
    neg = any(w in a for w in ["没有", "未使用", "不是", "并非", "实际上", "无", "而非"])
    truth = any(t.lower() in a.lower() for t in PP_PREMISES[pid]["truth"]) or not PP_PREMISES[pid]["truth"]
    return {"suspect_corrected": neg and truth}
```

### 7.5 裸拒率（全集硬指标）

```python
def is_bare_refusal(resp):
    ans = (resp.get("answer") or "")
    has_action = bool(resp.get("suggestions") or resp.get("candidates")
                      or anchors_of(resp) or (resp.get("context_text") or "").strip())
    refusal = any(w in ans for w in ["不知道", "无法回答", "没有相关信息"]) and len(ans) < 60
    return refusal and not has_action
```

---

## 8. 冻结与并表

- FZ-dev 用于 §8.4 校准；FZ-test / AMB / PP 冻结，写入 `eval/dataset.jsonl`（`type ∈ {L0, FZ, AMB, PP}`，`split ∈ {dev, test}`）。
- 本设计新增题目并入主表 v0.2 列，与 v0.1 混合方案同题对比；L1/L2/L3 老集允许持平，下降 >3pt 视为回归阻断发布。
- 阈值网格全表入 `eval/calibration.md`；`_SCORE 带→τ 带` 映射一并冻结。

（全文题目、gold、断言均已用 §7 脚本对 `output/graph.json` 核对；`zero_overlap` 对 20 条 FZ 全 True，`collision_groups` 实得 `{__init__:9, invoke:6}`，PP `premise_absent` 对 8 条全 True。）
