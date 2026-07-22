# Phase A+B 产物独立审查记录（opencode headless + gate 复跑）

**日期**：2026-07-22　**审查人**：Phase A+B 独立审查 agent
**方法**：opencode headless（`opencode run --pure`，模型 `qwen/glm-5.2` 与 `qwen/qwen3.8-max-preview`；首行硬约束「禁止使用任何工具、禁止读文件，只依据内嵌内容作答」）。因 Windows argv 上限，采用 inline 内嵌分段送审（`-f` 附件路径实测挂死，弃用）。
**送审包**（`design_work/`）：`_rev_D1.txt`（DECISIONS D-01..24）、`_rev_D2.txt`（D-R2/N/P + 对账表 + 自检 + 实验数字）、`_rev_gate.txt`（gate.py 全文 + eval-design §7 契约摘要）、`_rev_spec.txt`（spec 冻结块 + §4.6 指向注 + §B）。
**核对基座（真跑，非臆造）**：`output/graph.json` 510 节点（Function 259/Concept 139/Commit 75/Module 22/Class 15）/1698 边；`design_work/_probe_linked.py` 实探 `build_repo_context` 的 `linked` 结构；`eval/gate.py` 复跑 diff。

opencode 共提 **17 条**（DECISIONS 8 + gate.py 8 + spec 1）；**采纳 7、驳回 10**。逐条如下。

---

## 一、DECISIONS.md（D1+D2，共 8 条 → 采纳 4 / 驳回 4）

| # | 严重度 | opencode 意见 | 文件事实核对 | 处置 |
|---|---|---|---|---|
| D1-1 | 高 | D-11 状态「重议中」不在格式节声明的 {生效/待验证/已撤销} 内 | 属实：格式节仅列 3 值，D-11 头行用「重议中」，且 D-P1/自检均沿用该值 | **采纳**：格式节状态取值补入「重议中（机制未证伪但触发重议警报、待阶段复核）」，使 D-11 头行合规 |
| D1-5 | 低 | 格式节写「六字段」却只列 5 个（裁定/动因/显式损失/引用/复审触发） | 属实：确为 5 字段 | **采纳**：改「六字段」→「五字段」 |
| D2-1 | 中 | D-N3/D-22「259 节点」与实测 510 节点矛盾 | 属实但需精确：259 是 **Function 数**，总节点 **510**；D-12「本图 Function 259」正确，D-22/D-N3 把「函数」误写成「节点」 | **采纳**：D-22、D-N3 的「259 节点规模」→「本图规模（510 节点 / 259 函数）」；结论（图库无收益）不变，仅纠事实 |
| D2-2 | 低 | D-P4 头行第二字段填「待验证」而非日期，破坏 `ID\|日期\|状态` 三段式 | 属实：唯一一条日期位错填 | **采纳**：D-P4 头行日期位改回 `2026-07-22` |
| D1-2 | 低 | D-20 单条覆盖 §3 两表行，非 1:1 | 文末「映射说明」已显式声明 D-20 覆盖 #14/#20 为「同一集成缝隙输入/输出侧，非遗漏」 | **驳回**：已在文档内说明 |
| D1-4 | 低 | D-14/D-23 双引同一 §3 行21 | 「映射说明」已声明「行21 对应 2 编号（能力保留侧 D-14 + 工具形态 D-23），同一行两视角」 | **驳回**：已说明 |
| D1-3 | 低 | 前言「D-21..24 承接 4 砍除」滞后于 D-23 改判 | 前言为骨架映射；自检小结已注「砍除 4 = D-21..24（D-23 改判推迟）」，D-23 本体亦详述 | **驳回**：骨架陈述 + 自检已澄清，非缺陷 |
| D1-6 | 低 | D-13 引用「D-013」用 3 位编号 | 该处是引用**计划书附录 B 范例**，其 ID 原文即 `D-013`；准确转引 | **驳回**：忠实引用上游范例 ID |

D2（第二模型）另**确认无误**：具名条目 24+5=29≥26 算法自洽；对账表 24 行逐行映射无遗漏无重复错配；D-P1/P2/P3 状态=生效且所引 hit@3=0.1、0.5vs0.4、0/15、14/15=0.933/0.87%、404/402 与实验数字逐项吻合；D-P4 仍「待验证」；D-11「重议中」在 D-P1/自检/对账三处表述一致；无编号错位/引用悬空。

---

## 二、eval/gate.py（8 条 → 采纳 3 / 驳回 5；无「假绿」漏网）

| # | 严重度 | opencode 意见 | 文件事实核对 | 处置 |
|---|---|---|---|---|
| G4 | 中 | B-1 红判据仅验路由（`mode_class!=overview`）、不含 §7.1 的 hits≥3，路由修好但事实<3 会误翻绿 | 属实的前瞻风险：`judge_l0.pass` 已合取 mode==overview ∧ hits≥3，b1_red 却只取 mode | **采纳**：`b1_red = not l0_02["judge"]["pass"]`（整题通过才翻绿）+ 增 `l0_02_judge_pass` 字段。当前 L0-02 仍红，**基线值不变** |
| G3 | 高 | should_disambiguate 判据 `predicted=="should_disambiguate"` 恒 False，Phase C4 系统具备消歧能力后仍永远红 | 属实：`predicted` 被硬编码为 autopick，从不读已算出的 `nd`/`has_nd_field` | **采纳**（输出保持）：`predicted = "should_disambiguate" if (has_nd_field and nd) else "should_autopick"`。当前无 needs_disambiguation 字段→仍全 autopick，**AMB 数字不变**；字段落地后可翻绿 |
| G7 | 低 | is_bare_refusal docstring 写「mode=none 且…」但代码未查 mode，与 §7.5 及实现不符 | 属实：代码仅 `not has_action`，docstring 误导 | **采纳**：改 docstring 为「无任何可行动上下文；不设 mode 前置（§7.5；v1 概览恒兜底故实测恒 0）」，仅注释 |
| G1 | 高 | §7.3 的「linked 并列 top 数」代理未实现，`DELTA_SCORE=20` 成死代码，autopick 侧可能假绿抬高 0.7 | 部分属实：gate.py 刻意不用 tied 代理，docstring 明述「系统无消歧能力→一律预测自选」。实探 AMB-01 有 5 个并列 score=60 `invoke`，§7.3 代理会判 nd=True→给消歧「信用」，**反而高估**缺失能力 | **驳回代理部分**：gate.py 的「无能力=自选」更诚实、更保守（AMB 非硬门禁、当前 0.7 已是保守值）；实现 tied 代理会**过度授信**且改动冻结基线。DELTA_SCORE 保留为对齐 spec δ_score=20 的具名常量。前瞻可翻绿问题已由 G3 解决 |
| G2 | 高 | anchors_of `a.get("id") or entity_id or node_id`，`id` 优先且不分 mode，可能取到内部 id 致 FZ 恒空/错配 | **实探证伪**：topic 项键=[label,matched_terms,name,node_id,score]、symbol 项键=[entity_id,label,matched,method,name,score]，**无任何 linked 项带裸 `id` 键**；`or` 链正确落到 node_id/entity_id。且 V1 报告独立复现「recall node_id 序列=真实 topic_recall、hit@3=0.1 精确对齐」 | **驳回**：场景不可达，逻辑经实测正确 |
| G5 | 中 | `_NEG_WORDS` 含单字「无」，「无需/无法/无关」误触发 neg=True 抬高纠正率 | gate.py `_NEG_WORDS` 与 eval-design §7.4 **逐字一致**（设计原表即含「无」）；且 correction_rate 当前=0（非门禁、终判为 LLM/人工） | **驳回**：忠实于冻结的 §7.4 契约，改之反而背离；仅记为设计层共有的可改进项 |
| G6 | 中 | main 循环 build_repo_context 无 try/except，单题抛错整脚本崩、退出码 1 非 2 | 属实但方向安全：崩溃=响亮失败、不产报告、非 0 退出，**不会造成假绿**；门禁吞异常继续反而会掩盖问题 | **驳回**：fail-loud 对门禁是正确方向 |
| G8 | 低 | git_info `except:return ""` 静默吞异常，git 不可用时 tag_bound=False 无法与「未打标签」区分 | 属实但仅影响元数据、不影响红绿；门禁在无 git 环境仍应可跑 | **驳回**：可辩护，仅记 |

**假绿专项复核（外加）**：B-2=`fzd_hit3<0.8`（0.1 真红，anchors_of 已证正确）；B-3=`not pp_has_pf`（能力字段缺失=红，真实）；裸拒率 PASS 系概览恒兜底（计划书已认定裸拒率 0）；路由准确率/过问率/漏问率均 PENDING 未强制。**无任一被强制门禁因断言恒真/异常吞没而假绿**。

---

## 三、docs/RepoGraph-v0.3-spec.md 冻结块 + §B（1 条 → 驳回 1）

| # | 严重度 | opencode 意见 | 文件事实核对 | 处置 |
|---|---|---|---|---|
| S1 | 低 | §B.2 裁定「τ=0.15–0.20」与落地「≥0.15」不一致，上界 0.20 无实测支撑 | 送审仅内嵌了 τ=0.15 的数字，故模型误判上界无据；V3 报告实测 τ=0.20→13/15=0.867、τ=0.15→14/15=0.933，「区间 0.15–0.20 + 默认落 0.15（高召回端）」是标准表述，D-P2 亦同 | **驳回**：区间有实测支撑、区间+默认值自洽；系送审 cross-check 数据不全所致，非 spec 缺陷 |

spec 复审另**确认无误**：V0/V1/V2(=§B.4)/V3(=§B.2)/V4(=§B.3) 五项裁定全部写入；数值与 calibration.md 逐项吻合（零可行单元、hit、Jaccard、404/402、60 题补建）；与 DECISIONS D 编号无矛盾；FROZEN 声明 / R1「以实测为准」/ §4.6 指向注 / §B 自洽；「不冻结任何 topic 分带阈值」与「过渡规则仅方法档≥80 续用」无冲突；510 节点与 tag 正确。

---

## 四、已落地修订

- **DECISIONS.md**：① 状态取值补「重议中」；② 「六字段」→「五字段」；③ D-22、D-N3「259 节点」→「510 节点 / 259 函数」；④ D-P4 头行日期位补 `2026-07-22`。
- **eval/gate.py**：① b1_red 收紧为整题 judge 通过 + 增 `l0_02_judge_pass`；② judge_amb `predicted` 据 needs_disambiguation 字段翻转（输出保持）；③ is_bare_refusal docstring 纠正。
- **docs/RepoGraph-v0.3-spec.md**：无修订（唯一意见驳回）。

## 五、gate 复跑确认（`python eval/gate.py`）

- **B-1/B-2/B-3 仍全红**（Phase A 正确基线，未翻绿）。
- 复跑报告与提交版 `gate_report.json` **子集汇总、per_question 逐题判定、硬指标状态字节级一致**；差异仅两处：(a) 本次采纳的 B-1 `desc`/新增 `l0_02_judge_pass` 字段；(b) `meta.git`（HEAD 已从 rebaseline@8874a8d 前移至 phase-b-frozen@c0f3b7a → tag_bound=false），属 HEAD 位移的必然、非我方逻辑改动，任何一次复跑都会如此。
- 结论：三项修订**输出保持**（未改任何红绿），门禁无假绿。
