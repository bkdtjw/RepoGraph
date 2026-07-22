# grok 独立审查记录 · 落地设计终稿

**被审对象**：`RepoGraph-模糊语义处理-落地设计.md`（v0.2 落地版，348 行）
**审查器**：grok CLI（`C:/Users/nirvana/.grok/downloads/grok-windows-x86_64.exe`，`-p <文件> --max-turns 3 --disable-web-search --verbatim`），经 Python subprocess 捕获 bytes 双解码（utf-8/gbk）规避 GBK 乱码。
**分段**：终稿 >250 行，切 2 段送审——seg1=§0–§4（含决策表+架构+文件清单）、seg2=§5–§8+附录（附 seg1 结尾 50 行做衔接）。提示词首行硬性「禁止用工具/读文件，只依据内嵌全文作答」，第 1 段另嵌原稿机制清单供「遗漏」维度核对。
**产物**：提示词 `grok_prompt_seg1/2.txt`、原始输出 `grok_out_seg1/2.txt`、构建/运行脚本 `build_grok_prompts.py`/`run_grok.py`（均在 `design_work/`）。
**安全**：送审内容与终稿均无真实密钥（grep `sk-` 全仓 md 零命中）；grok stderr 出现一次 402（初次试连 `grok-build` 模型失败后回退，正文审查正常完成、逐条咬合文档真实内容，判定其输出可信可用）。

**审查维度**：(1) 内部一致性 (2) 可行性(stdlib+网关约束) (3) 与五原则 P1–P5 符合度 (4) 评测题目程序可断言性 (5) 决策表遗漏。

**结论计数**：grok 两段共约 40 条独立意见（跨段大量重复，合并去重后 36 条独立问题）。经逐条 Read 源码核对：**采纳 29 条、驳回 7 条**，另派生 1 处一致性对齐（§6.2 L0 口径同步）。终稿共修订 30 处。

---

## 一、核对所依据的源码事实（Read/Grep 实证，非臆断）

| 事实 | 位置 | 用途 |
|---|---|---|
| `link_entities` 在 `build_repo_context` 入口**无条件首跑** | `context.py:217` | 定 route↔linker 时序：linker 先跑、喂 route()，`no_linker_hit` 是链接后信号（采纳 C16/C18） |
| method 枚举 = exact_qualname/suffix_qualname/short_name/concept_name/module_path | `context.py:31,92,130-155` | 统一 method 命名；佐证 §4.6 分带 |
| `build_overview` 硬编码 `mode='overview'`，stats 缺 issues/time_span/… | `context.py:744,742` | 定 route_label 与事件 mode 两命名空间分离（采纳 C2） |
| `_META_MARKERS` 确无「破仓库/干啥/晓得」 | `context.py:444-449` | 佐证 L0-02 误路由属实（终稿主张正确） |
| `impact._resolve_symbol` 遇歧义 return `{error:ambiguous,candidates}`，**在 `_impact_calls` 遍历前** | `impact.py:42,47,146-152` | **驳回**「impact 违反 P3」——P3 行为已满足（R2） |
| `_reverse_adjacency`/`_bfs_levels` 存在且可复用纯函数 | `impact.py:56,89` | **驳回/确认自洽**：metrics 复用主张成立（R6） |
| `query_graph`/text2cypher 全仓不存在（仅 age.py 注释提及） | grep `src` | 原稿「query_graph 不变」无对象→不适用（采纳 C25） |
| 索引期 grok 通道 `ask_grok`（`--json-schema`+候选白名单） | `extract/grok_client.py:51`、`semantic.py:11`、`cli.py:261` | summary/中文卡片复用之、缺失优雅降级（采纳 C6） |
| SSE 事件现发 `{mode,linked,stats}` | `server.py:1016` | schema v2 = 扩此 dict（采纳 C1/C22/C23） |
| `_rg_normalize_mode→_RG_VALID_MODES`、`_rg_inject_prefix` 按 mode 三分支（无 meta/global） | `server.py:534/539,525` | 新增 meta/global 需同步扩两处（采纳 C2 可行性补点） |
| `read_session/write_session`（atomic_write JSON） | `server.py:610/618` | rg_focus 载体（采纳 C19） |
| `_MIN_SCORE=1.0`、`_DOC_LABELS=(Concept,Commit,Module)`、召回项 `{node_id,label,score,matched_terms}` | `topic.py:35,38` | bm25_card 候选映射（采纳 C5） |

---

## 二、采纳意见（29 条）——逐条：来源 · 维度 · 核对 · 修订落点

### 高严重度（发布阻断级）

- **C1｜schema 缺路由字段（seg1-2）**｜维度1｜核对：§4.2 要求透传路由决策供 §6.2 路由准确率，但事件无独立 `route_label`（route_source/confidence 原已在，grok 未见 §5.1 故部分误报，但 route_label 确缺——五分类无法从 8 值 mode 反推，因 symbol/topic/llm 均属 entity_local）。**修订**：§5.1 JSON 加 `route_label`。

- **C2｜mode 值域 / entity_local vs symbol / overview 缺失（seg1-3, seg2-1/2）**｜维度1+2｜核对：`build_overview` 恒返 `overview`（`context.py:744`），meta/global 是**路由标签**非现有 mode；两命名空间在原稿混用。**修订**：§5.1 补「route_label(5)↔事件 mode(9,含 overview)」分离说明；§4.7 补 `_rg_normalize_mode/_RG_VALID_MODES/_rg_inject_prefix` 扩点。

- **C3｜S4 分带缝隙：单一 short/concept 候选未定义（seg1-4）**｜维度1｜核对：§4.6 自动锚定仅 exact/suffix、消歧要求「多候选」，单一弱候选无归属。**修订**：§4.6 补「单一弱候选→autopick+degraded+披露，不进消歧」。

- **C4｜§4.7 文件清单缺 disambiguate/verify_premises/merge/S6 落点（seg1-8）**｜维度2｜核对：§3.2/§4.5 提及函数但 §4.7 无落点。**修订**：§4.7 router.py 行补四函数。

- **C5｜BM25-over-实体卡片候选映射悬空（seg1-9）**｜维度2｜核对：`topic_recall` 返回 `{node_id,…}`，未定义如何变链接候选。**修订**：§4.6 补 `{entity_id=node_id,score,method='bm25_card'}` + 去重 + 不重复展开。

- **C6｜索引期 LLM 通道未论证 + 缺卡降级（seg1-10, seg2-11/12）**｜维度2+3(P4)｜核对：`ask_grok` 通道真实存在。**修订**：§3.1/§4.7/§4.2/附录C 复用 `ask_grok`、`GrokError`/缺卡→纯确定性卡片 + `build_overview` 兜底、meta 不裸拒。

- **C7｜刷新策略遗漏（seg1-11/21）**｜维度5+2｜核对：§3 无刷新/水位行，§1.3 点名缺水位。**修订**：§3.1 表外附属决策①（砍增量、全量重算、一致性窗口诚实标注）。

- **C8｜out_of_scope 可裸拒违 P4（seg1-16, seg2-17）**｜维度3(P4)｜核对：§4.2/附录C 界外仅声明。**修订**：§4.2/§5.2/附录C 界外必带 ≥1 建议 + degraded，入裸拒率白名单。

- **C9｜structural 降级吞没路由标签、P1/P2/P3 折中未声明（seg1-13/17, seg2-4/15）**｜维度1+2+3｜核对：附录C 仅「降级 topic」。**修订**：§4.2/§8 F8/附录C——保 `route_label=structural`+`degraded`；能定量走 `build_overview`；显式披露让渡 P1、守 P2。

- **C10｜premise 强制纠正仅靠 prompt 却绑硬门禁；PP 幻觉率=0 不可机械复现（seg2-13/16/22）**｜维度2+3+4｜核对：§5.2 无程序闸门、§6.1 PP 判定纯裁判。**修订**：§5.2 premise_flags 必落+固定纠正前缀闸门；§6.2 幻觉率=0 改**程序负样式**（缺席技术栈词表 + 错误结构常量），裁判仅辅助。

- **C11｜§6 开篇「全部程序可断言」与 FZ/PP 裁判自相矛盾（seg2-18）**｜维度4｜核对：开篇 line 248 vs FZ「答案准确率(裁判)」/PP「裁判判」。**修订**：开篇拆「程序门禁集 / 裁判报告集」，后者不进硬门禁。

- **C12｜路由准确率用语义等价集合放水（seg2-2/20）**｜维度4｜核对：等价并集使 meta↔global 互错判对。**修订**：§6.2 改 `route_label` 精确匹配为唯一门禁；等价集合降为「概览能力命中率」诊断项。

- **C13｜AMB 判定循环、无过问/漏问公式（seg2-21）**｜维度4｜核对：原式把 should_* 定义为系统输出=重言，未对 gold。**修订**：§6.1 改为对 gold_behavior，补过问/漏问/一致率公式。

- **C18｜oos-1 正则误判仓库内概念（seg2-10）**｜维度2｜核对：「什么是适配层」（`适配层`∈L0_FACTS）会中 oos-1。**修订**：§4.2 定义 `no_repo_reference` 组合谓词；附录A 注明正则只作触发候选、组合谓词把关。

### 中/低严重度

- **C14｜裸拒率=0 无匹配模式（seg2-23）**｜维度4｜**修订**：§6.2 给程序负样式定义 + oos 例外。
- **C15｜§6.1 基线注记串到原稿章节号 §5.3/§5.7（seg2-5）**｜维度1｜核对：本稿 §5.3=前端、无 §5.7。**修订**：改指 §4.4/§3.2 S7。
- **C16｜route↔linker 时序未定义（seg2-8）**｜维度2｜核对：`context.py:217` linker 首跑。**修订**：§4.2 形态段写死时序。
- **C17｜附录A entity-1 无 pattern 吞尽含符号问句（seg2-9）**｜维度2｜**修订**：附录A 注明为默认桶 + 误落由兜底纠偏 + 回归用例。
- **C19｜rg_focus 结构/turn 未定义（seg1-12）**｜维度2｜**修订**：§4.5 冻结 `[{entity_id,label,turn}]` + TTL 规则。
- **C20｜cyclomatic AST 节点集未封口（seg1-14）**｜维度2｜**修订**：§3.1 补 `match_case`、`assert` 不计、白名单写死。
- **C21｜非 llm 档 P5 回显保底缺（seg1-18）**｜维度3(P5)｜**修订**：§4.5 补非 llm 档指代无解→原样回显+提示点名，禁静默错锚。
- **C22｜linked.entity_id 与 candidates.id 字段名不一（seg2-6）**｜维度1｜核对：`link_entities` 输出 `entity_id`。**修订**：§5.1 candidates 统一 `entity_id`。
- **C23｜route_source 值域不全（seg2-7）**｜维度1｜**修订**：§5.1 改 `rule:<id>|llm|fallback:<reason>`。
- **C24｜死代码 A7 漏列（seg1-22）**｜维度5｜**修订**：§3.1 表外附属决策②（砍除/不适用）。
- **C25｜query_graph 不变漏列（seg1-23）**｜维度5｜核对：query_graph 不存在。**修订**：§3.3 impact 行注明不适用。
- **C26｜§0 未声明决策表范围（seg1-24/25）**｜维度5｜**修订**：§0 补范围声明（§3 只覆盖 §4–§7 的 24 机制；§8–§11 承接进本稿 §6–§8；成本量级不变）。
- **C27｜answer_general 归属不明（seg2-14）**｜维度2｜**修订**：§4.2 注明 config 项 + server.api_chat 界外分支、无新服务。
- **C28｜level-0「原样采纳」标签存疑（seg1-5）**｜维度1｜判：机制设计不变、summary 沿用原稿既有单次 LLM 调用，**保留原样**但加澄清括注（半采纳）。
- **C29｜附录C「四档瀑布(symbol→topic→llm)」括号内仅三档（seg2-3）**｜维度1｜**修订**：改「三档检索+概览兜底」，并明确兜底 mode/degraded。
- **（派生）C30｜§6.2 L0 事实达标率口径同步**：随 C12/C19 把 L0 判定改到来源侧后，§6.2 对应行由「mode∈overview 类」同步为「route_label∈{meta,global}+注入上下文命中」，防新生不一致。

---

## 三、驳回意见（7 条）——附源码理由

- **R1｜24 机制计数不可复核（seg1-7）**：驳回。实数 §3 决策表 = 9(§3.1)+9(§3.2)+6(§3.3)=**24 行**，原样10/改造10/砍除4 精确加总（已在 §0 补「逐行加总可复核」并把刷新策略/死代码列为**表外**附属，不破坏 24 计数）。
- **R2｜impact 违反 P3（seg1-19）**：驳回。`impact._resolve_symbol` 遇歧义 return `{error:ambiguous}` 且**在 `_impact_calls` 之前**（`impact.py:42,47,146-152`），遍历不执行，P3 行为已满足；缺的仅响应格式（原行已披露）。已在 §3.3 加实证注解澄清。
- **R3｜§10 风险 F1–F7 漏映射（seg1-26）**：驳回（误报）。grok 仅见 seg1（止于 §4），本稿 §8 已完整映射 F1–F7 + 新增 F8/F9。
- **R4｜§11 计划 P0–P3 漏依赖序（seg1-27）**：驳回（误报）。本稿 §7 已有 P0–P3 分期 + 依赖行（P1 依赖 P0…），grok 未见 §7。
- **R5｜指标披露应单列第五条（seg1-28）**：驳回（已覆盖）。§3.3/§5.2 的「代理定义披露」即指标定义披露；已在 §5.2 加一句显式说明「等价于原稿第五条、并入不另设」。
- **R6｜metrics 复用 impact 需源码核实（seg1-15）**：驳回为「已核实自洽」。`_reverse_adjacency`/`_bfs_levels` 存在可复用（`impact.py:56,89`），无需改文。
- **R7｜seg1 无法评估维度(4)（seg1-20）**：非缺陷，grok 自述 seg1 不含 §6，维度4 由 seg2 覆盖，无需处理。

---

## 四、修订对终稿的净影响

- **一致性**：schema 增 `route_label`、`candidates.entity_id` 统一、mode 补 `overview`、附录C 回退速查与正文对齐、§6.1↔§6.2 L0 口径一致、基线注记章节号纠偏。
- **可行性闭环**：`_rg_normalize_mode/_rg_inject_prefix` 扩点、disambiguate/verify_premises/merge/S6 落点、bm25_card 候选 schema、rg_focus 结构、build.py 调 ask_grok 产 repo_card、answer_general 归属、cyclomatic 节点封口——全部补齐落点。
- **原则守护**：out_of_scope/meta 缺卡不裸拒（P4）、structural 折中显式披露（P1/P2）、非 llm 档 P5 保底、premise 程序化闸门（P5 可观测）。
- **评测硬化**：程序门禁 vs 裁判报告分离、路由准确率精确匹配、AMB 过问/漏问公式化、PP 幻觉率与裸拒率改程序负样式——四项硬门禁均落到可机械断言。

净：本轮把原稿假设与代码实况的最后几处**悬空集成**（路由字段、BM25 候选、索引 LLM 通道、premise 闸门）与**评测不可断言项**补实，未推翻任何既有决策方向。
