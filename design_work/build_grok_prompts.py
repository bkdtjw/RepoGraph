# -*- coding: utf-8 -*-
"""把落地设计终稿切片为 2 段审查提示词，写入 design_work。仅读 md、仅写 design_work。"""
import io, os

SRC = r"C:/Users/nirvana/Desktop/代码库知识图谱/RepoGraph-模糊语义处理-落地设计.md"
OUT = r"C:/Users/nirvana/Desktop/代码库知识图谱/design_work"

with io.open(SRC, "r", encoding="utf-8") as f:
    lines = f.readlines()
n = len(lines)

# 切分：seg1 = 1..205；seg2 衔接 = 156..205；seg2 正文 = 206..end
seg1 = "".join(lines[0:205])
seg2_cont = "".join(lines[155:205])
seg2_main = "".join(lines[205:n])

HEADER = u"""禁止使用任何工具、禁止读取任何文件、禁止联网检索；只依据本提示词下方内嵌的《落地设计》文档全文直接作答，不得臆测文档以外的代码细节。

你是一名资深架构评审。被审对象是一份中文技术落地设计文档《RepoGraph 模糊语义处理·落地设计》（v0.2 落地版）。背景：RepoGraph 是代码知识图谱系统。原 v0.2 设计稿假设存在向量层/embedding、MCP 工具层、SQL 中文别名表、进程内单会话焦点栈等基础设施，但经核对真实代码库中这些都不存在；真实检索路径是纯 stdlib 的“四层瀑布”（context.py 符号→topic.py 主题→LLM 受限概念→build_overview 概览兜底），集成形态是 claude-ui 网关把检索上下文注入聊天 system prompt（push 模型、无工具、会话存磁盘 JSON）。本落地稿的任务是把原稿每条机制重定位到“stdlib + 网关注入”的现实。

请严格按以下 5 个维度逐项审查。每发现一个问题给一条记录，一行一条，格式：
【编号】| 维度号 | 定位(章节号/机制名) | 问题描述(具体、可核对) | 严重度(高/中/低) | 修改建议
只报真问题，不要客套；某维度若无问题请明确写“未发现问题”。若某条需要看源码才能最终确认，请在描述里标“需源码核实”。

审查维度：
(1) 内部一致性：决策表(§3)与架构/接口/附录章节是否自相矛盾——例如某机制在决策表判“砍除”但在架构图/文件清单/附录里又出现并被启用；mode 值域、SSE 事件字段、回退关系速查是否前后一致；同一参数（如分数阈值/δ）在不同处定义是否冲突。
(2) 可行性：§4.7 文件级改动清单与各机制改法，在“纯 stdlib + 网关注入、无向量层 / 无 MCP / 无 SQL 表 / 无第三方库”的约束下是否成立；有无机制在“砍除”后仍被下游依赖（悬空引用）；纯 Python 幂迭代 PageRank、AST 圈复杂度、BM25 语料扩容、缩写扩展等改法是否自洽、有无遗漏前置。
(3) 与五原则 P1–P5 的符合度：有无某决策实际违反了它标注的原则却未说明。P1 答案来源前置 / P2 路由显式化 / P3 确定性工具不吃模糊输入 / P4 永不裸拒 / P5 解释回显。
(4) 评测题目健全性：§6 的判定方式是否真“程序可断言”（不靠主观）；facts_hit≥3、anchor hit@k、needs_disambiguation 判定、路由准确率“语义等价集合”、预设幻觉率=0 等门禁能否机械执行；有无循环定义、无法测量项、或裁判(LLM)与程序判定混淆。
(5) 遗漏：对照下方“原稿机制清单”，决策表(§3)是否有原稿机制被漏列、未给出采纳/改造/砍除决策。
"""

INVENTORY = u"""
========== 原稿（v0.2 补充稿）机制清单（供维度(5)核对，勿逐条复述，只标出被漏列者） ==========
§4.1 level-0 仓库卡片；level-1 包级概览；刷新策略（随增量水位重算）
§4.2 实体卡片中文功能描述（LLM≤40字）；中文别名入 symbol_alias(zh_alias) 表；缩写扩展表
§4.3 fan_in/pagerank；heat；cyclomatic/loc；blast_radius/fix_involvement；死代码(A7,非属性)；churn_90d；披露话术模板
§4.4 中文停用/疑问词表；代码词元检测器
§5.1 S0 规范化
§5.2 S1 路由器（规则表+LLM兜底+5标签+置信度<0.6→global+路由日志）
§5.3 S2 改写扩展（queries/symbol_guesses/premises 合并调用）；HyDE 变体
§5.4 S3 链接 v2（三段阈值带 τ_hi/τ_lo/δ）
§5.5 S4 消歧协议（needs_disambiguation/唯一强候选自选/澄清预算）
§5.6 S5 会话焦点栈（最近5实体/TTL10/指代词触发/回显）；ask_repo 新增 context 参数
§5.7 S7 前提校验
§5.8 S6 回退阶梯
§6 生成期四约束：边界三段式；代理披露；消解回显 resolved_query；前提处理；改写产物隔离段
§7.1 响应 schema v2
§7.2 repo_overview MCP 工具
§7.3 impact_analysis 模糊输入行为；query_graph 不变
§8 评测四子集/新增指标/主表扩展/阈值校准
§9 成本延迟预算
§10 风险 F1–F7
§11 实施计划 P0–P3
========== 清单结束 ==========
"""

seg1_prompt = (
    HEADER + INVENTORY +
    u"\n本段为终稿第 1/2 段（§0 概览 ~ §4 落地架构，含逐机制决策表与文件级改动清单）。维度(1)(2)(3)(5)在本段均可充分评估，维度(4)评测在第2段。\n\n"
    u"========== 待审文档（第 1/2 段）开始 ==========\n" + seg1 +
    u"\n========== 待审文档（第 1/2 段）结束 ==========\n"
)

seg2_prompt = (
    HEADER +
    u"\n本段为终稿第 2/2 段（§5 接口契约 ~ §8 风险表 + 附录ABC）。维度(4)评测健全性、(1)附录规则与正文一致性、(2)附录规则/prompt 可行性在本段重点评估；(5)遗漏已在第1段决策表评估，本段不必重复。\n\n"
    u"---- 上文衔接（第1段结尾，仅供理解上下文，不需重复审查）----\n" + seg2_cont +
    u"\n---- 上文衔接结束 ----\n\n"
    u"========== 待审文档（第 2/2 段）开始 ==========\n" + seg2_main +
    u"\n========== 待审文档（第 2/2 段）结束 ==========\n"
)

for name, txt in [("grok_prompt_seg1.txt", seg1_prompt), ("grok_prompt_seg2.txt", seg2_prompt)]:
    p = os.path.join(OUT, name)
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(txt)
    print(name, "lines=", txt.count("\n")+1, "chars=", len(txt))

print("seg1_src_lines=1..205 ; seg2_cont=156..205 ; seg2_main=206..%d" % n)
