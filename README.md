# RepoGraph

> 面向编码 Agent 的**代码知识图谱**与混合检索系统 —— 把一个 Python 仓库的「结构」（AST / 调用链 / git 历史）与「语义」（commit / docstring / README / issue 中的设计决策）统一抽取进一张属性图，让 Agent 不只知道「符号在哪里」，还能回答「改这里会波及谁」和「这套设计是怎么来的」。

RepoGraph 在整体架构里处于编码 Agent 的**上下文供给层**：常规的 `grep` / 文件读取只能定位符号，而多跳影响面分析与设计溯源需要沿调用链、导入链、提交历史做跨文件的图遍历——这正是本项目要补的能力。

## 与技术设计文档的关系

本仓库是设计文档的 **v0.1 工程实现**。完整的架构推导、Schema 设计、评测方案与里程碑见：

- [`docs/RepoGraph-技术设计文档.md`](docs/RepoGraph-技术设计文档.md)

代码严格遵循文档 §3（图谱 Schema）与 §4（确定性抽取层）；所有跨模块接口的唯一来源是 [`src/repograph/models.py`](src/repograph/models.py) 与 [`src/repograph/config.py`](src/repograph/config.py)。README 末尾诚实列出了 v0.1 与文档目标形态的差异。

## 架构分层（文档 §2.1）

```text
┌─────────────────────────────────────────────────────────────┐
│  接入层    CLI (repograph ...)   │   MCP Server (stdio)      │
├─────────────────────────────────────────────────────────────┤
│  检索层    entity linker │ impact_analysis(模板查询)         │
│            text2cypher(LLM 生成 + 四重防护)                  │
│            local search(k 跳子图 + pgvector 混合拼装)        │
├─────────────────────────────────────────────────────────────┤
│  存储层    PostgreSQL 16 单实例                              │
│            ├─ Apache AGE:属性图(节点/边/属性)              │
│            ├─ pgvector:文本块向量(HNSW)                    │
│            └─ 关系表:chunk 元数据 / 别名表 / 索引水位        │
├─────────────────────────────────────────────────────────────┤
│  抽取层    确定性:ast 结构抽取 │ 调用图解析 │ git diff 映射  │
│            语义:LLM 概念抽取(evidence+confidence)→ 对齐    │
├─────────────────────────────────────────────────────────────┤
│  数据源    仓库工作树 │ git 历史 │ GitHub Issues API          │
└─────────────────────────────────────────────────────────────┘
```

> v0.1 落地范围见「与设计文档的差异」一节：存储层以零依赖的本地 GraphStore 承接，接入层先交付 CLI，检索层先交付确定性的 `impact_analysis`。

## 快速开始

依赖仅需 `GitPython / networkx / matplotlib / pydantic / pydantic-settings`，无需数据库、无需容器，克隆即可跑。

```bash
git clone https://github.com/bkdtjw/RepoGraph
cd RepoGraph
pip install -e .

# 一键全流程：结构层 + git 层 → 语义层 → 可视化
python -m repograph.cli all --repo <目标仓库路径> --name <名>
# 安装后等价写法：
repograph all --repo <目标仓库路径> --name <名>
```

产出默认落在 `output/`（`graph.json` / `stats.json` / 可视化图片）。

### 子命令一览

| 命令 | 作用 |
|---|---|
| `index --repo PATH --name NAME [--out DIR]` | 确定性抽取（AST → 端点 → 调用图 → git），落盘 `graph.json` 与 `stats.json` |
| `semantic --repo PATH --name NAME [--graph PATH]` | LLM 语义层：就地合并 `Concept` 节点与 `DESCRIBES` / `IMPLEMENTS` 边并回写 |
| `viz [--graph PATH] [--out DIR]` | 渲染图谱可视化产出 |
| `impact --symbol S [--graph PATH] [--depth 3] [--mode calls\|imports]` | 影响面分析（确定性模板查询，分层缩进打印） |
| `stats [--graph PATH]` | 打印图谱规模与已存质量指标 |
| `all --repo PATH --name NAME [--out DIR] [--no-semantic]` | 一键全流程；`--no-semantic` 可跳过语义层 |

各阶段进度、最终节点/边计数与质量指标会打印到控制台（入口已 `reconfigure(encoding="utf-8")`，Windows 控制台中文与箭头字符正常显示）。

## 图谱 Schema：六类节点 · 九类边

**节点（`Module` / `Class` / `Function` / `Commit` / `Issue` / `Concept`）**

| Label | 关键属性 | 说明 |
|---|---|---|
| `Module` | `id, repo, path, name, package, loc, docstring` | 一个 `.py` 文件对应一个 Module |
| `Class` | `id, repo, qualname, file, span_start, span_end, docstring, bases[]` | `bases` 仅记录仓库内可解析的基类 |
| `Function` | `id, repo, qualname, file, span_*, signature, is_async, is_method, is_endpoint, http_method, route_path, docstring` | 含方法；端点标记见文档 §4.3 |
| `Commit` | `id, repo, hash, author, authored_at, message, files_changed, insertions, deletions` | 事件节点，天然承载 n 元事实 |
| `Issue` | `id, repo, number, title, state, labels[], created_at, body_excerpt` | `body_excerpt` 截断至 1000 字符 |
| `Concept` | `id, name, ctype, description, aliases[], confidence, evidence[]` | `ctype ∈ {design_decision, domain_concept, constraint}` |

**边（结构 6 类由确定性抽取产出，语义 3 类由 LLM 抽取产出）**

| 边 | 方向 | 属性 | 来源 |
|---|---|---|---|
| `CONTAINS` | Module→Class/Function, Class→Function | — | AST |
| `IMPORTS` | Module→Module | `names[]` | AST（仅仓库内目标；外部依赖记为 Module 属性，不建边） |
| `CALLS` | Function→Function | `count, call_sites[]` | 调用图解析（仅落**可静态判定**的边，不猜测） |
| `MODIFIES` | Commit→Function | `lines_added, lines_deleted, overlap_lines` | git diff → 函数跨度求交 |
| `TOUCHES` | Commit→Module | `lines_added, lines_deleted` | 函数级映射失败时的文件级兜底 |
| `FIXES` | Commit→Issue | `pattern` | commit message 正则 |
| `PROPOSES` | Issue→Concept | `evidence, confidence` | LLM 语义抽取 |
| `DESCRIBES` | Commit→Concept | `evidence, confidence` | LLM 语义抽取 |
| `IMPLEMENTS` | Function\|Module→Concept | `evidence, confidence` | LLM 语义抽取（目标符号经存在性校验） |

规范 ID（`{repo}::{relpath}::{qualname}` 等）即 MERGE 合并键，流水线重跑幂等；结构层因此**不存在实体对齐问题**，对齐工作被压缩到概念层。

## 质量指标（`repograph stats`）

确定性抽取不追求「看起来很全」，而是把**盲区如实量化**。`stats.json` 输出：

| 指标 | 定义 |
|---|---|
| `call_resolved_rate` | 已解析调用点 / 全部调用点。CALLS 边只落可静态判定者，此率直接度量调用图的完整度 |
| `modifies_coverage` | 产生 ≥1 条 `MODIFIES` 的代码提交 / 全部代码提交 |
| `dangling_modifies` | diff 命中的历史函数在 HEAD 已不存在、被丢弃的映射数（图中不留幽灵节点） |
| `parse_skips` | 语法错误跳过的文件数（工作树 + 历史 blob） |
| `blob_cache_hit_rate` | 函数跨度表按 `blob_sha` 缓存的命中率 |
| `semantic_extracted` / `extraction_reject_by_reason` | 语义层抽取数与各环节校验拒绝计数 |

指标以**实测为准，不预设数值**——「你如何度量图谱质量」的答案就是这张表。

## 输出物

| 文件 | 内容 |
|---|---|
| `output/graph.json` | 全量图谱：`{"nodes":[…], "edges":[{"src","type","dst","properties"}]}`，可用 `GraphStore.load()` 复原 |
| `output/stats.json` | 上表质量指标 + 图谱规模计数 |
| `output/*.png` / `*.svg` | 结构可视化（由 `viz` 阶段生成，具体文件见命令输出列表） |

## v0.1 与设计文档的差异（诚实声明）

设计文档以「PostgreSQL 16 单实例三引擎（AGE 图 + pgvector 向量 + 关系表）」为目标形态。为了**克隆即可零依赖运行、降低演示与评审门槛**，v0.1 做了如下取舍：

- **存储后端**：用本地 `GraphStore`（内存 + JSON 持久化，见 `models.py`）替代 PostgreSQL + Apache AGE。Cypher/AGE 的 DDL 与 `run_cypher` 唯一入口接口**原样保留在 `store/`**，作为部署形态切换点（文档 §2.3 / §6.3 的切换预案反向应用）——切库时改动面封闭在该目录内。
- **语义层驱动**：概念抽取由**本地 grok CLI**（headless 单轮，见 `config.py` 的 `grok_exe` / `llm_backend`）驱动，而非文档设想的国产云 API。流水线幂等，换模型可整体重跑。
- **检索层**：确定性的 `impact_analysis`（影响面分析）已交付；`text2cypher`、Local Search 混合检索、向量层（pgvector）与 **MCP Server** 列为 **Roadmap**，接口签名已在 `models.py` / 各包 `__init__` 处预留。

## 限制（照抄文档 §11 R1）

**静态调用图盲区**：动态分发、`functools.wraps` 之外的装饰器包裹、猴子补丁、字符串驱动的调度表等，无法由静态 AST 判定，对应的 `CALLS` 边会缺失，影响面分析可能**漏报**。对策：`call_resolved_rate` 如实公布、不掩饰；`impact --mode imports` 以模块级导入链兜底给出更粗粒度的影响面；本项目明确**不做**运行时插桩，静态方法的固有盲区作为已知限制披露，不试图消除。

概念层为概率产物（LLM 抽取 + 校验），**概念边永不参与 `impact_analysis`**——确定性工具不掺概率数据。

**模糊语义检索的召回上界（v0.3 · 无向量层的定量代价，风险 F9）**：v0.3 新增「口语中文问题 → 真实图谱证据」的模糊检索层（如「终止那块怎么搞的」落到 `_handle_terminate`），但**不引入向量/embedding 层**（架构裁定 D-22：本图 510 节点规模下无收益、维持零第三方依赖），跨语言召回由 BM25 词面命中 + 索引期双语卡片（`zh_desc`/`zh_aliases`）承载。此路线有**结构性召回天花板**：当用户口语与 gold 实体（即便卡片已富化）**零词面（n-gram）交集**时，BM25 无论如何重排都够不到，只有语义近邻能召回。该代价已在**冻结留出集 FZ-test（10 题）**上定量：hit@3=**0.8**，2 个失败题经真实 BM25 全量排名逐题归因**全部为「词面不可达」**（如「喊了暂停」↔`stop 标志消费`、「打个记号」↔`标记`，仅单字重叠不成词元）、**0 个排序失利**——即向量层缺失的净代价 ≈ **FZ-test 的 20%**（`design_work/d3_f9.py` 可复现，产物 `d3_f9.json`）。**口径**：此为 `semantic_mode=lexical` 下的**下界**（S2 改写 / L2 LLM 档增益未计入）；系统如实报告 hit@k、不承诺向量级召回，向量层列为 Roadmap（F9 复审触发 = 词面不可达占比过高时重议 D-21/D-22）。

## License

[MIT](LICENSE) © 2026 bkdtjw
