# Phase E 独立核验记录（verify-e）

> **性质**：Phase E（MCP 复归与开源交付）的独立核验。三条主线：(1) README 数字逐一回溯核对 + 第二次干净 clone 走查；(2) opencode 审查 MCP 代码；(3) 终态回归 + DECISIONS 一致性。
> **核验人视角**：独立于 E1/E3 执行者。可改文件：README.md / mcp_server.py / test_mcp_server.py / .mcp.json.example / DECISIONS.md（+ 纪律 #4 授权的 MCP 相关 Phase-E 文件 e2/e3_walkthrough）。冻结不动：`src/repograph/retrieve/*`、`eval/dataset*.jsonl`、gate 阈值、`graph.json`、`d2_results.json`。禁 push。
> **起点**：`747f4a9`（E3）。**终点**：本轮修订 commit（见文末）。

---

## 1. README 数字逐一回溯核对 —— **0 错引**

对 README 每个数字回溯到 `eval/report.md` / `eval/d2_results.json` / `eval/gate_report.json` / `output/graph.json` / `design_work/d3_f9.json` 的真实值，逐项核对：

| README 位置 | 数字 | 源 | 核验 |
|---|---|---|---|
| 核心表 / §5.1 L1 | correct A 0.25(5/20)→B 0.65(13/20) Δ+0.40；c+p 0.40→0.75 | d2 `main_by_layer`/`group_delta` | ✅ |
| 核心表 / §5.1 L2 | correct A 0.00(0/30)→B 0.533(16/30)；c+p 0.167→0.867 Δ+0.70 | d2 同上 | ✅ |
| 核心表 / §5.1 L3 | correct A 0.90(9/10)→B 0.50(5/10) Δ−0.40；c+p 1.00→0.90 | d2 同上 | ✅ |
| §2 程序断言 L1/L2/L3 | hit@min1 A 0.60/0.267/1.00，B 0.80/0.90/0.90；mean A 0.517/0.083/0.883，B 0.760/0.733/0.900 | d2 `segment1.main_program_assertions` | ✅ |
| §5.2 48 题 | L0 0.0→1.0；FZ_dev 0.7/0.7；FZ_test 0.8/0.8；AMB 0.0→1.0；leak 0；bare 0 | d2 `set48` + `gate_report.json` | ✅（两 harness 逐项一致）|
| PP 端到端 | 0.875(7/8)、c+p 1.0(8/8)、顺预设 0、error 0 | d2 `pp_correction_groupB` / report §4 | ✅ |
| §5.4 在线调用 | 256 次（128 生成+128 裁判）0 失败 | d2 `online_call_stats` | ✅ |
| 核心表 图谱规模 | 510 节点/1698 边；Module22·Class15·Function259·Commit75·Concept139；CALLS352·MODIFIES533·IMPLEMENTS390·DESCRIBES104 | `output/graph.json` 实测 | ✅ |
| §7 Concept.ctype | 81/40/18（design_decision/domain_concept/constraint） | graph.json 实测 | ✅（81+40+18=139）|
| §4.4 路由准确率 | 0.8542；7 处 mismatch（PP-02..08，route_label=entity_local） | `gate_report.json.hard_metrics` | ✅ |
| §4.4 锁定失败 | B-1 GREEN / B-2 RED / B-3 GREEN | `gate_report.json.locked_failures` | ✅ |
| §5.3 F9 | FZ-test 0.8、失败 t03/t04、词面不可达 2、排序失利 0、净代价 2/10=0.20；FZ-dev 0.7、d06/d09/d10、2 不可达+1 失利 | `d3_f9.json` | ✅ |
| §4.1 差距矩阵 | 落地 0 / 部分 11 / 未实现 20 / 不适用 1；六项基础设施 | `gap-matrix.md §9` 自陈汇总 + §0（6 行） | ✅（README 忠实引用源自陈；逐行原始计数因多行机制并合为「条」而不同，非 README 错引）|
| §4.3 V0/V1/V3 | V0 32 单元 0 可行；V1 n-gram 与 jieba hit@3 均 0.1；V3 精确 0/15、Jaccard τ=0.15 14/15=0.933、成本 0.87% | `v0_calibration.json`/`v1_tokenize_eval.json`/`v3_blocking_eval.json` | ✅ |

**结论：无一处数字错引/夸大。** 主叙事 Δ 与所有引用读数逐一坐实。

### 两处 precision 修订（非数字错引，但属可被 `git diff`/计数证伪的门面精度问题，已修）

- **VE-01｜README §核心数字一览「评测树 `src/repograph`…自 D1 逐字未变」**：`git diff 7df38d4 HEAD -- src output` **非空**——Phase E 在 `src/repograph` 新增了 `mcp_server.py`。评测**路径**代码（`retrieve/*`+`models.py`）与 `graph.json` 确实逐字未变（gate 在 HEAD 复跑数字与冻结报告完全一致，见 §2），但字面「`src/repograph` 逐字未变」被新增适配器证伪。**已改**为「评测路径代码与图谱逐字未变（`git diff … -- src output` 仅新增薄适配器 `mcp_server.py`，不在评测路径、不影响任何数字）」。
- **VE-02｜「MCP 用例数 15 vs 16」**：`test_mcp_server.py` 运行时真值 **16**（`ALL 16 MCP TESTS PASSED`，含 opencode E1-R1 补入的 `test_graph_override_malformed`）。README 正确（16），但 `DECISIONS.md`（D-23 回填、D-N8）与 `e2_acceptance.md` 仍写旧计数 15（E3 走查已知、留后续订正）。**已统一订正为 16**（DECISIONS ×2 + e2_acceptance ×2；e3_walkthrough 补闭环标注）。

### 观察（未改，defensible）
- README「DECISIONS 33 具名条目」：`## D-` 标题实为 37（D-01..24 + D-R2 + D-N1..8 + D-P1..4）。README §4.2 已显式定义 33 = 24 机制 + D-R2 + D-N1..8，D-P1..4 另列「验证配套」，全文自洽用 33。属特定口径，非错，保留。

---

## 2. 第二次干净 clone 走查（独立于 E3）

**环境**：全新克隆到 **ASCII 临时路径** `%TEMP%\rg_verifyE_clone\RepoGraph`（源仓路径含中文，同时验 cwd 无关性），HEAD `747f4a9`，**全程零 `pip install`**。

| 步 | 命令 | 结果 |
|---|---|---|
| §3.2 门禁 | `python eval/gate.py` | ✅ L0 10/10、FZ_dev 0.7、FZ_test 0.8、AMB 1.0、裸拒 0、路由 0.8542、B-1/B-2/B-3=绿/红/绿——**与冻结 `gate_report.json` 逐项一致**。HEAD=`747f4a9`（≠报告内 `b7206bb`）仍复现同数字，**坐实 §5.2「@HEAD 复跑」跨冻结点可复现** |
| §3.3 MCP 真测 | `python tests/test_mcp_server.py` | ✅ `ALL 16 MCP TESTS PASSED` |
| §3.3 pytest | `python -m pytest tests/test_mcp_server.py` | ✅ `16 passed` |
| §3.2 F9 | `python design_work/d3_f9.py` | ✅ `unreachable_share_of_fztest=0.2`（=2/10）、FZ-dev 0.2 |
| §3.4 裸协议 | 独立 stdio JSON-RPC 探针（initialize+tools/list+3 工具调用） | ✅ serverInfo `repograph 0.3.0`；3 工具；`impact_analysis(_handle_terminate)`→3 直接调用方+truncated；`repo_overview`→259/139/22/15/75；`ask_repo(元问题)`→meta/overview 非空；`impact(invoke)`→ambiguous 6 候选 |

**结论：5 步全部一次通过、0 处 README 步骤回改。** 印证 E3 走查。
（副带发现：裸探针若客户端未强制 UTF-8，Windows 会以 GBK 解码服务器含中文的 JSON 而破坏解析——**客户端侧编码坑**，服务器输出合法；`test_mcp_server.py` 强制 `PYTHONUTF8=1`+显式 `.decode("utf-8")` 处理正确。）

---

## 3. opencode 审查 MCP 代码

送审 `mcp_server.py` + `test_mcp_server.py`（带行号内嵌，首行硬禁工具/联网/读文件），维度=协议正确性/schema 与描述准确性/并发与超时/真实性。模型 `qwen/qwen3.8-max-preview`；提示词 `design_work/review_e/prompt_mcp_code.txt`、输出 `design_work/review_e/out_mcp_code.txt`。
（用法坑：`$(cat)` 触发「Argument list too long」、stdin 管道挂起；最终以 `opencode run "<短指令>" -f <提示词文件>` 成功，一次通过、无 429/重试。）

**意见 9 条逐条核实与处置**：

| # | sev | 位置 | 意见 | 核实 | 处置 |
|---|---|---|---|---|---|
| 1 | major | mcp_server:352 | 成功结果 `_tool_result_content`（内 `json.dumps`）在 try 外，不可序列化 payload 会冒泡 -32603 | **成立**（结构性契约缺口；实际 retrieve 层返回 JSON-safe，属潜在。但项目自立「绝不冒泡 -32603」绝对契约且已为 load 路径写回归测试） | **已修**：成功返回移入 try |
| 2 | major | mcp_server:96 | `from .models import GraphStore` 在 try 外，ImportError 冒泡 -32603；且 `_loaded=True` 先置致异常后缓存态 `(None,None)`→message=null | **成立**（同上，潜在但契约缺口） | **已修**：import+exists+load 整体纳入 try，`_error` 必赋值；两条既有 graph_unavailable 测试仍绿 |
| 3 | major(同2根) | mcp_server:95 | `_loaded` 先置的异常态不一致 | 同 #2 | 随 #2 修复 |
| 4 | nit | mcp_server:55 | `_INVALID_PARAMS=-32602` 死代码 | 成立（确未引用） | 保留作标准码完整参照，**加注**说明工具入参错走 isError（非删） |
| 5 | nit | mcp_server:140/175/189 | `additionalProperties:false` 声明但服务端未强制 | 成立 | **不改**：schema 对 MCP 客户端为 advisory，薄适配器宽容忽略多余键更稳健（记录 rationale） |
| 6 | nit | mcp_server:180 | repo_overview description 未提始终返回的 `source` | 成立 | **已修**：description 补 source/degraded 说明 |
| 7 | nit | test:129 | `close()` 无 kill 兜底 | 成立但 Windows（目标平台）`terminate`=`TerminateProcess` 立即生效，风险极低 | **不改**（记录 rationale） |
| 8 | minor | test | 缺 arguments 非 dict/缺失 边界用例 | 成立 | **已补**（折进 `test_unknown_tool`，计数中性保持 16）|
| 9 | minor | test | 缺非 str symbol / id:null 边界用例 | 成立 | **已补**（symbol=123 折进 `test_unknown_tool`；id:null 请求折进 `test_ping`）|

**统计**：9 条 → 修 5（2 major + 1 nit-注 + 1 nit-desc + 2 minor 覆盖并作 3 断言）、defensible 不改 2（nit 5/7，记 rationale）、保留加注 1（nit 4）。无 blocker。测试计数刻意保持 **16**（避免波动 README/DECISIONS/e3_walkthrough 的既有计数引用，且新增断言折入既有用例）。修后 `test_mcp_server.py` runner+pytest 双绿。

> 契约收口：两条 major 使「工具层任何失败（图谱缺失/载入/ImportError/入参非法/内部异常/**结果序列化**）绝不冒泡协议层 -32603、一律 isError」结构性闭合，承 E1-R1 同一契约。已在 DECISIONS D-N8 补审注记。

---

## 4. 终态回归 —— 无劣化

- **MCP 16 用例**：runner `ALL 16 MCP TESTS PASSED` + pytest `16 passed`（源仓，含本轮修订）。✅
- **冻结检索层测试**（各自 runner）：`test_topic/router/context/impact_stats` 均 `ALL TESTS PASSED`。✅
- **全量 pytest**：源仓 `75 passed / 37 errors`，**与 pristine clone（无本轮修订）逐字相同** → 37 errors 为既有 `store` fixture 约定伪像（这些用例设计走 `__main__` runner，非 pytest fixture），**非本轮引入、非真失败**。✅ 零劣化
- **gate 复跑**：干净 clone @HEAD 与冻结 `gate_report.json` 逐项一致（§2）。✅
- **隔离性证明**：`grep mcp_server` 于 `eval/` 与 `retrieve/`/`models.py` **无命中** → `mcp_server.py` 对评测路径 import 隔离，修订**不可能**影响任何图谱/门禁数字。✅
- **冻结产物**：`git diff -- eval/gate_report.json eval/d2_results.json output/graph.json retrieve/* dataset*.jsonl` **为空**。✅ 未触冻结

## 5. DECISIONS D-N7 与 D-14/D-23 一致性 —— 通过

- D-14/D-23/D-N7/D-N8 **均「状态: 生效」**。
- 交叉引用互洽：D-23 Phase E 回填正确指向 D-N7（三工具、`query_graph`→v0.4）+ D-N8（stdlib 非 SDK）+ D-14（`repo_overview` 能力由 meta 路由注入**复归**为按需工具、push 模型「无法主动拉概览」损失消除）。README §4.2 表行 D-N7 / D-14+D-23 与台账逐条对应。
- D-23 正文仍写原设「四件/FastMCP」为 2026-07-22 原裁定，回填注更正为「三件/stdlib」——标准「原裁定+回填更正」体例，非冲突。
- 唯一缺陷=旧计数 15，本轮已订正为 16（§1 VE-02）。

---

## 6. 本轮修订文件
`src/repograph/mcp_server.py`（2 major 契约兜底 + 2 nit）、`tests/test_mcp_server.py`（3 边界断言，计数中性 16）、`README.md`（VE-01 冻结口径精修）、`DECISIONS.md`（D-N8 补审注 + 15→16 ×2）、`design_work/e2_acceptance.md`（15→16 ×2）、`design_work/e3_walkthrough.md`（闭环标注）；新增 `design_work/review_e/`（提示词+输出+生成脚本）、本文件。**未 push。**
