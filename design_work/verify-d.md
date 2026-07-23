# Phase D 独立核验（Verify）收尾报告

**评测树**：`src/repograph` + `output/graph.json`（510 节点 / 1698 边），自 D1 提交 `7df38d4` 起逐字未变（`git diff 7df38d4 b7206bb -- src output` 为空，`git diff c971078 b7206bb -- src output` 亦空——报告/产物中 `7df38d4`/`c971078`/`b7206bb` 三个标签指向同一冻结内容，非矛盾）。
**HEAD**：`b7206bb`（tag `phase-d-20260723`）。
**性质**：前任独立核验 agent 两次被 API 网络瞬断打掉，失败前产物已落盘（`design_work/review_d/`）。本报告基于已有产物完成收尾，不重做已完成工作。
**红线合规**：全程离线/读盘核验，未发起任何在线调用；`eval/.judge_config.json`（真实密钥，已 gitignore）与 `claude-ui/config.json` 全程未读、未打印、未入提交。
**可复现脚本**：`design_work/review_d/verify_totals.py`（逐条加总 + 组隔离 + DR-01 零影响证明，纯读盘不写产物）。

---

## 1. 抽查结论（sample12 + 补抽 + deepdive）

抽查覆盖 **15 道题、30 个 A/B 判定格**：`_sample12.txt` 底稿 6 题（L1-01/L1-07/L2-01/L2-24/L3-01/L3-05）+ 本轮补抽 6 题（L2-05/L2-11/L1-14/L1-16/L3-03/L3-07）+ `_deepdive6.txt` 独有 3 题（L1-13/L2-03/L2-18）。全部对 `d2_runs/{gen,judge}_main_{A,B}.jsonl` 原始记录人工比对，不需在线调用。

### 1.1 生成输入是否真为对应组上下文 —— 是（机器证）
- **组 A 生成输入 mode 全为 `bm25_only`**（60/60），无一落入图谱模式集 `{symbol,topic,overview,...}`；**组 B 全为图谱模式**（实测 `{symbol,topic,overview}`），无 `bm25_only` 泄漏。
- **`gen.mode == ctx.mode` 逐题一致**（组 A/B 各 60/60）；且 **`gen.context_chars == len(ctx.context_text)` 逐题字节级一致**（组 A/B mismatch=0）——证明送入生成的上下文即对应组快照上下文，无串组。
- 组 A 基线 `baseline_bm25.build_bm25_context` 仅调 `topic_recall`（纯 BM25）+ 读节点自身 `path/file` 字段，**无任何边遍历/闭包/符号链接**；`route_label` 恒 `bm25_only`（自描述，不伪装 meta/global）。**组 A 无偷用图结构泄漏——确认。**

### 1.2 裁判 verdict 与答案是否相符 —— 相符，裁判误判 = 0
逐格比对答案文本、gold、裁判 verdict+reason，未发现任何 verdict 与答案+gold 明显矛盾者。代表性核验：
- **L2-05**（gold 7 项闭包）：组 B 完整命中全部 7 个成员（`_apply_gate/cmd_run/_ep_gate/_ep_thread_run/cmd_approve/cmd_reject/_make_handler.Handler._route_api`），判 `correct` **属实，非裁判放水**；组 A 未答判 `wrong` 属实。
- **L2-01**（gold 6）：组 B 命中 5/6（缺 `run_workspace._run_one`）判 `correct`，符合 L2 rubric「覆盖绝大多数」（83%）；组 A 未答判 `wrong` 属实。
- **L1-14 / L1-16**（commit→改动函数）：两组均因 commit 哈希未被检索到而未答，判 `wrong` 属实（这是组 B 的诚实失败，L1-14/16 在 B 的 `wrong_ids` 内）。
- **L3-03 / L3-07**（设计溯源）：两组答案对概念+溯源提交的命中与裁判 verdict 一致可辩护。
- **L3-01**：组 B 英文答「上下文不足」、未触及权限三件套/提交哈希，判 `wrong` 属实（此即 §6.6「L3 诚实负结果」的过泛化缺陷根因，非裁判误判）。

**误判计数 = 0**。另记：`partial/correct` 若干边界判定偏宽但均可辩护（如 L2-01 B 5/6 判 correct、L3-03 B 因“未点破为什么”判 partial、L3-05 A 命中概念未引提交判 correct——L3 rubric 为 `design_rubric_one_of`，命中概念或提交其一即可），不构成误判。

---

## 2. 加总核对结果（`verify_totals.py` 真算，全部 [OK]）

从 `d2_runs` 原始记录逐条重算，与 `d2_results.json` 汇总逐格比对：

| 核对项 | 结果 |
|---|---|
| 记录条数 | gen 60/60/8、judge 60/60/8、ctx 60/60，唯一 id 无重复 |
| segment2 三层 × 两组 c/p/w + correct_rate + c+p_rate + wrong_ids | **逐格 byte-identical**（A/B × L1/L2/L3 共 6 格全中）|
| `c+p+w == n_judged` 自洽 | 全 6 格通过 |
| PP（组 B）correct/partial/wrong=7/1/0、n=8、rate=0.875 | 一致 |
| online_call_stats | gen total=128=Σbuckets、error=0；judge total=128、error=0 |
| report.md 主表(§1)/程序断言(§2)/PP(§4)/在线统计(§6.7) | 与 `d2_results.json` 逐值一致（§2 三位小数四舍五入相符）|

**结论：`d2_results.json` 汇总数与 `d2_runs` 原始记录逐条加总一致；report.md 主表数字与 `d2_results.json` 一致。** 本轮 128 生成 + 128 裁判 **0 error**，opencode 关切的「error 不入分母稀释」不适用（分母无稀释）。

---

## 3. opencode 两轮意见台账（12 条：采纳 4 / 驳回 8）

逐条对真实代码（`baseline_bm25.py` / `run_d2.py` / `d1_goldcheck.py`）核实。

### 采纳（4）
| 编号 | severity | 位置 | 意见 | 处置 |
|---|---|---|---|---|
| **DR-03** | nit | `run_d2.py` `run_offline` | `json.load(open(...))` 未 `with`，句柄泄漏 | 前任已改 `with` 管理；核实正确 |
| **DR-01** | major(两轮) | `run_d2.py:context_has_entity` | Function/Class 叶名纯子串匹配，短叶名（如 `invoke`）被更长标识符误命中虚高召回 | 前任已加 `_has_identifier_token` 词边界（仅 Fn/Class）；**核实零影响**（见 §4）|
| **DR-02** | blocker/major | `run_d2.py:744 findings` | 硬编码 `"(7/8)"`，重跑时文本与数值可自相矛盾（伪造绿） | 前任已改 `"(%d/%d)"%(pp_c,n)` 动态派生；核实 pp 真为 7/8，输出文本不变 |
| **DR-04** | minor(R2) | `run_d2.py:aggregate meta.graph` | 硬编码 `{nodes:510,edges:1698}`，图谱更新后失真 | **本轮改动态读** `len(g["nodes"])/len(g["edges"])`；真值恰=510/1698，对冻结产物零影响 |

### 驳回（8，附理由）
| severity | 位置 | 意见 | 驳回理由 |
|---|---|---|---|
| major(R2) | `run_d2.py` Concept recall token | 概念名无最小长度守卫，≤2 字假阳性 | 冻结集最短概念名=**3 汉字**（`适配层`），汉字无 ASCII 标识符边界问题；segment1 重算已证零误命中；DR-01 注释已锁定 Concept 保持原子串「足够长/含分隔符、已证零误命中」|
| minor(两轮) | commit 短 sha `[:8]` vs `[:12]` 不一致 | 统一为 12 | **驳回且反向有害**：上下文以 8 位显示 commit sha（`[Commit] 5c6bb736`，12 位串不出现）；实测 64 个 commit 锚点中 **27 格「仅 [:8] 命中、[:12] 全漏」**，统一为 12 会把全部 commit 召回变假阴性、反改坏冻结 L3 数字；8 位 hex 于 ~150 commit 无碰撞风险。`[:8]`（匹配显示）与 distinctive `[:12]`（题面泄漏检查，另一表面）各司其职 |
| nit(R1) | `run_d2.py:429 load_done` 循环内重读 O(n²) | 提到循环外 | 纯性能、非正确性；评测已完成，改动续跑逻辑零收益且引入回归风险 |
| minor(两轮) | `d1_goldcheck.py:53 module_of` 仅上溯 2 层 | 改 while 递归 | 真图 **Class→Class CONTAINS 边=0**（无嵌套类），深度 2 对本图充分；`d1_goldcheck` 复跑全 60 题 gold 重算 **0 失败**，module_of 口径已证正确 |
| minor(两轮) | `d1_goldcheck` blast_radius 交叉校验 off-by-1 隐患 | 加容差/锁口径 | `rev_closure` 返回 depth≥1（不含自身），`br==len(gold)` 现口径成立：复跑 **depth3 闭包与 blast_radius 交叉校验 12 题全过**，语义正确无 off-by-1 |
| minor(R2) | `d1_goldcheck.py:206` 泄漏检查 `s in question` 子串 | 加词边界 | 子串匹配是**保守方向**（宁误报不漏放真泄漏），且复跑 0 失败=0 误报负担；放宽反而削弱泄漏防线，方向错误 |
| minor(R1) | `findings` 键名 `"L1_L2_图谱决胜"` 预设结论 | 中性化 | 冻结数据未反转（L2 组 B correct 0.533 vs A 0.0），键名与数据相符；中性化仅美化且会改产物 findings 键，非成立缺陷 |
| minor(R1) | error 不入分母稀释风险 | 加守卫断言 | 本轮 gen/judge 各 0 error，分母无稀释，不适用；`error_ids` 已透明列出可审计 |

---

## 4. DR-01 零影响证明（冻结产物不重算的依据）

DR-01 把 Function/Class 叶名从纯子串改为标识符词边界匹配。**证明其对已冻结 `d2_results.json` 零影响**：
- 在既有 `ctx_main_{A,B}.jsonl` 快照上，对全部 60 题 × 2 组 × 各 gold 实体，同时用**旧口径（纯子串）**与**新口径（Fn/Class 词边界）**计算命中集：**diff cells = 0**（60×2 格逐格 byte-identical）。
- 用新口径重算 segment1 `layer_summary`，与 `d2_results.json` 存档（旧口径生成）**逐格一致**：A/L1(0.6,0.5167)、A/L2(0.2667,0.0831)、A/L3(1.0,0.8833)、B/L1(0.8,0.76)、B/L2(0.9,0.7325)、B/L3(0.9,0.9)。
- 根因：冻结集 **113 个 Fn/Class gold 锚 token 叶名全部 ≥6 字符**（最短 `invoke`=6），词边界与原子串在这些长叶名上等价。故 DR-01 是纯防御性硬化，不触发重算。

---

## 5. gate + 回归终态（步骤 4，与 Phase C 终态一致，无劣化）

**`python eval/gate.py`**（离线确定性，无 urllib/requests/socket）@HEAD `b7206bb` / tag `phase-d-20260723`：

| 项 | 值 | 期望 |
|---|---|---|
| L0 pass | 10/10 = 1.0 | ✓ |
| FZ_dev hit@3 | 0.7 (7/10) | ✓ B-2 红 |
| FZ_test hit@3 | 0.8 (8/10) | ✓ |
| AMB 行为一致率 / over_ask / under_ask | 1.0 / 0.0 / 0.0 | ✓ AMB 1.0/0/0 |
| 裸拒率 | 0 | ✓ |
| **B-1 / B-2 / B-3** | GREEN / **RED** / GREEN | ✓ 与 Phase C 终态一致 |
| 路由准确率 | 0.8542 | 未劣化 |

**`tests/` 全量回归**：14 个测试文件均为脚本式（`test_fn(store)` 靠文件底 `__main__` 传 store，非 pytest fixture），按设计以 `python tests/test_*.py` 逐个运行 —— **14/14 全过**。（直接 `pytest tests/` 报 37 error 是调用方式产物：脚本式用例的 `store` 参数被 pytest 当缺失 fixture；59 passed 为无参用例。此为既有测试结构，前任仅改 `eval/`、未碰 `tests/`/`src/`，非回归。）

---

## 6. 修订清单与提交

| 编号 | 文件 | 改动 | 落地方 |
|---|---|---|---|
| DR-01 | `eval/run_d2.py` | `context_has_entity` Fn/Class 叶名词边界匹配 `_has_identifier_token` | 前任已改，本轮核实 |
| DR-02 | `eval/run_d2.py` | `findings.PP_端到端` 去硬编码 `(7/8)`→`(%d/%d)` 派生 | 前任已改，本轮核实 |
| DR-03 | `eval/run_d2.py` | `run_offline` 图谱读盘 `with` 管理句柄 | 前任已改，本轮核实 |
| DR-04 | `eval/run_d2.py` | `aggregate` meta.graph 去硬编码 510/1698→动态读 | **本轮新修** |
| —（同步） | `eval/report.md` | §3 来源行 gate_report 复跑 HEAD `c971078`→`b7206bb`（步骤4复跑同步） | 本轮 |
| —（复跑产物） | `eval/gate_report.json` | Verify 于冻结点复跑，meta head/tag 刷新至 `b7206bb`/`phase-d-20260723`，红绿值不变 | 本轮 |
| —（核验产物） | `design_work/review_d/verify_totals.py`、`design_work/verify-d.md` | 新增可复现核对脚本与本报告 | 本轮 |

冻结不动：dataset gold（`dataset_main.jsonl` 复跑 0 失败）、gate 阈值、`d2_results.json` 数字、`src/`、`output/graph.json`。
