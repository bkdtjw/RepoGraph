# E3 干净 clone 5 分钟走查记录（Phase E · README 开源交付验收）

> **目标**：验证「陌生人 clone 后 5 分钟内跑起」——严格按重写后的 `README.md` §3「快速开始（5 分钟验收线）」逐条执行，全程**零第三方依赖、零密钥、零在线 LLM 调用**，卡住的每一步回改 README。
>
> **走查环境**：Windows 11 / Git Bash / Python 3.14.0。**干净目录**：`%TEMP%\repograph_e3_walk\RepoGraph`（ASCII 路径，同时验证 cwd 无关性——源仓路径含中文 `代码库知识图谱`）。源 = 本地仓库 `C:/Users/nirvana/Desktop/代码库知识图谱`（HEAD `61e8081`，E1 提交）。
>
> **结论**：**5 步全部一次通过，0 处 README 步骤回改**。活跃命令总耗时 < 10 秒（远低于 5 分钟预算）。另有 1 处 §3.5 措辞精修（读 `llm_client.py` 源码后的表述澄清，非走查卡点）。

---

## 逐步执行与计时

| # | README 锚点 | 命令 | 耗时 | 结果 |
|---|---|---|---|---|
| 0 | §3.1 克隆 | `git clone <local> RepoGraph` | **<1s** | ✅ 810 万字节 output/graph.json + repo_card.json 随克隆到位（仓库自带演示图谱，无需先建图） |
| 1 | §3.2 门禁 | `python eval/gate.py` | **1s** | ✅ 真实红绿（见下），纯 stdlib、不经网关 |
| 2 | §3.3 MCP 真测 | `python tests/test_mcp_server.py` | **1s** | ✅ `ALL 16 MCP TESTS PASSED`（16 用例，子进程真实 stdio JSON-RPC） |
| 3 | §3.4 步1 | `cp .mcp.json.example .mcp.json` | 即时 | ✅ `.mcp.json` 就位 |
| 4 | §3.4 / 附录 | 裸协议 MCP 自检（`python -m repograph.mcp_server`，`PYTHONPATH=src`） | **<1s** | ✅ serverInfo `repograph 0.3.0` + `impact_analysis(_handle_terminate)` 返回 3 真实直接调用方 |
| 5 | §3.2 复现 | `python design_work/d3_f9.py` | **<1s** | ✅ F9 归因，`unreachable_share_of_subset=0.2`（= 2/10，与 README §5.3 一致） |

**关键**：步骤 1/2/4/5 均**无需 `pip install`**——`gate.py` 自行把 `src` 插入 `sys.path`；`test_mcp_server.py` 为子进程注入 `PYTHONPATH=src`；MCP 服务器与检索层纯 stdlib。第三方依赖（GitPython/networkx/matplotlib/pydantic）**仅重建图谱**（§3.5）时需要，本走查全程未触发。

---

## 步骤 1 门禁真实输出（`eval/gate.py` @ 干净 clone）

```
[子集通过率]
  L0     : pass 10/10 = 1.0   fail=[]
  FZ_dev : hit@1=0.5  hit@3=0.7 (7/10)
  FZ_test: hit@1=0.6  hit@3=0.8 (8/10)
  AMB    : 行为一致率=1.0 过问率=0.0 漏问率=0.0
  PP     : 纠正率=0.0 泄漏率=0.0 premise_flags能力=True
[锁定失败 B-1/B-2/B-3]
  B-1: GREEN | B-2: RED (FZ-dev hit@3 < 0.8) | B-3: GREEN
[硬指标]
  裸拒率 = 0.0 → PASS   路由准确率 = 0.8542
```

与 `eval/gate_report.json`（Phase D 冻结 @ `b7206bb`）逐项一致：L0 1.0 / FZ_dev 0.7 / FZ_test 0.8 / AMB 1.0 / 裸拒 0 / 路由 0.8542。**证明冻结数字在干净 clone 上可复现**——README §3.2、§4.4、§5.2 引用的红绿即此。

> 注：Git Bash 终端将 gate 的 UTF-8 输出按 GBK 解码会显示乱码（本机终端编码问题，非 gate 缺陷；gate 入口已 `reconfigure(encoding="utf-8")`，Windows 原生终端显示正常）。上表为解码还原后的真实内容。

## 步骤 2 MCP 真测输出（`tests/test_mcp_server.py` @ 干净 clone）

```
test_initialize OK ... test_repo_overview OK ... test_graph_override_malformed OK
ALL 16 MCP TESTS PASSED
```

16 用例全绿（README §3.3 与 §8 导览表所述「16 用例」以此为准）。**口径差异（已知，留 E1 台账后续订正）**：DECISIONS D-23/D-N8 回填与 `e2_acceptance.md` 写「15 用例」，为 opencode E1-R1 审查补入第 16 例 `test_graph_override_malformed` 时的旧计数；HEAD `61e8081` 的测试文件实为 16 例，README 采用运行时真值。E3 scope 限 README + MCP 新文件，**不改 DECISIONS**，差异如实标注而非隐藏。**〔E-Verify 2026-07-24 已闭环〕** 独立核验阶段已将 DECISIONS D-23/D-N8 回填与 `e2_acceptance.md` 的旧计数「15」统一订正为 **16**（运行时真值），全仓计数一致；详见 `design_work/verify-e.md`。

## 步骤 4 裸协议 MCP 自检输出

```
initialize -> serverInfo = repograph 0.3.0 | proto 2025-06-18
impact_analysis(_handle_terminate) -> isError=False
  | direct_callers = ['_dispatch_group','_dispatch_group_async','_finish_interrupted_terminate']
  | truncated = True
```

不装 Claude Code、不 `pip install`，一行 `.mcp.json` 命令即通——与 `design_work/e2_acceptance.md` 动作 A 实测值一致。

---

## README 回改记录

- **步骤卡点导致的回改：0 处**。§3.1–§3.4 全部一次跑通。
- **精度澄清（非卡点）：1 处** —— §3.5 重建图谱说明：读 `src/repograph/extract/llm_client.py` 源码后，把「配置读 claude-ui/config.json」精修为明确「Anthropic 兼容 `/v1/messages` 网关 + 换成你自己的兼容网关即可 + 令牌只入内存、`sk-****` 占位」，供开源读者理解语义层通道（claude-ui 为同级项目、非本仓一部分）。不影响 §3.1–§3.4 的 5 分钟验收路径。

## 清理

走查用临时目录 `%TEMP%\repograph_e3_walk`、`%TEMP%\repograph_e3_clonetime` 已删除，不留残余。源仓库工作树 E3 改动仅两处：`README.md`（重写）+ 新增本记录 `design_work/e3_walkthrough.md`。`src/`、`eval/`、`output/`、`DECISIONS.md` 均未改（守冻结与 scope 边界）。
