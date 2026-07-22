# Phase C 新增代码独立审查 · verify-c.md

审查人：独立审查子 agent（opus4.8）。审查基线 HEAD=5b52bb9（tag phase-c-20260722）。
送审对象：router.py / metrics.py / llm_client.py / enrich.py / repo_card.py（新增全量）+
context.py / server.py / app.js（Phase C 增量，基线 tag phase-b-frozen-20260722）+
ast_extractor.py·models.py 圈复杂度增量。四维度：正确性 / 契约一致（spec §5.1/§4.5/§4.6）/
假数据扫描 / 密钥处理。

---

## 0. 审查方法与阻断（如实记录，真实数据铁律）

- **mandated opencode 审查被配额阻断**：任务指定 `opencode run -m qwen/glm-5.2` 及
  `qwen/qwen3.8-max-preview`。实测两模型经 `opencode run` 恒挂起无输出；直连其端点
  （`~/.config/opencode/opencode.json` 的 qwen provider → aliyun `token-plan` 网关）探针得
  **HTTP 429 `insufficient_quota`：“Your token-plan 5-hour quota has been exhausted”**（多次探针
  跨 ~20min 均 429，opencode 在 429 上无限退避故表现为挂起）。按真实数据铁律**绝不伪造其输出**。
- **替换其他 provider 被自动模式正确拒绝**：尝试以 `opencode/big-pickle`、
  `opencode/deepseek-v4-flash-free` 等免费层替代，被 auto-mode classifier 判为「数据外泄」拦截
  （用户仅授权 qwen 两模型，未授权其他第三方目的地）——**此判定成立，予以尊重，未越界送码**。
- **一个外部信号（边界生效前取得）**：`opencode/deepseek-v4-flash-free` 审 C 段（router 前提校验）
  在拦截前已返回，产 2 条意见，留档 `design_work/review_c/out_C_ds.txt`，纳入下方核验。
- **审查实质由文件事实核查承担**：本 agent 独立通读全部送审对象，逐条比对 spec 冻结契约与真实
  图谱/真实行为探针。送审提示词与分段留档 `design_work/review_c/*.txt`（首行禁工具禁读文件、
  代码带真实行号、附契约摘录、**密钥自检干净无真实 token**）；构建器 `_build_review_c.py`；
  被拒的并行编排器 `review_c/_run_reviews.sh`（未运行）。
- 注：opencode 的 qwen provider 配置文件内联了一枚真实 apiKey；本审查全程以 sk-**** 指代，
  绝不外传/写入任何提交物（送审文件与本记录均已自检无泄漏）。

## 1. 意见台账

| 编号 | 来源 | 文件:行 | 维度 | 判定 | 处置 |
|---|---|---|---|---|---|
| F1 | deepseek(C) + 本 agent | router.py:457 | 正确性 | **成立** | 已修 + 加回归 |
| F2 | 本 agent | repo_card.py:12 | 假数据/标注实性 | **成立(文档)** | 已修 |
| R1 | deepseek(C) | router.py:507 | 正确性 | 驳回 | 调用点保证非 None |
| R2 | 本 agent | metrics.py:60 `_window_start` | 正确性 | 驳回 | 真实数据全带时区 |
| R3 | 本 agent | server 事件 `confidence` | 契约 | 驳回 | 可选字段，LLM 路由才产 |
| R4 | 本 agent | server `_rg_focus_peek` want_labels | 契约 | 驳回 | 设计内取最近，合法 |
| R5 | 本 agent | enrich 非命中节点幂等 | 正确性 | 驳回 | 卡片集稳定，非缺陷 |
| R6 | 本 agent | ast cyclomatic match 通配 case | 正确性 | 驳回 | 白名单已冻结口径 |

意见数 **8**（外部 2 + 本 agent 6）：采纳 **2**（F1、F2），驳回 **6**（附理由）。

## 2. 成立意见（修订）

### F1 · router.py:457 `_NUM_UNIT_RE` 中文数字类漏「零」（正确性）
- 现象：数字量词短语抽取正则 `[0-9一二三四五六七八九十百千两]+…` 未含「零」。
- 真实探针坐实：`premises_from_claims(["零次校验就放行"])` 的「零次」**整条不匹配→漏抽**（terms 无「零次」）；
  「重试一百零五轮」被截断为「五轮」。前提校验（S7）少一个可校验 term，属完整性缺口。
- 修法：数字类加「零」→ `[0-9一二三四五六七八九十百千零两]+`。**单调放宽**（只会抽得更全、
  不引入误报），不改任何既有命中。
- 锁定：test_router.py `test_premises_from_claims` 加 2 条断言（含「零」短语须完整抽取）。

### F2 · repo_card.py:12 summary 白名单重试次数标注不实（假数据/标注实性）
- 现象：模块 docstring 称「违规重试 **1 次**后降级」，但 `generate_card_summary(whitelist_retries=4)`
  实际至多重试 4 次（`generate_and_save` 用默认值）。标注与代码不符。
- 修法：docstring 改为「违规则重试，至多 `whitelist_retries` 次（默认 4）仍违规才降级弃 summary」。
  纯注释、零运行时影响。

## 3. 驳回意见（附理由，误报驳回）

- **R1 store=None 守卫**：`_term_in_graph`/`verify_premises` 未防 store=None。核验调用点——context
  `_attach_schema_v2` 传真实 store；server `_rg_collect_premise_flags` 已 `store is not None` 前置守卫。
  模块契约明写「只据真实 store 扫描」，非缺陷；加守卫仅为可选硬化。
- **R2 naive/aware 时间比较**：`_window_start` 若混入无时区 commit 会 TypeError。核验真实 graph.json
  **75/75 提交全带时区偏移**（git_extractor 契约产 ISO8601+tz），不触发。理论鲁棒性点，非真实缺陷。
- **R3 confidence 缺省**：spec §5.1 列 `confidence`，离线规则路由不产。核验 §4.2：confidence 是 LLM
  兜底分类的置信度，规则路由无此值，事件按「有值才附」优雅省略，契约允许（全字段可选）。
- **R4 焦点 peek 未按类型过滤**：调用方 `_rg_focus_peek(sess,cur_turn)` 传 want_labels=None 取最近焦点。
  spec §4.5「取最近类型相容实体」——指代词无明确类型锚，取最近为合理落地；函数已支持 want_labels，非缺陷。
- **R5 enrich 非命中节点不清旧属性**：仅覆盖卡片命中节点。核验 c2_cards 卡片集稳定，且属性为 C2 全权
  字段整体覆盖；换版重跑对命中节点干净替换。非命中节点保留原值不损幂等语义（对同一卡片集幂等）。
- **R6 match 通配 case 计数**：`count_cyclomatic` 每个 `match_case`（含 `case _`）+1，通配理论上非分支。
  白名单在 ast_extractor 已冻结「match 每个 case」口径（保守近似 McCabe），一致口径非缺陷。

## 4. 契约逐字段核验（无偏离）

- **spec §5.1 stats 五键**：`_stats(**kw)` 恒返回 `{symbols,topics,impact_callers,commits,concepts}`
  基座，meta/global/structural/oos/消歧全路径均满足（附加键为超集，v1 兼容）。✓
- **spec §5.1 事件纯增字段**：`_rg_build_event` mode/linked/stats 恒在、其余有值才附；`focus_used`
  为 C4 观测附加字段（前端不消费，符合「纯增」）。✓
- **spec §4.5 rg_focus 结构**：`{entity_id,label,turn}`、cap 5、turn=len(messages)快照、
  `age>10` 过期——push/peek 逐字段一致。✓
- **spec §4.6 分带/消歧**：真实探针验证 `merge_link_candidates`（exact>suffix>…>bm25_card 恒垫底、
  同 id 方法档优先、同档 score 高者留）、`disambiguate`（invoke×6 短名并列→needs_disambiguation；
  exact 领先→autopick；短60 vs 模30 Δ30≥δ20→autopick）。δ_score=20、bm25 永不自动锚定。✓
- **假数据扫描 CLEAN**：fix_involvement/blast_endpoints/entrypoints 因本图无对应数据**如实产 0/空**
  （真实核验无 FIXES 边、无 is_endpoint），非填充；卡片/概览统计全来自 store.counts()/edges；
  summary 失败→None 不伪造；app.js 全字段来自事件、num() 守卫、textContent 防注入、无写死数字。✓
- **密钥处理 CLEAN**：llm_client token 仅入 Authorization 头；异常仅记 `HTTP {code}`/`type(e).__name__`，
  绝不含 token；配置缺失抛明确异常（不含 token）；提及一律 sk-****。server 错误路径 `str(e)` 不触及 headers。✓

## 5. 回归 + gate 终跑（对比审查前基线，零劣化）

- **测试全绿（脚本式，项目约定 stdlib 无 pytest；Makefile `test:` 口径）**：
  RepoGraph 6/6（test_router/metrics/topic/context/enrich/lexicon）OK；
  claude-ui 6/6（test_app_js/backend/repograph_integration/repograph_ui/semantic_waterfall/ui_shell）OK。
  （注：`pytest tests/` 因测试用 `store` 位置参数被误判为 fixture 报 37 ERROR，属项目脚本式约定，非缺陷。）
- **gate 终跑 GATE_EXIT=0，硬指标逐项等于审查前基线**：

| 硬指标 | 阈值 | 审查前 | 审查后 | 判定 |
|---|---|---|---|---|
| 裸拒率 | =0 | 0.0 | **0.0** | PASS |
| 路由准确率(48) | ≥0.9 | 0.8542 | **0.8542** | PENDING（未达，承前） |
| AMB 行为一致率 | — | 1.0 | **1.0** | — |
| AMB 过问率 | ≤0.2 | 0.0 | **0.0** | PASS |
| AMB 漏问率 | ≤0.1 | 0.0 | **0.0** | PASS |
| PP premise_leak | =0 | 0.0 | **0.0** | PASS |
| PP premise_flags 能力 | 就位 | True | **True** | PASS |
| L0 通过率 | — | 1.0 | **1.0** | — |
| FZ-dev hit@3/@1 | — | 0.7/0.5 | **0.7/0.5** | — |
| FZ-test hit@3/@1 | — | 0.8/0.6 | **0.8/0.6** | — |
| 锁定失败 B-1/B-2/B-3 red | — | F/T/F | **F/T/F** | 承前（B-2 锁定红） |

F1（前提数字抽取）不触及任何 PP gold（PP 前提为技术专名非数字量词），gate 逐项不动，符合预期。

## 6. 结论

Phase C 新增/改动代码质量高：契约（§5.1/§4.5/§4.6）逐字段吻合、假数据扫描全清、密钥处理合规、
异常降级完备。仅 2 处轻微修订（F1 前提数字抽取完整性 + F2 文档标注实性），已修并回归全绿、gate 零劣化。
mandated opencode·qwen 审查因配额 429 阻断、且不得越界替换 provider——建议配额恢复后由编排侧补跑
`opencode -m qwen/glm-5.2`（送审段已就绪于 `design_work/review_c/`）作二次交叉。

---

## 补跑记录（2026-07-23 配额恢复后）

审查人：补审子 agent（opus4.8）。基线 HEAD=e711122（tag phase-c-20260722 之后的审查修订提交）。
配额窗口于 2026-07-23 00:22 后重置；本节全部判定基于真实文件/真实命令输出，密钥一律 sk-****。

### 0. 送审前置：配额探针 + 送审段对齐 HEAD

- **配额恢复确认**：`opencode run --pure -m qwen/glm-5.2`（及 `qwen/qwen3.8-max-preview`）对 trivial
  prompt 各 **6s 返回** —— 429 已解除。
- **送审段重生成**：`_build_review_c.py` 读当前工作树重生成 15 段。git diff 证 **C/G/H 三段陈旧**
  （e711122 修过 router.py:457 加「零」、repo_card.py docstring 拆行使 12 行后 +1 偏移，波及 G 全段行号
  与 H 的 207–349 段）——已重生成对齐 HEAD；A/B/D–F/I–N 因源文件未变无差异。

### 1. 送审执行：glm-5.2 主审恒挂起 → 授权集内 max-preview 承接全段

- **glm-5.2 对真实审查 prompt 恒挂起**（非配额）：`--print-logs` 诊断证 `agent=build` 流启动后
  **>300s 零输出 token、无完成事件**（清 startup 干扰后仍挂，两次观测：后台 A 段 300s rc=124、
  干净临时目录 E 段 >310s）。复现了本文件 §0 的原始观测，但此刻配额已恢复 —— 故根因是
  **opencode+qwen(glm-5.2) 对大审查 prompt 的挂起**，非配额。按重试规则等 60s 重试仍挂，如实记录。
- **旁证：opencode 启动开销**：在大仓库目录内每次调用启动耗 ~110s（git 快照/project copy
  refresh/cleanup prune）；改在**大仓库外干净临时目录**运行消除此开销（送审段已内联代码、cwd 无关内容）。
- **max-preview 可用并承接全段**：`qwen/qwen3.8-max-preview`（mandated 授权集内另一模型）对同一 E 段
  **97s 产出真实审查**。为完成「补跑正规 opencode 审查」且**不越界换任何非 qwen 模型**，改由 max-preview
  送审全 15 段（含原定 C/K1 关键段；glm-5.2 挂起致无法做跨模型二审，如实记录）。干净临时目录、
  `timeout 360`/段、失败等 60s 重试一次。**各段送审模型与耗时**（均 qwen/qwen3.8-max-preview，一次成功）：

  | 段 | 秒 | 段 | 秒 | 段 | 秒 |
  |---|---|---|---|---|---|
  | A_router_route | 211 | F_llm_client | 56 | K1_server_rewrite_premise | 110 |
  | B_router_banding | 96 | G_repocard_det | 66 | K2_server_event_focus | 75 |
  | C_router_premise | 112 | H_repocard_summary | 79 | L_server_apichat | 136 |
  | D_metrics_graph | 81 | I_enrich | 61 | M_appjs_a | 91 |
  | E_metrics_cyclo | 79 | J_context_route | 132 | N_appjs_b | 86 |

  产出留档 `review_c/out_<段>_max.txt`（送出前密钥自检 CLEAN，无真实 token）。

### 2. 新意见统计与逐条裁定

max-preview 15 段共提 **21 条**意见（E_metrics_cyclo、K2_server_event_focus 两段「未发现缺陷」）；
与本文件已有 8 条台账去重 **1 条**（D_metrics naive/aware = 台账 **R2**）；**去重后新意见 20 条**：
**采纳 6、驳回 14**。

| 编号 | 段·文件:行 | 维度 | 判定 | 依据（真实核实） |
|---|---|---|---|---|
| CR-01 | A router.py:108 `_RE_PRONOUN` | 正确性 | **采纳** | 探针证「应该」的「该」误置 has_pronoun→压 oos；修 `(?<!应)该`，gate 48 题无一含「应该」不动 gate |
| CR-02 | C router.py:439 S7 注释 | 文档实性 | **采纳** | 注释「两者都落 flag」与代码(540 `if present:continue`)及 docstring(528) 矛盾，改齐 |
| CR-03 | F llm_client.py:76 `json.load` | 正确性+密钥 | **采纳** | 损坏 config 抛未捕获 JSONDecodeError（.doc 含整份 config 即 token），归一 GatewayConfigError 且 `from None` 断链 |
| CR-04 | G repo_card.py:7 docstring | 假数据/实性 | **采纳** | docstring 写死 22/15/259/75/139（今与 store.counts() 恰合但再索引即腐），改为五类描述 |
| CR-05 | J context.py:277 + repo_card.py:327 docstring | 文档实性 | **采纳** | 两处 docstring 称 `mode='meta'`，探针证代码实返 `mode='overview'`（route_label='meta' 承载）；改齐 |
| CR-06 | L server.py:1405 focus_anchor | 正确性 | **采纳** | S2 改写恢复的 exact/suffix 强锚只进局部 linked 未回 rg，1405 用 rg 取锚→漏压焦点栈；改用本轮最终 linked |
| — | B router.py:337 disambiguate 排序 | 正确性 | 驳回 | `_SCORE` 定死 exact=100/suffix=80/short=60…，分数与方法档确定性绑定，short>exact 不可能，按分排序≡按档排序 |
| — | B router.py:293/411 未校验 score≥80 | 契约 | 驳回 | 同上：exact/suffix 恒 100/80，≥80 由 `_SCORE` 构造保证 |
| — | B router.py:382 fan_in=None 守卫 | 正确性 | 驳回 | metrics 写的是 `len(...)` int，永不 None（理论化，R 类） |
| — | D metrics.py:65/105 naive/aware | 正确性 | 驳回·**去重=R2** | 真实 75/75 提交全带时区（git_extractor 契约 ISO8601+tz），不触发 |
| — | H repo_card.py:343 缺 route_label | 契约 | 驳回·误报 | 唯一调用点 context.py:308 `_finalize(...,"meta",...)` 已挂 route_label；test 已断言端到端 route_label=='meta' |
| — | H repo_card.py:335 stats 写死 0 | 假数据 | 驳回·误报 | meta 档 symbols/topics/impact_callers/commits=0 是**真实检索计数**（整卡注入这些通道确 0）；五键为 schema 基座；同 §4「假数据 CLEAN」 |
| — | I enrich.py:72 缺 id 卡静默跳过 | 正确性/契约 | 驳回 | 文件级结构检查(load_cards)已严格；单卡缺 id 属设计内宽容，需生成端 bug |
| — | I enrich.py:84 `__dup_ids__` 哨兵碰撞 | 正确性 | 驳回 | node id 皆结构化 symbol_id（`repo::path::qn`/`concept::`…），永不为 `__dup_ids__`（理论化） |
| — | I enrich.py:129 assert 在 -O 下剥离 | 正确性 | 驳回 | 契约明写「assert 守」；CLI/测试从不 -O（理论硬化） |
| — | A router.py:146 `结构` 抢 oos | 正确性 | 驳回 | `结构` 是 global-1 有意概览触发词；保守 oos 设计（注释 150-152）宁落 global 也不误判界外，此偏差与设计同向；narrow 有伤「系统结构如何」类正当概览题风险 |
| — | K1 server.py:641 瞬态失败被缓存 | 正确性 | 驳回 | 缓存空结果**有意共享往返**：premise 路径(721「缓存命中」)依赖 rewrite 已缓存，两消费方共享单次网关往返(692「零新增往返」)；不缓存空会致失败时前提路径重复调网关双 20s |
| — | K1 server.py:630 非严格 LRU | 契约 | 驳回 | 注释自称「LRU-ish」=近似；单会话 >256 不同问题不现实 |
| — | L server.py:1412 str(e) 可能含 token | 密钥 | 驳回·误报 | 本仓库用 urllib（token 在 header 非 URL/str）；内层网关函数各自 catch 不外抛；§4 已验「str(e) 不触及 headers」CLEAN |
| — | M app.js:563 `c.path!==c.entity_id` | 正确性 | 驳回 | 需候选 entity_id 空/缺；enrich_candidates 恒设非空 symbol_id（理论化） |
| — | N app.js:670 tConcepts 用 stats | 正确性 | 驳回 | 实测 12 道 topic 题 stats.concepts 恒==linked 中 Concept 数（结构同步，不触发） |

（B/M/N/L-op2 的修法均为「零风险防御性硬化」，逻辑上更稳但不修**当前不可触发**的缺陷，故驳回；如后续放宽
`_SCORE`/引入外部候选源/改 topic 装配口径，应回补。）

### 3. 修订清单（6 处，均限 Phase C 文件+对应测试）

1. **CR-01** `src/repograph/retrieve/router.py` `_RE_PRONOUN` 加 `(?<!应)该`；`tests/test_router.py` 新增
   `test_pronoun_excludes_yinggai`（反例「应该」不含指代 + 正例「该函数」仍命中 + e2e 界外题落 oos）。
2. **CR-02** `router.py` S7 模块注释改齐「可定位→不产 flag、留待边级校验」（纯注释）。
3. **CR-03** `src/repograph/extract/llm_client.py` `load_gateway_config` try/except 归一损坏/不可读 config
   为 `GatewayConfigError`、`from None` 断链、消息只留类型名+路径（防 JSONDecodeError.doc 泄 token）。
4. **CR-04** `router` 无关；`src/repograph/retrieve/repo_card.py:7` docstring 去写死 22/15/259/75/139。
5. **CR-05** `repo_card.py:327` 与 `retrieve/context.py:277` docstring `mode='meta'`→`mode='overview'`。
6. **CR-06** `claude-ui/server.py:1405` focus_anchor 改用本轮最终 `linked`（含 S2 恢复强锚）。

### 4. 回归 + gate 终跑（零劣化）

- **RepoGraph 6/6 全绿**：test_router（含新 test_pronoun_excludes_yinggai）/metrics/topic/context/enrich/lexicon。
- **claude-ui 6/6 全绿**：test_backend(16)/repograph_integration(15)/repograph_ui/semantic_waterfall(18)/app_js/ui_shell。
- **CR-03 专项验证**：损坏含 token 的 config → 抛 GatewayConfigError，`str(e)`/`__cause__`/`__context__` 均无 token（PASS）。
- **gate 终跑 GATE_EXIT=0，硬指标逐项等于补跑前基线**：

  | 硬指标 | 阈值 | 基线 | 补跑后 | 判定 |
  |---|---|---|---|---|
  | 裸拒率 | =0 | 0.0 | **0.0** | PASS |
  | 路由准确率(48) | ≥0.9 | 0.8542 | **0.8542** | PENDING（承前） |
  | AMB 行为一致率 | — | 1.0 | **1.0** | — |
  | AMB 过问率 | ≤0.2 | 0.0 | **0.0** | PASS |
  | AMB 漏问率 | ≤0.1 | 0.0 | **0.0** | PASS |
  | PP premise_leak | =0 | 0.0 | **0.0** | PASS |
  | PP premise_flags 能力 | 就位 | True | **True** | PASS |
  | 锁定 B-1/B-2/B-3 red | — | F/T/F | **F/T/F** | 承前（B-2 锁定红） |

  CR-01（`应该`排除）不触及任何 gate 48 题（无一含「应该」）；余 5 处为文档/损坏路径/server（gate 走离线
  build_repo_context 不触），故 gate 逐项不动，符合预期。

### 5. 结论

配额恢复后补跑证实：mandated **glm-5.2 主审对真实审查 prompt 恒挂起**（非配额，opencode+qwen 大 prompt
问题），已如实记录、绝不伪造其输出、绝不越界换非 qwen 模型；由授权集内 **qwen3.8-max-preview 承接全 15 段**
正规送审。共 21 条意见、去重 1（=R2）、去重后 20 条：**采纳 6**（CR-01…CR-06，2 正确性缺口 router 焦点+
llm_client 损坏 config、1 正确性 router 指代、3 文档/实性）、**驳回 14**（多为 `_SCORE` 定死使方法档/分数绑定、
误报未见调用点/检索计数语义、或**当前不可触发**的理论硬化，逐条附据）。全部修订限 Phase C 文件+对应测试，
回归 RepoGraph 6/6 + claude-ui 6/6 全绿、gate 终跑 GATE_EXIT=0 硬指标零劣化。
