# -*- coding: utf-8 -*-
"""Phase C 独立审查送审提示词构建器（可复现、可回溯）。

按 (段名, 源文件, 行段列表, 契约摘录) 生成带真实行号的送审 .txt；首行硬约束
「禁止使用任何工具、禁止读文件，只依据下方内嵌代码直接作答」。送审不含密钥。
"""
import os

RG = r"C:/Users/nirvana/Desktop/代码库知识图谱"
UI = r"C:/Users/nirvana/Desktop/claude-ui"
OUT = os.path.join(RG, "design_work", "review_c")
os.makedirs(OUT, exist_ok=True)

HEADER = (
    "禁止使用任何工具、禁止读文件，只依据下方内嵌代码直接作答。\n"
    "你是资深 Python/JS 代码审查员，审查 RepoGraph v0.3 Phase C 新增/改动代码。\n"
    "只按四个维度找**真实缺陷**，逐条给：文件:行号 + 一句问题 + 触发场景/输入 + 修法。\n"
    "没有缺陷就明说“未发现缺陷”。不要复述代码、不要风格建议、不要臆测未给出的代码。\n"
    "四维度：\n"
    " 1) 正确性：边界条件、异常吞掉后状态、并发（若涉及锁）、off-by-one、类型误判；\n"
    " 2) 契约一致：是否与下方【契约摘录】逐字段吻合（字段名/取值域/优先级/阈值）；\n"
    " 3) 假数据：任何写死的统计数字/标注/伪造的模型输出/占位真值（本项目铁律：无假数据）；\n"
    " 4) 密钥处理：鉴权 token 是否只入请求头、绝不打印/写文件/入异常消息（提及应为 sk-****）。\n"
)


def numbered(path, ranges):
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    out = []
    for (a, b) in ranges:
        for i in range(a, min(b, len(lines)) + 1):
            out.append(f"{i:>4}\t{lines[i-1].rstrip(chr(10))}")
        out.append("")
    return "\n".join(out)


SEGMENTS = [
    # name, file, ranges, contract
    ("A_router_route", os.path.join(RG, "src/repograph/retrieve/router.py"), [(1, 232)],
     "【契约 spec §4.2 路由器】五标签 meta/global/entity_local/structural/out_of_scope，按序首中即出。"
     "route(question, linked, topic_hits, has_code_token)->(label,rule_id|None)，纯函数确定性。"
     "no_repo_reference 是组合谓词=no_linker_hit ∧ topic全低 ∧ 无指代词 ∧ 无反引号（不是仅字面无“本项目”，"
     "否则“什么是适配层”这类仓库内概念会被 oos 误判界外）。全不中→(entity_local,None) 兜底。"
     "信号均为链接后信号。规则每条 {id,label,pattern(可选编译re),requires(可选信号名列表)}。"),

    ("B_router_banding", os.path.join(RG, "src/repograph/retrieve/router.py"), [(234, 432)],
     "【契约 spec §4.6 分带】方法档优先级 exact_qualname>suffix_qualname>short_name>concept_name>module_path>bm25_card(恒最低)。"
     "merge_link_candidates 按 entity_id 去重、方法档优先、同档 score 高者留、bm25_card 恒排方法档之后。"
     "过渡规则 D-N1：仅 exact/suffix(≥80) 可自动锚定给确定性工具；纯 bm25_card 永不自动锚定；"
     "matched_terms 提供时要求≥1内容词(非停用词)。disambiguate：强档领先(exact/suffix)→autopick；"
     "单一弱候选→autopick+degraded；弱档多候选整数分差≥δ_score(20)→autopick 否则 needs_disambiguation。"
     "card_hits_to_candidates 只收 Function/Class（Concept 走 topic 不在此重复）。"),

    ("C_router_premise", os.path.join(RG, "src/repograph/retrieve/router.py"), [(434, 549)],
     "【契约 spec §5.7/§3.2 S7 前提校验】premise 实体图谱不可词面定位→unknown_entity(保守当未证实)；"
     "可定位但断言支撑边缺失→本轮词面校验保守视为已获证据、不产 flag（missing_edge 半支留待边级校验，避免误伤真前提）。"
     "verify_premises(store,premises) 返回仅未证实项 [{claim,status:'unverified',reason,source,terms}]。"
     "两路来源：lexical(题面技术专名比对，gate 离线走这条) + llm(rewrite premises 抽 terms)。只据真实 store 扫描，绝不臆造。"),

    ("D_metrics_graph", os.path.join(RG, "src/repograph/metrics.py"), [(1, 215)],
     "【契约 spec §4.3 指标】fan_in=CALLS 入度(不同直接调用方)；heat=commits_all+2*commits_90d；"
     "90d 窗口基准=仓库最新提交日(确定性,非wall-clock)；churn_90d=窗口内 MODIFIES 边 lines_added+lines_deleted；"
     "blast_radius=反向 CALLS≤3跳闭包大小(不含自身)；fix_involvement=FIXES∩MODIFIES(本图无 FIXES 边→如实 0，禁填充)；"
     "pagerank=Function 级 CALLS 图幂迭代 d=0.85 阈1e-6 含悬挂质量项。只加/覆盖属性、图结构不动。"),

    ("E_metrics_cyclo", os.path.join(RG, "src/repograph/metrics.py"), [(217, 365)],
     "【契约 spec §4.3/D-03 圈复杂度+组装】cyclomatic：读源文件 span 重析 AST，按 symbol_id 回填；"
     "源码缺失/漂移如实记 missing_files、绝不臆造；--repo-root 缺省跳过 cyclomatic 其余照常。"
     "compute_all 返回 node_props+stats(可回溯统计)。write_metrics 幂等 merge。"
     "另附 ast_extractor 圈复杂度：count=1+决策点(If/For/While/ExceptHandler/IfExp/BoolOp额外操作数/推导式if/match_case)，嵌套 def/class 各自归属不下沉。"),

    ("F_llm_client", os.path.join(RG, "src/repograph/extract/llm_client.py"), [(1, 162)],
     "【契约 密钥安全铁律 + D-N4】token(anthropic_auth_token) 只读入内存放 Authorization 头，绝不打印/写文件/记日志/入异常消息(提及一律 sk-****)。"
     "配置读 claude-ui/config.json，缺文件/缺 base/缺 token→抛明确异常(不含 token)，绝不静默伪造。"
     "POST {base}/v1/messages 非流式，anthropic-version 头，限速 sleep 0.5s、重试 2 次，全失败抛 GatewayCallError(调用方降级不伪造)。"),

    ("G_repocard_det", os.path.join(RG, "src/repograph/retrieve/repo_card.py"), [(1, 205)],
     "【契约 spec §4.1 level-0 卡片】确定性字段纯图谱统计零网络：stats(模块/类/函数/提交/概念)、top_modules(按loc)、"
     "hot_functions(按MODIFIES)、core_concepts(按IMPLEMENTS)、entrypoints(聚合 is_endpoint,本图0则如实空)。"
     "render_card_text 恒含规模事实行(L0事实≥3)。禁写死统计数字，一切来自真实 store。"),

    ("H_repocard_summary", os.path.join(RG, "src/repograph/retrieve/repo_card.py"), [(207, 349)],
     "【契约 spec §4.1 summary 反幻觉 F2 + P4】summary 唯一一次索引期真实网关调用(≤300字)，"
     "经专名白名单校验(summary 英文标识符必须出现在输入中)，违规重试后降级弃 summary(返回None)，绝不伪造。"
     "查询期 meta 路由 load_or_build_repo_card：缓存优先，缺失/损坏→现场 build+degraded=True，绝不因缺文件裸拒。"
     "build_meta_context 返回 mode='overview' 展示态(route_label='meta' 承载精确五分类)。"),

    ("I_enrich", os.path.join(RG, "src/repograph/enrich.py"), [(1, 172)],
     "【契约 D-11/D-16 C2 双语回填】只加属性 zh_desc/zh_aliases，绝不动概念集/边集(节点/边大小不变，assert 守)。"
     "zh_desc/zh_aliases 是 C2 全权字段每次整体覆盖→幂等。blocked/缺失/结构不符→明确报错退出，绝不静默假数据。"
     "desc 仅 desc_accepted 才写。序列化 ensure_ascii=False indent=1 减 diff 噪声。"),

    ("J_context_route", os.path.join(RG, "src/repograph/retrieve/context.py"), [(243, 462)],
     "【契约 spec §5.1 schema v2 + §4.3 瀑布】build_repo_context 单出口挂 schema v2 纯增字段(v1兼容)："
     "route_label(五分类,§6.2判定基准)、route_source(rule:<id>|fallback:default)、premise_flags、needs_disambiguation、candidates、resolved_query。"
     "stats 恒含 {symbols,topics,impact_callers,commits,concepts}。meta/global/structural 展示 mode=overview，route_label 承载五分类。"
     "entity_local 四档瀑布 L0符号→L1主题→L3概览兜底(永不失联,裸拒率0)；allow_overview=False 回落 none。extra_queries 仅非空且无 linked 时启用(gate 离线不传)。"),

    ("K1_server_rewrite_premise", os.path.join(UI, "server.py"), [(560, 747)],
     "【契约 spec §4.4 改写 + §5.2 前提闸门】_rg_llm_rewrite 一次 flash 出 {queries(截2-4,中英各≥1),symbol_guesses(截5),premises(允许空)} 按问题 LRU 缓存(cap256)。"
     "_rg_try_rewrite_link：仅二次召回落到 symbol/topic/llm 才采信(overview/none=仍无锚交L2)；build_ctx 不支持 extra_queries→None 静默不启用。"
     "_rg_collect_premise_flags：lexical(build_repo_context已产)+llm(verify_premises 真实图谱校验)合并去重；网关缺失/异常静默降级仅 lexical。"
     "premise_flags 非空→_rg_premise_gate_text 注入固定 ⚠ 前缀要求先纠正后答。token 绝不回显；失败不抛不伪造。"),

    ("K2_server_event_focus", os.path.join(UI, "server.py"), [(749, 896)],
     "【契约 spec §4.5 焦点栈逐字段 + §5.1 事件】rg_focus=[{entity_id,label,turn}] 最多5条，"
     "turn=len(messages)快照，过期判定 当前turn−entry.turn>10 弹出(TTL=10)。强锚 method∈{exact_qualname,suffix_qualname} 才压栈。"
     "_rg_build_event：mode/linked/stats 恒在(v1)，其余纯增字段有值才附：route_label/route_source/confidence/resolved_query/needs_disambiguation/candidates/degraded/suggestions/premise_flags/focus_used。"
     "_rg_focus_peek 取最近未过期且类型相容；_rg_focus_push 去重+TTL剪枝+cap5 绝不抛。_rg_inject_prefix：meta/global/structural/overview/out_of_scope→概览前缀，topic/llm→主题前缀，其余→symbol前缀。_rg_normalize_mode：graph→symbol，未知→none。"),

    ("L_server_apichat", os.path.join(UI, "server.py"), [(1319, 1420), (1499, 1526)],
     "【契约 spec §4.5 消歧/焦点数据流 + 并发】读(检索前)：问题含指代词且无自身强锚→_rg_focus_peek 取最近类型相容焦点作种子并入检索重跑，命中回显解读。"
     "写(锚定后)：强锚压栈，就近挂在追加 assistant 的 write_session 之前(零额外落盘)，turn=cur_turn。"
     "非llm档焦点未命中→resolved_query 原样回显+suggestions 提示点名，禁止静默错锚(守P5)。"
     "并发：重检索/网关在 _RG_LOCK 之外；收尾 latest=read_session(sid) 重读避免并发覆盖，focus_push 改 latest。图谱注入绝不打断聊天(整块 try/except 降级)。"),

    ("M_appjs_a", os.path.join(UI, "web/app.js"), [(394, 587)],
     "【契约 spec §5.3 前端展示】renderRepoRef 按 mode 渲染 9 档；服务把 meta/global/structural 展示 mode 归一 overview，"
     "前端据 route_label 在 mode==='overview' 时还原细分(不触碰 symbol/topic/llm/none/out_of_scope)。"
     "全部字段可选、缺失=不渲染该块，绝不臆造数字/卡片；textContent 防注入。num() 只接受有限数字。"),

    ("N_appjs_b", os.path.join(UI, "web/app.js"), [(588, 736)],
     "【契约 spec §5.3 前端展示】premise_flags 非空→前提待核实逐条 claim(缺 claim 跳过)；"
     "needs_disambiguation=true 且有 candidates→候选卡片(path/doc_head/fan_in)，无 candidates 不渲染(不臆造)；"
     "degraded=true→低置信标记；suggestions→建议问法(P4出路)；resolved_query→系统理解回显(P5)。真实字段驱动，缺失优雅省略。"),
]


def main():
    manifest = []
    for name, path, ranges, contract in SEGMENTS:
        body = numbered(path, ranges)
        rng = ",".join(f"{a}-{b}" for a, b in ranges)
        src_rel = path.replace("\\", "/").split("Desktop/")[-1]
        text = (
            HEADER
            + "\n【被审文件】" + src_rel + "  行段 " + rng + "\n"
            + "\n" + contract + "\n"
            + "\n===== 内嵌代码（行号即源文件真实行号）=====\n"
            + body + "\n===== 代码结束 =====\n"
            + "\n请逐条列出真实缺陷（文件:行号 + 问题 + 触发场景 + 修法）；无则答“未发现缺陷”。\n"
        )
        fn = os.path.join(OUT, name + ".txt")
        with open(fn, "w", encoding="utf-8") as f:
            f.write(text)
        manifest.append((name, src_rel, rng, len(body.splitlines())))
    print("生成送审段：")
    for m in manifest:
        print(f"  {m[0]:22s} {m[1]:48s} 行段 {m[2]:15s} 代码 {m[3]} 行")
    # 密钥泄漏自检：送审文件绝不能含真实 token
    leak = []
    for name, *_ in [(s[0],) for s in SEGMENTS]:
        fn = os.path.join(OUT, name + ".txt")
        with open(fn, "r", encoding="utf-8") as f:
            t = f.read()
        if "sk-" in t and "sk-****" not in t.replace("sk-****", ""):
            # 仅当出现 sk- 且不是占位符
            import re
            for mm in re.findall(r"sk-[A-Za-z0-9\-_]{6,}", t):
                if mm != "sk-****":
                    leak.append((name, mm[:6] + "…"))
    print("密钥自检：", "干净（无真实 token）" if not leak else f"⚠ 疑似泄漏 {leak}")


if __name__ == "__main__":
    main()
