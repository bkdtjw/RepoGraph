# RepoGraph 技术设计文档

**项目代号**:RepoGraph — 代码知识图谱与混合检索系统
**版本**:v0.1(设计评审稿)
**日期**:2026-07-21
**目标仓库**:Agent Studio(Python/FastAPI)、Hansard(二期,经语言适配层接入)
**交付形态**:开源仓库 + MCP Server(接入 Claude Code / 任意 MCP 客户端)

---

## 1. 项目概述

### 1.1 背景与动机

编码 Agent(Claude Code、Codex 等)的核心瓶颈之一是上下文供给:Agent 依赖 grep / 文件读取获取代码上下文,这类手段只能回答"符号在哪里",无法回答两类高价值问题:

1. **影响面问题(多跳结构推理)**:"修改函数 X 会波及哪些上游调用方 / API 端点?"——答案需要沿调用链和导入链做反向可达性遍历,跨越多个文件。
2. **设计溯源问题(结构 × 语义联合)**:"这个模块的重试策略是基于什么决策引入的?"——答案分散在 commit message、issue 讨论和代码之间,需要把非结构化文本与代码符号关联起来。

RepoGraph 的方案:对仓库做**双源抽取**——代码结构(AST、git 历史)走确定性解析,自然语言(commit message、docstring、README、issue)走 LLM 语义抽取——统一落入属性图,配合向量检索构成混合检索层,以 MCP Server 形式供编码 Agent 调用。

### 1.2 目标(Goals)

- G1:对目标 Python 仓库构建覆盖 模块 / 类 / 函数 / 提交 / Issue / 概念 六类实体的知识图谱,结构关系抽取错误率为零(确定性解析),语义关系带证据与置信度。
- G2:支持三类查询:一跳事实查询、多跳影响面分析(变长路径遍历)、设计决策溯源。
- G3:提供 text2cypher 与 Local Search 混合检索,与纯向量 RAG 基线在自建三层评测集上做量化对比。
- G4:封装为 MCP Server(stdio 传输),在 Claude Code 中完成端到端实测。
- G5:PostgreSQL 单实例同时承载关系元数据、图存储(Apache AGE)与向量索引(pgvector)。

### 1.3 非目标(Non-Goals)

- 不做通用多语言索引器。一期仅 Python;TypeScript 通过 tree-sitter 适配层作为二期扩展点,接口预留但不实现。
- 不做运行时动态追踪(不注入、不插桩),静态调用图的固有盲区(动态分发、反射、装饰器包裹)作为已知限制披露,不试图消除。
- 不做 IDE 插件与 Web 前端,交互面只有 CLI 与 MCP。
- 不声称研究新颖性。Aider repo map、Sourcegraph、Microsoft GraphRAG 为先行工作,本项目定位是**工程整合与 Agent 集成**,差异点在 §1.4。

### 1.4 与现有工作的关系

| 对比对象 | 它做什么 | RepoGraph 的差异 |
|---|---|---|
| ctags / LSP | 符号定义与引用索引("是什么、在哪里") | 补"为什么"(commit/issue 语义层)与自然语言多跳问答接口 |
| Aider repo map | 用 PageRank 压缩仓库结构进 prompt | 结构显式落图、可精确多跳查询,而非一次性摘要 |
| Sourcegraph | 商业级代码搜索与代码智能 | 轻量、自托管、面向 Agent 的 MCP 工具化;引入 commit→概念 语义层 |
| Microsoft GraphRAG | 通用文本开放抽取 + 社区摘要 | 双源抽取;代码实体天生有规范 ID,结构层实体对齐问题被消灭,索引成本低约一个量级 |

---

## 2. 总体架构

### 2.1 分层视图

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

### 2.2 数据流

索引路径:`repo → AST/git 确定性抽取 → 落图(MERGE 幂等) → LLM 语义抽取 → 概念对齐 → 落图 + 文本块向量化入库 → 更新水位`。

查询路径(以 `ask_repo` 为例):`问题 → 实体链接 → [命中] 图查询(模板或 text2cypher)取子图 + pgvector 取原文块 → 上下文拼装 → LLM 生成答案(带引用) / [未命中] 退化为纯向量 RAG`。

### 2.3 技术选型及理由

| 项 | 选型 | 理由 | 备选与切换条件 |
|---|---|---|---|
| 图存储 | PostgreSQL 16 + Apache AGE 1.5(openCypher) | 与现有技术栈(Agent Studio 用 PG)零冲突;单实例三引擎是架构亮点 | AGE 工程手感糙(参数绑定需 prepared statement)。里程碑 W1 结束若阻塞 > 2 天,切 Neo4j Community(Docker),接口层已抽象,切换成本限定在 `store/age.py` 单文件 |
| 向量 | pgvector 0.8(HNSW,cosine) | 同实例;Agent Studio 已有使用经验 | 无 |
| 语言解析 | CPython `ast` 标准库 | 零依赖、与目标仓库 Python 版本一致(3.12) | 二期 TS 用 tree-sitter |
| git | GitPython + 必要处直接调 `git` 命令 | diff/blob 读取成熟 | 无 |
| LLM API | 国产 API(DeepSeek / GLM / Kimi 任一,配置化) | 成本与可得性 | 抽取质量不达标时换更强模型重跑,流水线幂等支持重跑 |
| Embedding | bge-m3(1024 维,本地 sentence-transformers 或 SiliconFlow API,配置化) | 中英混合语料效果稳定 | 维度写入配置,换模型需重建向量表 |
| MCP | Python `mcp` SDK(FastMCP),stdio | 官方 SDK,Claude Code 原生支持 | 无 |

---

## 3. 图谱 Schema

### 3.1 设计原则

1. **结构层 schema-first**:实体与关系类型封闭枚举,抽取输出经 schema 校验后才落图。
2. **实体 vs 属性判定**:需要被独立查询、被多条边连接者建实体(如函数、提交);仅用于描述者做属性(如行号区间、根因文本)。
3. **规范 ID 优先**:代码实体的 ID 由 `仓库 + 相对路径 + 限定名` 确定性生成,结构层不存在实体对齐问题;仅概念层实体需要对齐(§5.4)。这是本项目相对通用 GraphRAG 的核心成本优势,必须在文档与评测中显式量化(对齐工作量对比)。
4. **事件具体化(reification)**:Commit 本身即事件节点,天然承载 n 元事实(时间、作者、改动、修复),无需额外事件建模。

### 3.2 节点类型

| Label | 关键属性 | 说明 |
|---|---|---|
| `Module` | `id, repo, path, name, package, loc, docstring` | 一个 .py 文件对应一个 Module |
| `Class` | `id, repo, qualname, file, span_start, span_end, docstring, bases[]` | `bases` 仅记录仓库内可解析的基类 qualname |
| `Function` | `id, repo, qualname, file, span_start, span_end, signature, is_async, is_method, is_endpoint, http_method, route_path, docstring` | 含方法;端点标记见 §4.3 |
| `Commit` | `id, repo, hash, author, authored_at, message, files_changed, insertions, deletions` | `authored_at` 为 ISO8601 字符串 |
| `Issue` | `id, repo, number, title, state, labels[], created_at, body_excerpt` | `body_excerpt` 截断至 1000 字符 |
| `Concept` | `id, name, ctype, description, aliases[], confidence, evidence[]` | `ctype ∈ {design_decision, domain_concept, constraint}`;evidence 为 `{source_ref, quote}` 列表 |

### 3.3 边类型

| 边 | 方向 | 属性 | 来源 |
|---|---|---|---|
| `CONTAINS` | Module→Class, Module→Function, Class→Function | — | AST |
| `IMPORTS` | Module→Module | `names[]`(被导入符号) | AST(仅仓库内目标;外部依赖以 Module 属性 `external_imports[]` 记录,不建边) |
| `CALLS` | Function→Function | `count, call_sites[]`(行号列表) | AST 调用图解析(§4.2),仅落已解析边 |
| `MODIFIES` | Commit→Function | `lines_added, lines_deleted, overlap_lines` | git diff→函数映射(§4.4) |
| `TOUCHES` | Commit→Module | `lines_added, lines_deleted` | 函数级映射失败时的文件级兜底 |
| `FIXES` | Commit→Issue | `pattern`(命中的关键词) | commit message 正则(§4.5) |
| `PROPOSES` | Issue→Concept | `evidence, confidence` | LLM 语义抽取 |
| `DESCRIBES` | Commit→Concept | `evidence, confidence` | LLM 语义抽取 |
| `IMPLEMENTS` | Function\|Module→Concept | `evidence, confidence` | LLM 语义抽取,目标符号经存在性校验 |

### 3.4 ID 规范

```text
Module   {repo}::{relpath}                    agent-studio::app/core/loop.py
Class    {repo}::{relpath}::{qualname}        agent-studio::app/core/loop.py::AgentLoop
Function {repo}::{relpath}::{qualname}        agent-studio::app/core/loop.py::AgentLoop.run
Commit   {repo}::commit::{sha}                agent-studio::commit::9f3ab12...
Issue    {repo}::issue::{number}              agent-studio::issue::42
Concept  concept::{slug}                      concept::context-compression-3tier(对齐后规范名)
```

约束:`relpath` 使用 POSIX 分隔符;`qualname` 取 `ast` 语义限定名(嵌套用 `.` 连接);同一 blob 内重名(如条件定义)取首个定义并记 `duplicate_defs` 统计。ID 即 `MERGE` 的合并键,保证流水线重跑幂等。

### 3.5 与第 2–5 节(通用 KG 流程)的映射

结构层跳过"知识融合"(规范 ID 免对齐);语义层完整保留"抽取 → 校验 → 对齐 → 合并"四步。评测报告中需给出:结构层三元组数、语义层三元组数、语义层对齐前后实体数——用数字支撑"对齐问题被压缩到概念层"的论断。

---

## 4. 确定性抽取层

### 4.1 AST 结构抽取

对工作树内所有 `.py` 文件(排除 `tests/`、`migrations/`、生成代码目录,排除规则配置化):

```python
import ast, pathlib

def extract_module(repo: str, relpath: str, source: str) -> ModuleFacts:
    tree = ast.parse(source)                       # SyntaxError → 记入 skip 名单并告警
    facts = ModuleFacts(id=f"{repo}::{relpath}")
    for node in ast.walk(tree):
        match node:
            case ast.ClassDef():
                facts.classes.append(_class_facts(node))
            case ast.FunctionDef() | ast.AsyncFunctionDef():
                facts.functions.append(_func_facts(node))
            case ast.Import() | ast.ImportFrom():
                facts.imports.append(_import_facts(node))
    return facts
```

关键实现约定:

- **行号跨度**:`span_start = min(node.lineno, *[d.lineno for d in node.decorator_list])`(装饰器行计入函数跨度,否则 diff 映射会漏掉纯改装饰器的提交);`span_end = node.end_lineno`(要求 Python ≥ 3.8)。
- **限定名**:自维护作用域栈生成(`ast.walk` 不保序,实际实现用 `NodeVisitor` 递归以正确维护栈)。
- **docstring**:`ast.get_docstring(node)`,原文进向量层,首行进节点属性。
- **签名**:由 `node.args` 重建为字符串,不做类型求值。

### 4.2 调用图解析算法

两遍扫描,仅产出**可静态判定**的 `CALLS` 边,不猜测:

**Pass 1(全局符号表)**:遍历全部模块,建立 `qualname → node_id` 索引;逐模块建立导入映射:`import a.b as c → {c: "a.b"}`,`from a.b import f as g → {g: "a.b.f"}`。

**Pass 2(逐函数解析调用点)**:对每个 `ast.Call`,按以下顺序解析 callee:

1. `Name(id=x)`:同模块局部定义 → 直连;否则查 from-import 映射 → 目标模块符号(仅当目标在仓库内且符号存在)。
2. `Attribute(value=Name(id=m), attr=f)`:`m` 在导入映射中 → `{m 映射的模块}.f`,存在性校验后连边。
3. `Attribute(value=Name(id='self'|'cls'), attr=f)`:解析为所属类的方法;类内不存在时沿 `bases` 在**仓库内**做单层查找,仍未命中则放弃。
4. 其余(变量上的方法调用、`getattr`、回调、装饰器注入)一律计入 `unresolved`,**不建边**。

产出质量指标 `call_resolved_rate = resolved / (resolved + unresolved)`,由 `repograph stats` 输出并写入 README。该指标以实测为准,不预设数值;它同时是面试中"你如何度量图谱质量"的直接答案。

已知盲区(写入 §11 风险):动态分发、`functools.wraps` 之外的装饰器包裹、猴子补丁、字符串驱动的调度表。

### 4.3 端点识别

Agent Studio 后端为 FastAPI。识别规则(配置化的装饰器模式列表):

```python
ENDPOINT_PATTERNS = [
    r"^(app|router)\.(get|post|put|delete|patch|websocket)$",
]
```

装饰器匹配 `Attribute` 链且首个位置参数为字符串字面量时,写入 `is_endpoint=True, http_method, route_path`。Feishu Bot 回调、CLI 入口等其他"入口型函数"通过追加模式接入,识别不到不硬编码。端点标记是影响面分析(§7.2)把"波及的函数"进一步汇聚为"波及的 API"的依据。

### 4.4 Git 历史抽取与 diff→函数映射

对每个提交 C(首个父提交 P,合并提交只取第一父;开启 rename 检测 `find_renames=True`):

1. 取 `C.diff(P, create_patch=True)` 中的 `.py` 文件变更。
2. 解析 unified diff 的 hunk 头 `@@ -a,b +c,d @@`:新增/修改取新侧区间 `[c, c+d-1]`;**纯删除 hunk**(d=0)取旧侧区间,映射到 P 版本的函数跨度(删除同样构成"该提交动了这个函数")。
3. 取 `git show C:path` 的 blob,`ast.parse` 得到**该提交时点**的函数跨度表——不能用 HEAD 的跨度表,历史版本行号早已漂移。
4. 变更区间与函数跨度求交,非空则建 `MODIFIES` 边,记录重叠行数。
5. blob 解析失败(历史语法错误等)→ 降级为 `TOUCHES(Commit→Module)`,记入 `mapping_fallbacks` 统计。

**性能设计**:函数跨度表按 `blob_sha` 缓存(同一 blob 在多个提交间复用,缓存命中率在线性历史上通常很高);解析范围严格限定为"每个提交实际变更的文件",总成本 O(Σ 变更文件数) 而非 O(提交数 × 文件数)。

**映射目标锚定**:`MODIFIES` 的目标函数 ID 使用**当前 HEAD 中同 qualname 的节点**;若 HEAD 中已不存在(函数被删/改名且 rename 检测未覆盖),记为悬空映射并计数,不建边。这一取舍(牺牲已删除函数的历史,换取图中无幽灵节点)写入文档限制说明。

### 4.5 Issue 抽取与关联

GitHub REST API 拉取 issues(含已关闭),落 `Issue` 节点。`FIXES` 边由 commit message 正则产生:

```python
FIXES_RE = re.compile(
    r"(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s+#(\d+)", re.IGNORECASE
)
```

同时解析 GitHub squash-merge 标题中的 `(#N)` 引用为弱关联(属性 `pattern="pr_ref"`),与显式 fixes 区分。

### 4.6 确定性层质量指标(repograph stats 输出)

| 指标 | 定义 |
|---|---|
| `call_resolved_rate` | 已解析调用点 / 全部调用点 |
| `modifies_coverage` | 产生 ≥1 条 MODIFIES 的代码提交 / 全部代码提交 |
| `dangling_modifies` | 因目标函数消失被丢弃的映射数 |
| `parse_skips` | 语法错误跳过的文件(工作树)与 blob(历史)数 |
| `blob_cache_hit_rate` | 跨度表缓存命中率 |

---

## 5. 语义抽取层

### 5.1 输入单元

| 来源 | 切分方式 | 预估规模(以两仓库量级估算,实测为准) |
|---|---|---|
| commit message | 按提交,每 20 条一批送入 LLM(降低调用次数) | 数百至一千余条 |
| docstring(模块级 + 类级) | 单条 | 数百条 |
| README / docs | 按二级标题切分 | 数十段 |
| Issue 标题 + 正文 | 单条,正文截断 2000 字符 | 数十条 |

函数级 docstring 不进语义抽取(信噪比低),只进向量层。

### 5.2 抽取 Prompt 与输出契约

单一职责:从文本中抽取 `Concept` 实体及其与来源对象的边(`DESCRIBES` / `PROPOSES` / `IMPLEMENTS`)。输出契约(Pydantic 校验,失败重试 1 次,再失败丢弃并计数):

```json
{
  "concepts": [
    {
      "name": "三级可续传上下文压缩",
      "ctype": "design_decision",
      "description": "以 180K 预算为界,分三级压缩历史消息,压缩过程可中断续传",
      "evidence": {"source_ref": "agent-studio::commit::9f3ab12", "quote": "introduce 3-tier resumable compression under 180k budget"},
      "confidence": 0.9,
      "implements_candidates": ["app/core/context.py::ContextManager.compress"]
    }
  ]
}
```

关键约束(写入 system prompt,全文见附录 B):

1. **只抽文本明说的**,不做推断;`quote` 必须是原文连续子串(落图前程序校验子串成立,不成立则 confidence 减半并标记)。
2. `implements_candidates` 只能从**随 prompt 附带的候选符号列表**中选择——候选列表由程序生成(该提交 MODIFIES 的函数 ∪ 该文档所在模块的符号),从机制上杜绝 LLM 幻觉出不存在的目标;落图前再做一次存在性校验(双保险)。
3. 无概念可抽时返回空数组,禁止凑数。

### 5.3 校验与落图

流水线:`JSON 解析 → Pydantic schema 校验 → quote 子串校验 → 目标符号存在性校验 → confidence ≥ 0.6 过滤 → 暂存 staging 表(不直接落图)`。全部批次完成后统一进入对齐(§5.4),对齐完成才落图。校验各环节的拒绝计数进 `repograph stats`(`extraction_reject_by_reason`)。

### 5.4 概念对齐

仅概念层需要对齐,采用 blocking + matching 两级(与检索的召回 + 重排同构):

**Blocking(候选生成)**:对 staging 中每个概念,取 `name + " " + description` 做 embedding,与已有规范概念做余弦相似度;`sim ≥ 0.85` 或规范化名称精确相等(小写、去空格连字符)进入候选对。阈值配置化,以人工抽检 30 对校准。

**Matching(裁决)**:候选对连同双方 evidence 交给 LLM 三分类:`same / different / unsure`。`same` → 合并(保留高置信度者为规范名,另一名进 `aliases`,evidence 取并集);`unsure` → 不合并,记录待人工;`different` → 各自独立。裁决结果与理由落审计日志(JSONL),供抽检。

**合并后落图**:`MERGE (c:Concept {id: $canonical_id})`,边随规范 ID 重定向。

### 5.5 成本估算

以上限估:输入 ≈ 1500 条 commit message × 平均 120 token × 批处理开销 1.5 + 600 条 docstring/文档段 × 400 token + 对齐裁决 ≈ 全流程输入 1–3M token,输出一个量级以下。按国产 API 价格,单次全量重跑成本在个位数至十余元人民币;流水线幂等,允许换模型整体重跑对比抽取质量。

---

## 6. 存储设计

### 6.1 单实例三引擎

PostgreSQL 16 一个实例承载:AGE(图)、pgvector(向量)、普通关系表(chunk 元数据、别名、水位、staging、审计)。镜像自建:

```dockerfile
FROM apache/age:release_PG16_1.5.0
RUN apt-get update && apt-get install -y --no-install-recommends \
      git build-essential postgresql-server-dev-16 \
 && git clone --depth 1 --branch v0.8.0 https://github.com/pgvector/pgvector.git /tmp/pgvector \
 && cd /tmp/pgvector && make && make install \
 && rm -rf /tmp/pgvector && apt-get purge -y git build-essential && apt-get autoremove -y
```

### 6.2 关系表 DDL(核心)

```sql
CREATE EXTENSION IF NOT EXISTS age;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE chunk (
  id           TEXT PRIMARY KEY,          -- {repo}::chunk::{source_type}::{hash}
  repo         TEXT NOT NULL,
  source_type  TEXT NOT NULL CHECK (source_type IN ('docstring','readme','commit_msg','issue','code')),
  source_ref   TEXT NOT NULL,             -- 关联的图节点 ID,构成"块↔节点"双向锚
  content      TEXT NOT NULL,
  token_count  INT,
  embedding    vector(1024)
);
CREATE INDEX chunk_embedding_idx ON chunk USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunk_source_ref_idx ON chunk (source_ref);

CREATE TABLE symbol_alias (               -- 实体链接用:短名/别名 → 节点 ID
  alias      TEXT NOT NULL,
  entity_id  TEXT NOT NULL,
  kind       TEXT NOT NULL,               -- exact | suffix | concept_alias
  PRIMARY KEY (alias, entity_id)
);

CREATE TABLE index_meta (
  repo         TEXT PRIMARY KEY,
  last_commit  TEXT NOT NULL,
  indexed_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE concept_staging ( ... );     -- §5.3,字段与抽取契约一致
CREATE TABLE align_audit    ( ... );      -- §5.4 裁决审计
```

`chunk.source_ref` 是混合检索的关键设计:向量命中的块能反查图节点,图命中的节点能正查其原文块(§7.4)。

### 6.3 AGE 初始化与查询模式

```sql
LOAD 'age';
SET search_path = ag_catalog, "$user", public;
SELECT create_graph('repograph');
-- 属性 GIN 索引(AGE 将每个 label 存为 graph schema 下的表)
CREATE INDEX ON repograph."Function" USING gin (properties);
CREATE INDEX ON repograph."Commit"   USING gin (properties);
```

**参数化查询**:AGE 的 `cypher()` 第三参数为 agtype 参数映射,必须经 prepared statement 传入,这是 AGE 与 Neo4j 驱动体验差异最大的点,封装进 `store/age.py` 的唯一入口:

```sql
PREPARE q_callers(agtype) AS
SELECT * FROM cypher('repograph', $$
  MATCH (caller:Function)-[:CALLS]->(t:Function {id: $fid})
  RETURN caller.id, caller.qualname
$$, $1) AS (id agtype, qualname agtype);

EXECUTE q_callers('{"fid": "agent-studio::app/core/tools.py::ToolRunner.run"}');
```

```python
# store/age.py 对外唯一入口(psycopg 3)
def run_cypher(conn, query: str, params: dict, columns: list[str]) -> list[dict]:
    """query 中以 $name 引用参数;本函数负责 PREPARE/EXECUTE 与 agtype 反序列化。
    写查询与读查询走不同连接池角色(§8.4)。"""
```

**写入幂等**:所有节点 `MERGE ... ON CREATE SET ... ON MATCH SET ...` 以 §3.4 的 ID 为合并键;边以 `(src_id, type, dst_id)` MERGE。

**Cypher 特性使用约束**:限定在 AGE 1.5 已验证子集内——`MATCH / OPTIONAL MATCH / MERGE / WHERE / RETURN / ORDER BY / LIMIT / collect / count / UNWIND / 变长路径 *m..n`。不使用 shortestPath 与 APOC 类能力;W1 落库当天以冒烟脚本逐条验证上述子集,任何缺失立即触发 §2.3 的 Neo4j 切换预案。

### 6.4 向量层

Embedding 模型配置化(默认 bge-m3,1024 维);维度变更 = 重建 `chunk` 表,以 `repograph reindex-vectors` 一键完成。入库前按 token 上限 512 截断,超长文档段二次切分。

### 6.5 幂等与增量更新

- **水位**:`index_meta.last_commit` 记录已索引提交;增量 = `git rev-list last_commit..HEAD` 的新提交,仅对新提交跑 §4.4 映射与 §5 语义抽取。
- **工作树结构层增量**:对比两次索引间变更的文件集合,对每个变更文件执行"删除该 `Module` 及其 `CONTAINS` 闭包内节点与关联边 → 重建"。删除以文件属性定位:`MATCH (n) WHERE n.file = $path DETACH DELETE n`(封装为模板,普通用户角色无权执行,见 §8.4)。
- **概念层**:追加式抽取 + 每次增量后全量重跑对齐(概念规模小,全量对齐成本可忽略),避免增量对齐的顺序依赖问题。

---

## 7. 检索与查询

### 7.1 实体链接

输入:自然语言问题。流程:

1. **词面候选**:问题分词后,n-gram 与 `symbol_alias` 精确/后缀匹配(`ToolRunner.run`、`tools.py`、概念别名均已在索引期展开为别名行:qualname 全名、类名、函数短名、文件名)。
2. **向量兜底**:词面无命中时,问题 embedding 与"实体卡片块"(每个节点的 `qualname + docstring 首行` 预生成的 chunk,`source_type='code'`)做 top-5 相似度,阈值 0.5 以下判定为无锚点。
3. 输出:`[(entity_id, score, method)]`;无锚点 → `ask_repo` 退化为纯向量 RAG 并在响应中标注 `mode="vector_only"`。

### 7.2 影响面分析(模板查询,非 LLM 生成)

确定性能力用确定性实现——`impact_analysis` 不走 text2cypher,使用参数化模板。变长上界在 openCypher 中**不可参数化**,由服务端以白名单整数(1–4)拼入查询串,这是唯一允许的字符串拼接点,且拼接值经 `int` 校验:

```cypher
-- depth 由服务端白名单拼入;$fid 走参数
MATCH (t:Function {id: $fid})
OPTIONAL MATCH (caller:Function)-[:CALLS*1..3]->(t)
WITH t, collect(DISTINCT caller) AS callers
UNWIND (callers + [t]) AS c
OPTIONAL MATCH (m:Module)-[:CONTAINS]->(c)
RETURN c.id, c.qualname, c.is_endpoint, c.http_method, c.route_path, m.path
```

返回结构分层:`直接调用方(1 跳) / 传递调用方(2..d 跳) / 波及端点(is_endpoint 过滤) / 波及模块`。同一工具的 `mode="imports"` 变体沿 `IMPORTS` 反向闭包给出模块级影响面,用于函数级调用图未覆盖的场景(动态调用密集的模块)。

设计溯源为第二个模板:

```cypher
MATCH (f:Function {id: $fid})<-[:MODIFIES]-(c:Commit)
OPTIONAL MATCH (c)-[:DESCRIBES]->(k:Concept)
OPTIONAL MATCH (c)-[:FIXES]->(i:Issue)
RETURN c.hash, c.authored_at, c.message, k.name, k.description, i.number, i.title
ORDER BY c.authored_at
```

### 7.3 text2cypher(开放式图查询)

供模板覆盖不到的长尾问题使用。Prompt = schema 摘要(§3.2/3.3 的紧凑版)+ 3 个 few-shot + 规则(全文见附录 B)。四重防护:

1. **PG 角色隔离**:查询连接使用 `repograph_ro` 角色,仅对 `repograph` graph schema 与 `chunk` 表授 SELECT。AGE 的图写操作最终落为对 label 表的 INSERT/UPDATE,无 INSERT 权限的角色在数据库层被硬拦截——这是真正的强制层。
2. **关键词过滤**:`\b(CREATE|MERGE|SET|DELETE|REMOVE|DROP|CALL|LOAD)\b` 大小写不敏感命中即拒绝(纵深防御,非主防线)。
3. **LIMIT 与超时**:无 LIMIT 则注入 `LIMIT 50`;连接级 `SET statement_timeout = '5s'`。
4. **失败反馈重试 1 次**:执行报错时把错误信息回传 LLM 重生成一次;再失败则降级 Local Search,响应标注 `degraded=true`。

### 7.4 Local Search(混合上下文拼装)

`ask_repo` 的主路径。输入问题与链接到的实体,输出拼装好的上下文与最终答案:

1. **子图**:对每个锚点实体取 k=2 跳邻域(边类型全集,方向双向,节点上限 40,超限按边类型优先级截断:`CALLS/MODIFIES > CONTAINS/IMPORTS > 概念边`)。
2. **原文块**:两路取块后合并去重——(a) 子图内全部节点经 `chunk.source_ref` 正查其原文块;(b) 问题 embedding 对 `chunk` 全表 top-8。
3. **拼装格式**(预算 6000 token,超限按序截断:实体卡片 > 1 跳三元组 > 2 跳三元组 > 原文块):

```text
[实体卡片]
Function agent-studio::app/core/tools.py::ToolRunner.run
  signature: def run(self, call: ToolCall) -> ToolResult
  docstring: 执行单次工具调用,含三层拦截……

[子图三元组]
(ToolRunner.run) -[CALLS]-> (SecurityAuditor.audit)
(commit 9f3ab12) -[MODIFIES]-> (ToolRunner.run)
(commit 9f3ab12) -[DESCRIBES]-> (概念: HMAC 签名三层拦截)

[原文片段]
<commit 9f3ab12 message> ……
<docstring app/core/security.py> ……
```

4. **生成**:上下文 + 问题送 LLM,要求答案逐条标注来源引用(`[commit 9f3ab12]` 形式);无法从上下文得出时明确说"图谱未覆盖",禁止编造。

### 7.5 基线:纯向量 RAG

与主链路共用同一 `chunk` 表与同一生成 prompt,仅检索方式不同(问题 embedding top-8,无图谱上下文)。保证对比实验只有一个自变量。

### 7.6 可选:社区摘要(全局问题)

W4 的可选项,时间不足直接砍(§12)。若做:`igraph + leidenalg` 对结构层子图(CALLS + IMPORTS)做社区检测,每社区生成一段 LLM 摘要存为 `source_type='community'` 的 chunk;全局类问题(评测集 L3)对社区摘要做一轮 map-reduce。明确记录:此项照搬 GraphRAG 思路的最小实现,不是本项目主菜。

---

## 8. MCP Server

### 8.1 架构与传输

Python `mcp` SDK(FastMCP),stdio 传输,单进程内直连 PG 连接池。不引入独立服务进程,Claude Code 按需拉起。

### 8.2 工具定义(共 3 个,刻意克制)

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("repograph")

@mcp.tool()
def impact_analysis(symbol: str, depth: int = 3, mode: str = "calls") -> dict:
    """分析修改某符号的影响面。
    symbol: 限定名或其唯一后缀(如 'ToolRunner.run');depth: 1-4;
    mode: 'calls'(函数级调用链) | 'imports'(模块级导入链)。
    返回 {resolved_symbol, direct_callers[], transitive_callers[],
          affected_endpoints[], affected_modules[], truncated}"""

@mcp.tool()
def query_graph(question: str) -> dict:
    """将自然语言问题翻译为图查询并执行(text2cypher,只读)。
    适合精确结构问题:'哪些函数没有被任何测试外的代码调用''改动次数最多的前 10 个函数'。
    返回 {cypher, rows[], degraded}"""

@mcp.tool()
def ask_repo(question: str) -> dict:
    """仓库混合问答(图谱子图 + 向量原文)。适合开放问题与设计溯源:
    '上下文压缩这套设计是怎么演化来的'。
    返回 {answer, citations[], mode}"""
```

三个工具对应三种确定性等级:模板查询(全确定)→ text2cypher(LLM 生成、强防护)→ 混合问答(LLM 生成、带引用)。工具描述中写明适用场景,让调用方 Agent 自行路由——这本身是一次面向 Agent 的 API 设计练习。

### 8.3 Claude Code 接入

项目根 `.mcp.json`:

```json
{
  "mcpServers": {
    "repograph": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/repograph", "repograph", "mcp"],
      "env": { "REPOGRAPH_DSN": "postgresql://repograph_ro:...@localhost:5432/repograph" }
    }
  }
}
```

验收动作(W5):在 Claude Code 中执行三个真实任务并录屏——(a) 让其重构 `ToolRunner.run` 前先调 `impact_analysis`;(b) 问一个设计溯源问题观察其调用 `ask_repo`;(c) 对比关闭 MCP 后同任务的行为差异,差异写入 README 的 Motivation 一节。

### 8.4 安全

- 连接双角色:`repograph_rw` 仅供索引流水线;MCP 进程持 `repograph_ro`(SELECT-only,§7.3 防护第 1 层)。
- MCP 工具全部只读,无任何写图工具。
- `statement_timeout=5s`、结果行数上限、返回体 token 上限(截断置 `truncated=true`)。
- 该项目为本地单用户工具,不做鉴权;若未来暴露网络传输(SSE),再引入 token 鉴权,当前明确不做(非目标)。

---

## 9. 评测方案

### 9.1 数据集构建

自建 90 题 JSONL(`eval/dataset.jsonl`),作者依据 ground truth 出题,三层分布:

| 层 | 数量 | 定义 | 示例 |
|---|---|---|---|
| L1 局部事实 | 30 | 单点查找即可回答 | "`ContextManager.compress` 定义在哪个文件?" |
| L2 多跳结构 | 40 | 需 ≥2 跳图遍历或跨源关联 | "修改 `SecurityAuditor.audit` 会波及哪些 API 端点?""4-Zone 消息分区是哪个 issue 引入、哪次提交实现的?" |
| L3 全局概括 | 20 | 需通盘归纳 | "Agent Studio 的容错设计集中在哪几个子系统?" |

单条格式:

```json
{"qid":"L2-013","type":"multihop","question":"...",
 "gold_answer":"...","gold_entities":["agent-studio::app/core/security.py::SecurityAuditor.audit"],
 "gold_paths":[["SecurityAuditor.audit","CALLS←","ToolRunner.run","CALLS←","AgentLoop.step"]]}
```

出题纪律:问题在写系统**之前**冻结一半(45 题)防止对着实现出题的偏置;另一半在 W4 依据图谱统计补齐覆盖面。两仓库各占约一半。

### 9.2 指标

| 指标 | 适用层 | 计算 |
|---|---|---|
| 答案准确率 | L1/L2 | LLM 裁判三档(正确/部分/错误,rubric 见附录 B),correct 计 1、partial 计 0.5;全部 partial 与 10% 随机样本人工复核 |
| 上下文实体召回 | L1/L2 | gold_entities 出现在拼装上下文中的比例(检索质量与生成质量解耦的关键指标) |
| 路径正确率 | L2 | 答案引用的路径与 gold_paths 的集合重合度(仅图谱侧可评,基线记 N/A——这本身就是可解释性论据) |
| 覆盖度评分 | L3 | 每题预置 3–5 个 gold aspects,裁判判定命中数 / 总数 |
| 效率 | 全部 | 端到端延迟 P50/P95、每题 LLM token 消耗 |

### 9.3 对比与消融

固定生成模型与 prompt,四组:纯向量 RAG(基线)/ 仅图谱(子图不带原文块)/ 混合(主方案)/ 混合+社区摘要(若 §7.6 实现)。预期(待验证的假设,报告中如实呈现):L1 基线与混合打平;L2 混合显著占优;L3 无社区摘要时两者都差。**假设被证伪同样是结论**,照实写。

### 9.4 判分协议

裁判用强模型(与被评生成模型不同源),输入 `question + gold_answer + candidate`,禁止访问上下文(只评答案);同一裁判、同一温度(0)评全部四组;裁判 prompt 冻结后不得修改,修改即全量重评。

### 9.5 报告模板

`eval/report.md` 固定结构:实验设置 → 主表(4 组 × 5 指标 × 3 层)→ 分层分析 → 失败案例各层 3 例(含检索到的上下文,定位是检索失败还是生成失败)→ 结论与限制。简历上的数字全部出自主表。

---

## 10. 工程实现

### 10.1 目录结构

```text
repograph/
├── pyproject.toml            # uv 管理;Python 3.12
├── docker/pg/Dockerfile      # AGE + pgvector 镜像(§6.1)
├── docker-compose.yml
├── src/repograph/
│   ├── config.py             # pydantic-settings;全部可调项集中于此
│   ├── cli.py                # typer
│   ├── extract/
│   │   ├── ast_extractor.py  # §4.1
│   │   ├── callgraph.py      # §4.2 两遍解析
│   │   ├── endpoints.py      # §4.3
│   │   ├── git_extractor.py  # §4.4 diff→函数映射 + blob 缓存
│   │   ├── issues.py         # §4.5
│   │   └── semantic.py       # §5 LLM 抽取
│   ├── fuse/align.py         # §5.4 blocking + matching
│   ├── store/
│   │   ├── ddl.sql
│   │   ├── age.py            # run_cypher 唯一入口;Neo4j 切换点
│   │   └── vector.py
│   ├── retrieve/
│   │   ├── linker.py         # §7.1
│   │   ├── impact.py         # §7.2 模板
│   │   ├── text2cypher.py    # §7.3
│   │   └── local_search.py   # §7.4
│   ├── mcp_server.py         # §8
│   └── eval/
│       ├── dataset.jsonl
│       ├── run_eval.py
│       └── judge.py
└── tests/                    # 抽取层单测以固定 fixture 仓库为准
```

### 10.2 CLI

```text
repograph index --repo PATH --name NAME [--full|--incremental]   # 结构层 + git 层
repograph semantic --name NAME                                   # 语义抽取 → staging
repograph align                                                  # 对齐并落图
repograph stats                                                  # §4.6/§5.3 全部质量指标
repograph ask "问题"                                             # 命令行版 ask_repo
repograph eval [--groups baseline,hybrid,...]
repograph mcp                                                    # 启动 MCP Server(stdio)
repograph reindex-vectors                                        # 换 embedding 模型后重建
```

### 10.3 可观测

结构化日志(JSONL):每次抽取批次、每次 LLM 调用(model、token、耗时、拒绝原因)、每次图查询(cypher、行数、耗时、是否降级)。`stats` 与评测报告直接消费日志,不另建统计通道。

### 10.4 部署

```yaml
# docker-compose.yml
services:
  db:
    build: ./docker/pg
    environment:
      POSTGRES_DB: repograph
      POSTGRES_PASSWORD: ${PG_PASSWORD}
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
volumes:
  pgdata:
```

应用侧本机 `uv run`,不容器化(本地单用户工具,降低演示门槛)。环境变量:`REPOGRAPH_DSN / LLM_API_KEY / LLM_BASE_URL / LLM_MODEL / EMBED_BACKEND / GITHUB_TOKEN`。

---

## 11. 风险、限制与对策

| # | 风险/限制 | 影响 | 对策 |
|---|---|---|---|
| R1 | 静态调用图盲区(动态分发、装饰器包裹、猴子补丁) | CALLS 边不完整,影响面分析漏报 | `call_resolved_rate` 如实公布;`imports` 模式兜底模块级影响面;README 限制一节明示,不掩饰 |
| R2 | AGE 工程成熟度(参数绑定繁琐、Cypher 子集缺口) | 开发阻塞 | W1 冒烟脚本前置验证;>2 天阻塞触发 Neo4j 切换预案,切换面封闭在 `store/age.py` |
| R3 | LLM 语义抽取幻觉/漏抽 | 概念层可信度 | quote 子串校验 + 候选符号白名单 + confidence 过滤 + 审计日志抽检;概念边永不参与 impact_analysis(确定性工具不掺概率数据) |
| R4 | MODIFIES 悬空映射(函数被删/大规模改名) | 历史边丢失 | rename 检测开启;悬空计数公布;接受该取舍(§4.4) |
| R5 | 评测自建自评的偏置 | 数字可信度 | 一半题目先冻结;裁判与生成模型异源;失败案例全量附录;表述上只声称"在自建评测集上" |
| R6 | 两仓库规模偏小,L3 全局题区分度不足 | L3 结论弱 | 如实呈现;必要时 L3 降为定性案例分析 |
| R7 | Hansard 若为 TS 为主,一期覆盖不到其代码结构层 | 图谱只含其 git/issue/概念层 | 文档明示一期边界;tree-sitter 适配层列为 Roadmap 首项 |

---

## 12. 里程碑与验收标准

| 周 | 交付 | 量化验收(exit criteria) |
|---|---|---|
| W1 | 结构层:AST + 调用图 + git 映射 + AGE 落图 | 两仓库全量索引跑通;`stats` 输出全部指标;AGE 冒烟脚本 100% 通过或已完成 Neo4j 切换;示例 Cypher(附录 A)全部可执行 |
| W2 | 语义层:抽取 + 校验 + 对齐 | staging 拒绝率、对齐合并数有数;人工抽检 30 条概念,主观可信率 ≥ 80%,否则调 prompt 重跑 |
| W3 | 检索层:linker + impact 模板 + text2cypher + local search + 向量基线 | 附录 A 的 8 个代表问题端到端全部出正确答案;text2cypher 防护四层各有单测 |
| W4 | 评测:90 题数据集 + 四组对比 + 报告 | `eval/report.md` 主表完整;每层 ≥3 个失败案例分析;(可选)社区摘要 |
| W5 | MCP Server + Claude Code 实测 | §8.3 三个验收动作完成并录屏;.mcp.json 一键接入验证 |
| W6 | 开源:README、架构图、demo GIF、许可证 | README 含 quick start(<5 分钟起库)、指标表、限制声明;仓库打 v0.1 tag |

依赖关系:W2 依赖 W1 的 MODIFIES 边(候选符号列表);W4 依赖 W3;W5 只依赖 W3,可与 W4 并行。总缓冲:每周计划占用 ≤ 10 小时,西藏实习期业余推进,累计缓冲 1 周。

---

## 附录 A:代表性 Cypher 查询清单(冒烟 + 演示两用)

```cypher
-- A1 一跳:某函数定义位置与签名
MATCH (m:Module)-[:CONTAINS]->(f:Function {qualname:'ContextManager.compress'})
RETURN m.path, f.signature, f.span_start;

-- A2 影响面(见 §7.2 模板)

-- A3 设计溯源(见 §7.2 第二模板)

-- A4 热点函数:被修改次数 Top 10
MATCH (c:Commit)-[:MODIFIES]->(f:Function)
RETURN f.qualname, count(c) AS n ORDER BY n DESC LIMIT 10;

-- A5 修 bug 最多牵连的模块
MATCH (c:Commit)-[:FIXES]->(:Issue)
MATCH (c)-[:MODIFIES]->(f:Function)<-[:CONTAINS]-(m:Module)
RETURN m.path, count(DISTINCT c) AS fixes ORDER BY fixes DESC LIMIT 10;

-- A6 概念的实现落点
MATCH (k:Concept {name:'三级可续传上下文压缩'})<-[:IMPLEMENTS]-(x)
RETURN labels(x), x.qualname, x.path;

-- A7 无入边函数(候选死代码,排除端点)
MATCH (f:Function) WHERE f.is_endpoint = false
  AND NOT EXISTS { MATCH (:Function)-[:CALLS]->(f) }
RETURN f.qualname LIMIT 50;

-- A8 某端点的完整依赖闭包(正向)
MATCH (e:Function {is_endpoint:true, route_path:'/v1/chat'})-[:CALLS*1..4]->(g:Function)
RETURN DISTINCT g.qualname;
```

(A7 的 `EXISTS` 子查询语法若 AGE 不支持,改写为 `OPTIONAL MATCH + WHERE caller IS NULL`,冒烟脚本二选一固化。)

## 附录 B:Prompt 模板(冻结版随代码入库,此处为骨架)

**B1 语义抽取 system prompt 要点**:角色定义 → 输出 JSON schema 全文 → 五条规则(只抽明说、quote 必须为原文连续子串、implements_candidates 只能取自候选列表、无可抽返回空、confidence 校准说明)→ 1 个正例 + 1 个"应返回空"的反例。

**B2 text2cypher system prompt 要点**:schema 紧凑表(label/边/属性)→ 输出约束(单条语句、必须 MATCH 起手、必须 RETURN、必须 LIMIT ≤ 50、禁用写关键词)→ 3 个 few-shot(一跳 / 聚合 / 变长路径)→ "无法表达时输出 `UNSUPPORTED`"逃生口。

**B3 评测裁判 prompt 要点**:三档定义(correct:关键事实全对;partial:主体对但遗漏/含次要错误;wrong)→ 只依据 gold_answer 判断、禁止使用自身知识补全 → 输出 `{"verdict":..., "reason":...}`。

## 附录 C:术语表

| 术语 | 定义 |
|---|---|
| 规范 ID | 由仓库+路径+限定名确定性生成的实体主键,MERGE 合并键 |
| 悬空映射 | diff 命中的历史函数在 HEAD 已不存在,被丢弃的 MODIFIES 边 |
| 候选符号白名单 | 语义抽取时随 prompt 附带的、程序生成的合法 IMPLEMENTS 目标集合 |
| 水位 | `index_meta.last_commit`,增量索引起点 |
| 降级 | text2cypher 失败回退 Local Search、或无实体锚点回退纯向量 RAG 的行为,均在响应中显式标注 |
