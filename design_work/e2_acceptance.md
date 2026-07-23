# E2 验收动作实测 + 用户录屏操作手册（Phase E · D-23 复归 · D-14/D-N8）

> **性质**：archive `docs/archive/RepoGraph-技术设计文档.md` §8.3 定义的**三个 Claude Code 验收动作**在本机的**等价真实调用实测**记录，外加供用户亲自录屏的 step-by-step 操作手册。
>
> **实测方式**：把 `python -m repograph.mcp_server` 拉起为**子进程**，走真实换行分隔 JSON-RPC 2.0 stdio（非只测 import），对真实 `output/graph.json`（被索引仓库 `multi-agent-orch` 快照，510 节点 / 1698 边）产出的**真实检索值**做记录。复现脚本：`design_work/e2_acceptance_probe.py`（`python design_work/e2_acceptance_probe.py`）。
>
> **服务器**：`serverInfo={name: repograph, version: 0.3.0}`，`protocolVersion=2025-06-18`（回显客户端所请求），`tools/list=[ask_repo, impact_analysis, repo_overview]`。选型见 DECISIONS **D-N8**（stdlib 而非官方 SDK）；第四工具 `query_graph` 推迟见 **D-N7**。
>
> **§8.3 原文三动作**：(a) 让其重构 `ToolRunner.run` 前先调 `impact_analysis`；(b) 问一个设计溯源问题观察其调用 `ask_repo`；(c) 对比关闭 MCP 后同任务的行为差异，差异写入 README 的 Motivation 一节。**本仓无 `ToolRunner.run`**（那是归档设想架构的符号）；取被索引仓库中的**真实等价符号**执行，逐一标注映射。

---

## 动作 A — 重构前先调 `impact_analysis`

**§8.3 映射**：归档设想「重构 `ToolRunner.run`」。本仓被索引的 `multi-agent-orch` 无 `ToolRunner`，取两个真实符号：主用 `_handle_terminate`（真实热点函数，被 6 次提交修改、有真实调用方，最能体现「改它之前先看波及面」）；对照 `ChaosHarness.run`（本仓**唯一** `.run` 方法，是 `ToolRunner.run` 的字面同形）。

### A.1 输入
```
tools/call impact_analysis {"symbol": "_handle_terminate", "depth": 2, "mode": "calls"}
```
### A.1 输出（真实，isError=false）
| 字段 | 值 |
|---|---|
| resolved_symbol | `multi-agent-orch::src/orch/scheduler/core.py::_handle_terminate` |
| direct_callers (3) | `_dispatch_group_async`, `_dispatch_group`, `_finish_interrupted_terminate` |
| transitive_callers (2) | `run_thread_async`, `run_thread` |
| affected_modules (2) | `src/orch/scheduler/async_core.py`, `src/orch/scheduler/core.py` |
| truncated | **true**（depth=2 处闭包截断，尚有更上游调用方；depth=3 收敛为 truncated=false、间接 3 个） |

### A.2 输入（`.run` 字面同形对照）
```
tools/call impact_analysis {"symbol": "ChaosHarness.run", "depth": 2, "mode": "calls"}
```
### A.2 输出（真实，isError=false）
| 字段 | 值 |
|---|---|
| resolved_symbol | `multi-agent-orch::src/orch/chaos/__init__.py::ChaosHarness.run` |
| direct_callers | `[]`（无内部调用方——混沌 harness 入口，**可安全重构**，这是一条同样有行动力的确定性答复） |
| affected_modules (1) | `src/orch/chaos/__init__.py` |
| truncated | false |

**结论（A 通过）**：工具在「重构前」以**确定性调用方闭包**回答「改它会波及谁」——`_handle_terminate` 波及 3 直接 + 2 间接调用方、2 个调度模块且提示闭包未穷尽（truncated）；`ChaosHarness.run` 无调用方、可安全改。二者都是可直接行动的结构化答复，符合 P3「确定性工具不吃模糊输入」（入参已消解为唯一符号 ID，歧义则另走 candidates，见 `tests/test_mcp_server.py::test_impact_ambiguous_invoke`）。

---

## 动作 B — 设计溯源问 `ask_repo`

**§8.3 映射**：归档示例是「上下文压缩这套设计是怎么演化来的」。本仓取真实设计溯源问句「终止清单这套设计是怎么演化来的」（`终止清单` 是本图核心概念，被多条 `DESCRIBES` 提交描述，最能体现「演化史」）。

### 输入
```
tools/call ask_repo {"question": "终止清单这套设计是怎么演化来的"}
```
### 输出（真实，isError=false）
| 字段 | 值 |
|---|---|
| route_label | `entity_local` |
| mode | `topic`（BM25 主题召回 → 概念展开） |
| degraded | false |
| linked (8) | `终止清单`, `_handle_terminate`, `core`, `_late_after_id`, `存储层六表DDL` … |
| context_text | 长度 3897 字；**含 `DESCRIBES` 提交（演化史）**，样例 sha：`4ecabcb3` / `6a9a32f7` / `10c3fdc3` / `7d182a97` |
| 命中概念样例 | `终止清单`, `终止完善`, `async 终止兜底与 upsert_session 作废 sid`, `§5.4 终止保留既有 pending` |
| premise_flags | `[]`（无未验证前提） |

**结论（B 通过）**：`ask_repo` 对开放式设计溯源问句返回**结构化检索上下文**——命中 `终止清单` 概念、沿 `IMPLEMENTS`/`DESCRIBES` 汇集实现函数与**真实提交历史**（回答「怎么演化来的」的一手材料）。**答案由调用方模型依据 `context_text` 生成**（工具只供检索证据、不生成事实，见工具描述与 D-N8 边界）。`route_label`/`mode`/`linked`/`premise_flags` 等 schema v2 字段随上下文一并回显，供上游诚实回显。

---

## 动作 C — 关闭 MCP 后的行为差异

**§8.3 映射**：对比「关 MCP」与「开 MCP」在同一任务（改 `_handle_terminate` 的影响面）上的差异。

> **诚实前提**：被索引仓库 `multi-agent-orch` **不在本机**（本仓只持有其图谱快照 `output/graph.json`），故无法在同一份源码上做 grep 与工具的逐行并排；以下按**能力轴**对比，「MCP 开」侧为本机真实工具输出。

| | **MCP 关**（编码 Agent 只有 grep / 读文件） | **MCP 开**（`impact_analysis`） |
|---|---|---|
| 能回答 | `_handle_terminate` 在**哪些行**出现（定义 + 若干文本匹配） | 改它**波及谁**：调用方闭包 |
| 调用方闭包 | ✗ 无（grep 命中 ≠ 调用边；跨文件/间接调用无法靠词面聚合） | ✓ direct=`_dispatch_group_async`/`_dispatch_group`/`_finish_interrupted_terminate`，trans=`run_thread_async`/`run_thread` |
| 受影响模块 | ✗ 无 | ✓ `scheduler/async_core.py`, `scheduler/core.py` |
| 闭包是否穷尽 | ✗ 无从判断 | ✓ `truncated=true`（提示还有更上游） |
| 确定性 | 文本匹配，含注释/字符串误命中 | 沿真实 `CALLS` 边的确定性 BFS，不掺概率 |

**结论（C 通过）**：`grep` 回答「符号在哪」，`impact_analysis` 回答「改了谁受影响」——后者是编码 Agent 用 grep/读文件**无法**低成本获得的结构化上下文（archive §2「grep 只能回答符号在哪」的正是此缺口）。此差异即 README Motivation 一节的实证素材（README 由 E3 交付，本手册只记录实测）。

---

## 三动作实测小结

| 动作 | 工具 | 输入 | 关键真实输出 | 结论 |
|---|---|---|---|---|
| A 重构前查影响面 | `impact_analysis` | `_handle_terminate` / `ChaosHarness.run` | 3 直接+2 间接调用方 / 0 调用方 | 通过 |
| B 设计溯源 | `ask_repo` | 「终止清单这套设计是怎么演化来的」 | topic 档、命中概念 + 4 条 DESCRIBES 提交 | 通过 |
| C 关 MCP 对比 | grep vs `impact_analysis` | 同符号 `_handle_terminate` | grep 只给「在哪」，工具给调用闭包/受影响模块/截断信号 | 通过 |

全部经**真实子进程 stdio JSON-RPC**产出，可由 `python design_work/e2_acceptance_probe.py` 复现；等价的断言化真测见 `tests/test_mcp_server.py`（15 用例，pytest 与独立 runner 双绿）。

---

## 用户录屏操作手册（step-by-step，供用户亲自录屏）

> 目标：从「在 Claude Code 里接入 `.mcp.json`」到「逐一演示三个验收动作」，全程可录屏。以下命令 / 路径按本机（`C:/Users/nirvana/Desktop/代码库知识图谱`）给出，换机时替换为你的克隆路径。**无需任何在线 LLM 调用、无密钥**（工具全离线读图谱）。

### 前置条件
1. Python ≥ 3.12（本机 3.14 实测可用）。
2. 已克隆本仓库，且 `output/graph.json` 存在（仓库自带；或 `repograph index --repo <PATH> --name <NAME>` 生成）。
3. 已安装 Claude Code CLI。

### 步骤 1 — 放置 `.mcp.json`（项目级接入）
在**项目根目录**（Claude Code 打开的工作目录）新建 `.mcp.json`，内容照抄 `.mcp.json.example` 并把占位路径改成**绝对路径**：
```json
{
  "mcpServers": {
    "repograph": {
      "command": "python",
      "args": ["-m", "repograph.mcp_server"],
      "env": {
        "PYTHONPATH": "C:/Users/nirvana/Desktop/代码库知识图谱/src",
        "REPOGRAPH_GRAPH": "C:/Users/nirvana/Desktop/代码库知识图谱/output/graph.json"
      }
    }
  }
}
```
> 若已 `pip install -e .`（把 repograph 装进环境），可省去 `env` 整块——`REPOGRAPH_GRAPH` 缺省即解析到仓库 `output/graph.json`。`REPOGRAPH_GRAPH` 用于指向任意 graph.json（换仓库时改这里）。

### 步骤 2 —（可选）先脱离 Claude Code 自检服务器
录屏前建议先确认服务器能起（避免录制时卡壳）：
```bash
cd C:/Users/nirvana/Desktop/代码库知识图谱
python tests/test_mcp_server.py     # 期望：ALL 15 MCP TESTS PASSED
```
或手动喂一行 JSON-RPC（PowerShell/bash 均可，见文末「附录：裸协议自检」）。

### 步骤 3 — 启动 Claude Code 并接入
1. 在项目根目录启动 Claude Code。
2. 首次会提示是否信任 / 启用项目级 MCP 服务器 `repograph`——**选允许**。
3. 输入斜杠命令 `/mcp`，确认面板显示 `repograph` 已连接、列出 3 个工具 `ask_repo / impact_analysis / repo_overview`。**（录屏镜头 1：`/mcp` 面板）**

### 步骤 4 — 演示动作 A（重构前查影响面）
向 Claude Code 输入（自然语言，让它自己路由到工具）：
> 「我想重构 `_handle_terminate` 这个函数，动手前先用 impact_analysis 看看会波及哪些调用方。」

**观察**：Claude Code 发起 `impact_analysis(symbol="_handle_terminate", depth=2)` 工具调用，返回 3 个直接调用方（`_dispatch_group_async` / `_dispatch_group` / `_finish_interrupted_terminate`）、2 个间接、2 个受影响调度模块、`truncated=true`。**（录屏镜头 2：工具调用 chip + 结构化返回 + Claude 的影响面解读）**

### 步骤 5 — 演示动作 B（设计溯源）
输入：
> 「用 ask_repo 查一下：终止清单这套设计是怎么演化来的？」

**观察**：Claude Code 发起 `ask_repo(question=...)`，返回 `route_label=entity_local / mode=topic` 的检索上下文，含 `终止清单` 概念与多条 `DESCRIBES` 提交（演化史）；Claude 据 `context_text` 综合出带出处的回答。**（录屏镜头 3：ask_repo 调用 + 上下文 + Claude 溯源回答）**

### 步骤 6 — 演示动作 C（关 MCP 对比）
1. 关闭 MCP：`/mcp` 面板里**禁用 repograph**（或临时把 `.mcp.json` 改名），重启会话。
2. 重复步骤 4 的问题。**观察**：Claude Code 退回 `grep` / 读文件，只能告诉你符号**在哪**出现，给不出调用方闭包 / 受影响模块 / 是否截断。**（录屏镜头 4：无工具时的 grep 行为）**
3. 重新启用 repograph，对照两次回答的差异——这段差异即 README Motivation 的实证。

### 录屏要点
- 建议时长 2–4 分钟；镜头覆盖：`/mcp` 面板（工具已连）、动作 A/B 的工具调用 chip 与结构化返回、动作 C 的开/关对比。
- **无需担心密钥**：三工具全离线读 `output/graph.json`，不发起任何在线 LLM 调用，屏幕上不会出现任何 token/host。
- 若工具返回 `graph_unavailable`：说明 `REPOGRAPH_GRAPH` 路径不对——按错误信息里给出的绝对路径修正 `.mcp.json`。

---

## 附录：裸协议自检（不装 Claude Code 也能验证）

一行初始化 + 一次工具调用（bash）：
```bash
cd C:/Users/nirvana/Desktop/代码库知识图谱
printf '%s\n' \
'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"cli","version":"0"}}}' \
'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"impact_analysis","arguments":{"symbol":"_handle_terminate","depth":2}}}' \
| PYTHONPATH=src PYTHONUTF8=1 python -m repograph.mcp_server
```
期望首行返回 `serverInfo={name:repograph,version:0.3.0}`，次行 `structuredContent` 含真实 `direct_callers` 三项。启动诊断（图谱路径、是否存在）打在 stderr，不污染 stdout 的 JSON-RPC 通道。
