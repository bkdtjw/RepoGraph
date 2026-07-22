"""代码上下文装配（§7.3）——把自然语言问题落到真实图谱证据上。

契约 A：为 Claude UI 聊天提供"真配合、无假数据"的上下文源。本模块只做两件事：

1. ``link_entities``：从自然语言问题的**词面**匹配图谱里真实存在的符号
   （Function/Class 的 qualname、Module 的路径/点分名、Concept 的名称/别名），
   返回带打分与匹配方式的候选实体列表。中文问题里夹带的英文标识符与点分名
   会被抽取出来（如"改 _handle_terminate 会怎样" → 链接到 _handle_terminate）。

2. ``build_repo_context``：以命中的 Function 实体为锚，调用确定性的
   ``retrieve.impact.impact_analysis`` 求真实调用方闭包，并沿 MODIFIES / IMPLEMENTS
   / DESCRIBES 汇集相关提交与概念，拼成带 ``[来源]`` 标注的结构化中文上下文。
   所有内容均来自真实 ``graph.json`` 检索结果，不编造。

纯标准库；只依赖 ``..models.GraphStore`` / ``..models.dotted_from_relpath`` 与
``.impact.impact_analysis``，不与其它抽取模块互相 import。
"""
from __future__ import annotations

import re
from typing import Optional

from ..models import GraphStore, dotted_from_relpath
from .impact import impact_analysis
from .topic import topic_recall
from .router import (
    normalize, is_code_token, route, default_suggestions,
    merge_link_candidates, card_hits_to_candidates, has_strong_method,
    extract_lexical_premises, verify_premises, disambiguate, enrich_candidates,
)
from .repo_card import build_meta_context
from .lexicon import expand_abbreviations

# ---------------------------------------------------------------------------
# 打分（对应契约优先级：精确 qualname > 后缀 > 短名 > 概念名 > 模块）
# ---------------------------------------------------------------------------

_SCORE = {
    "exact_qualname": 100,
    "suffix_qualname": 80,
    "short_name": 60,
    "concept_name": 40,
    "module_path": 30,
}

# 影响面深度白名单（与 impact_analysis 保持一致）
_IMPACT_DEPTHS = (1, 2, 3, 4)

# 汇集材料的默认上限（避免上下文被单一维度撑爆；仍受 budget_chars 二次约束）
_MAX_COMMITS = 6
_MAX_CONCEPTS = 8

# 点分标识符链：形如 name / a.b.c / Store._begin / _handle_terminate。
# 以字母或下划线起头，中文标点与空白等一律作为分隔符，从而抽出中文里夹的英文标识符。
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")


# ---------------------------------------------------------------------------
# 词面切分：非标识符字符（含中文标点）切 token + 相邻 token 生成 1..3-gram
# ---------------------------------------------------------------------------

def _tokenize(question: str) -> set[str]:
    """从问题抽出候选词面：

    - 完整点分链本身（``orch.scheduler.core`` / ``Store._begin``）；
    - 点分链的逐级后缀（``a.b.c`` → ``b.c`` / ``c``）；
    - 相邻词（点分链拆段后按出现顺序展平）的 1..3-gram，分别用 ``_`` 与 ``.``
      连接，以命中"append system event"→``append_system_event`` 这类空格写法。
    """
    chains = _IDENT.findall(question or "")
    candidates: set[str] = set()
    words: list[str] = []
    for ch in chains:
        candidates.add(ch)
        segs = ch.split(".")
        words.extend(segs)
        for i in range(1, len(segs)):          # 逐级后缀：跳过完整链本身（i 从 1 起）
            candidates.add(".".join(segs[i:]))

    # 缩写双向扩展（落地设计 §4.4）：逐词把 ctx↔context / cfg↔config 等对侧形态补入候选，
    # 使"改 ctx 会怎样"命中 qualname 含 context 的符号。纯字符串、无语义（见 lexicon）。
    for w in list(words):
        for alt in expand_abbreviations(w):
            candidates.add(alt)
            words.append(alt)

    n_words = len(words)
    for n in (1, 2, 3):
        for i in range(0, n_words - n + 1):
            gram = words[i:i + n]
            candidates.add("_".join(gram))
            if n >= 2:
                candidates.add(".".join(gram))

    return {c for c in candidates if len(c) >= 2 and not c.isdigit()}


# ---------------------------------------------------------------------------
# link_entities —— 词面 → 真实图谱符号
# ---------------------------------------------------------------------------

def link_entities(store: GraphStore, question: str, top_k: int = 5) -> list[dict]:
    """把自然语言问题词面匹配到图谱中真实存在的符号。

    返回 ``[{entity_id, label, name, matched, score, method}]``，
    ``method ∈ {exact_qualname, suffix_qualname, short_name, module_path, concept_name}``。
    同一 entity 只保留最高分记录，按分数（并列时按 entity_id）排序取前 ``top_k``。
    未链接到任何符号返回空列表。
    """
    cands = _tokenize(question)
    if not cands:
        return []

    best: dict[str, dict] = {}

    def offer(entity_id: str, label: str, name: str,
              matched: str, method: str) -> None:
        score = _SCORE[method]
        cur = best.get(entity_id)
        # 同实体取最高分；分数并列时更长的匹配词面更具体，优先
        if cur is None or score > cur["score"] or (
            score == cur["score"] and len(matched) > len(cur["matched"])
        ):
            best[entity_id] = {
                "entity_id": entity_id,
                "label": label,
                "name": name,
                "matched": matched,
                "score": score,
                "method": method,
            }

    # Function / Class：qualname 全名（精确）、多段后缀、末段短名
    for node in store.nodes():
        label = node["label"]
        if label not in ("Function", "Class"):
            continue
        qn = node.get("qualname", "")
        if not qn:
            continue
        short = qn.rsplit(".", 1)[-1]
        for c in cands:
            if c == qn:
                offer(node["id"], label, qn, c, "exact_qualname")
            elif "." in c and qn.endswith("." + c):
                offer(node["id"], label, qn, c, "suffix_qualname")
            elif c == short:
                offer(node["id"], label, qn, c, "short_name")

    # Module：完整点分名、文件名、POSIX 路径、点分后缀
    for node in store.nodes("Module"):
        path = node.get("path", "")
        name = node.get("name", "")
        dotted = dotted_from_relpath(path) if path else ""
        display = dotted or name or path
        for c in cands:
            if c == dotted or c == path or (name and c == name) or (
                "." in c and dotted and dotted.endswith("." + c)
            ):
                offer(node["id"], "Module", display, c, "module_path")

    # Concept：名称 / 别名（自然语言，忽略大小写）
    for node in store.nodes("Concept"):
        name = node.get("name", "")
        targets = [name] + list(node.get("aliases") or [])
        low = {t.lower() for t in targets if t}
        for c in cands:
            if c.lower() in low:
                offer(node["id"], "Concept", name, c, "concept_name")

    # 中文别名包含匹配（v0.3 · C2 · D-16/§4.6）：C2 索引期回填的中文口语别名
    # （Concept.aliases / Function·Class.zh_aliases）多为无空格中文短语，_tokenize 只抽英文
    # 标识符、无法命中；此处对**规范化后的原问题**做**连续子串**匹配作补充。方法档 concept_name
    # （40，低于 exact/suffix），受过渡规则「仅方法档≥80 自动锚定」约束、不会自动锚给确定性工具。
    # 高精度护栏：别名长度≥3 且非停用词，避免短碎片误锚（FZ 口语问题不含连续正式别名，故不误伤 topic）。
    q_low = (question or "").lower()
    if q_low:
        _offer_zh_alias_matches(store, q_low, offer)

    ranked = sorted(best.values(), key=lambda r: (-r["score"], r["entity_id"]))
    return ranked[:top_k]


# 中文别名包含匹配的最短长度（防短碎片误锚；3 起可含"看门狗"这类三字机制名）
_ZH_ALIAS_MIN_LEN = 3


def _offer_zh_alias_matches(store: GraphStore, q_low: str, offer) -> None:
    """对 Concept.aliases / Function·Class.zh_aliases 做中文别名连续子串匹配（method=concept_name）。

    只收长度≥``_ZH_ALIAS_MIN_LEN`` 且非中文停用词的别名，作为原问题的连续子串命中即 offer。
    别名只进检索/链接候选、永不进答案事实（与 symbol_guesses 隔离同原则，见 §4.4 铁律辩护）。
    """
    from .lexicon import is_zh_stopword

    def _try(node_id: str, label: str, name: str, aliases) -> None:
        for a in aliases or []:
            al = (a or "").strip()
            if len(al) < _ZH_ALIAS_MIN_LEN or is_zh_stopword(al):
                continue
            if al.lower() in q_low:
                offer(node_id, label, name, al, "concept_name")

    for node in store.nodes("Concept"):
        merged = list(node.get("aliases") or []) + list(node.get("zh_aliases") or [])
        _try(node["id"], "Concept", node.get("name", ""), merged)
    for label in ("Function", "Class"):
        for node in store.nodes(label):
            _try(node["id"], label, node.get("qualname", ""), node.get("zh_aliases"))


# ---------------------------------------------------------------------------
# 文本拼装辅助
# ---------------------------------------------------------------------------

def _first_line(text: Optional[str]) -> str:
    if not text:
        return ""
    for line in text.splitlines():
        s = line.strip()
        if s:
            return s
    return ""


def _qn(store: GraphStore, node_id: str) -> str:
    """节点 id → 展示名（Function/Class 用 qualname，兜底用 id 末段）。"""
    node = store.get_node(node_id)
    if node is None:
        return node_id.rsplit("::", 1)[-1]
    return node.get("qualname") or node.get("name") or node_id.rsplit("::", 1)[-1]


def _short_path(module_id: str) -> str:
    """Module id（repo::path）→ path。"""
    return module_id.split("::", 1)[-1]


# ---------------------------------------------------------------------------
# build_repo_context —— 命中实体 + 影响面 + 提交 + 概念
# ---------------------------------------------------------------------------

def _finalize(ctx: dict, route_label: str, route_source: str,
              mode: Optional[str] = None, degraded: Optional[bool] = None,
              suggestions: Optional[list] = None) -> dict:
    """给上下文 dict 挂 schema v2 的路由回显字段（纯增字段，v1 消费方兼容）。

    ``route_label`` = S1 五分类（§6.2 路由准确率判定基准）；``route_source`` =
    ``rule:<id>`` / ``fallback:default``；``mode`` 传入则覆盖（global 路由把 overview
    改回显为 global）；``degraded``/``suggestions`` 按需附（S6 回退阶梯 / oos）。
    """
    ctx["route_label"] = route_label
    ctx["route_source"] = route_source
    if mode is not None:
        ctx["mode"] = mode
    if degraded is not None:
        ctx["degraded"] = degraded
    if suggestions is not None:
        ctx["suggestions"] = suggestions
    return ctx


def build_repo_context(
    store: GraphStore,
    question: str,
    budget_chars: int = 6000,
    impact_depth: int = 2,
    allow_overview: bool = True,
    extra_queries: Optional[list] = None,
) -> dict:
    """S1 五路路由器 + 四档瀑布装配上下文（契约 A 主入口，v0.3 Phase C1）。

    时序（落地设计 §4.2，消除 route↔linker 先后歧义）：``normalize`` → ``link_entities``
    + ``is_code_token``（+ 无链接时 ``topic_recall``，供 oos 组合谓词与 topic 档复用）→
    ``route()`` 分诊到 5 标签 → 分派（确定性、不碰网关）：

    - **meta**         → 注入 repo_card（``build_meta_context``），``mode='overview'``（route_label='meta' 承载五分类）；
    - **global**       → ``build_overview`` 顶层概览，``mode='global'``；
    - **entity_local** → 现四档瀑布（L0 符号→L1 主题→L2 LLM→L3 概览），``mode∈{symbol,topic,llm,overview}``；
    - **structural**   → 能定量者走 ``build_overview`` 字段，``route_label=structural`` + ``degraded``；
    - **out_of_scope** → 界外声明 + ≥1 建议问法 + ``degraded``，``mode='out_of_scope'``（P4 不裸拒）。

    统一返回 ``{mode, linked, context_text, stats, route_label, route_source[, degraded, suggestions]}``。
    ``stats`` 恒含 ``{symbols, topics, impact_callers, commits, concepts}`` 五键。规则全不中 →
    entity_local 兜底（离线确定性；网关侧 ``semantic_mode=='llm'`` 的 LLM 兜底分类非本函数职责）。

    向后兼容：``mode`` 语义按 spec §5.1 扩展 meta/global；旧 ``_is_meta_question`` 由 meta
    路由取代（函数保留不删）；symbol/topic/llm/overview/none 行为不变。
    """
    norm_q = normalize(question)
    linked = link_entities(store, norm_q, top_k=5)
    has_ct = is_code_token(norm_q)
    # topic_recall 仅在无链接时需要（symbol 档不经 topic；oos 组合谓词要求 no_linker_hit），
    # 且结果回灌 entity_local 的 topic 档避免二次计算。
    topic_hits = [] if linked else topic_recall(store, norm_q, top_k=8)

    # S2 改写二次召回（落地设计 §4.3/§4.4 / D-N2）：``extra_queries`` 由**网关侧** S2 改写
    # 产出并传入（entity_local 无主锚时的同义改述 query）；本函数纯确定性，只做二次 link/topic
    # 合并。**gate 离线永不传 extra_queries**（离线四层瀑布行为不变，S2 不进 gate 指标）。
    if extra_queries and not linked:
        linked, topic_hits = _augment_with_extra_queries(
            store, linked, topic_hits, extra_queries)

    label, rule_id = route(norm_q, linked, topic_hits, has_ct)
    src = f"rule:{rule_id}" if rule_id else "fallback:default"

    if label == "meta":
        ctx = _finalize(build_meta_context(store), "meta", src)
    elif label == "global":
        # 展示 mode 保持 overview（spec §5.1：build_overview 恒返回 overview，global 的事件
        # mode 改写在网关侧据 route_label 完成）；route_label='global' 承载精确五分类。
        ctx = _finalize(build_overview(store), "global", src)
    elif label == "structural":
        # 能确定性计数者走 build_overview 字段（模块/函数/概念总数等）；route_label 独立
        # 回显不被 overview 吞没（保 P2 可评测），degraded 标注模板层未建（落地设计 §4.2/F8）。
        ctx = _finalize(build_overview(store), "structural", src,
                        degraded=True, suggestions=default_suggestions())
    elif label == "out_of_scope":
        ctx = _build_oos_context(store, src)
    else:
        # entity_local（entity-1 规则或兜底）——现四档瀑布 + 消歧协议（C4），行为向后兼容
        ctx = _entity_local_waterfall(
            store, norm_q, linked, topic_hits, budget_chars, impact_depth,
            allow_overview, src)

    # 单出口挂 schema v2 纯增字段（v1 消费方兼容）：S7 前提校验 + 消歧/解读回显默认。
    return _attach_schema_v2(ctx, store, norm_q)


def _attach_schema_v2(ctx: dict, store: GraphStore, norm_q: str) -> dict:
    """给上下文挂 schema v2 纯增字段（落地设计 §5.1）——只加不覆盖已设者。

    - ``premise_flags``：S7 前提校验（``extract_lexical_premises`` → ``verify_premises``，
      lexical 规则，纯确定性；gate 离线判定读此字段，B-3 由此翻绿）；
    - ``needs_disambiguation`` / ``candidates`` / ``resolved_query``：消歧层（C4）未设时给默认，
      消歧层已设者（``entity_local`` 消歧）保留不覆盖。
    """
    if "premise_flags" not in ctx:
        ctx["premise_flags"] = verify_premises(store, extract_lexical_premises(norm_q))
    ctx.setdefault("needs_disambiguation", False)
    ctx.setdefault("candidates", [])
    ctx.setdefault("resolved_query", norm_q)
    return ctx


_MAX_EXTRA_QUERIES = 4


def _augment_with_extra_queries(
    store: GraphStore, linked: list[dict], topic_hits: list, extra_queries: list,
) -> tuple[list[dict], list]:
    """用 S2 改写 query 补链接/主题召回（无主锚时）：对每条改写 query 各跑一次
    ``link_entities`` / ``topic_recall``，按 entity_id / node_id 取并集去重、分数取高，
    重排取 Top-N。纯确定性、无网关（改写 query 由 server 侧网关产出后传入）。"""
    link_by_id = {c["entity_id"]: c for c in linked}
    topic_by_id = {r["node_id"]: r for r in topic_hits}
    for eq in (extra_queries or [])[:_MAX_EXTRA_QUERIES]:
        if not eq or not str(eq).strip():
            continue
        neq = normalize(str(eq))
        for c in link_entities(store, neq, top_k=5):
            cur = link_by_id.get(c["entity_id"])
            if cur is None or c["score"] > cur["score"]:
                link_by_id[c["entity_id"]] = c
        for r in topic_recall(store, neq, top_k=8):
            cur = topic_by_id.get(r["node_id"])
            if cur is None or r["score"] > cur["score"]:
                topic_by_id[r["node_id"]] = r
    merged_link = sorted(link_by_id.values(),
                         key=lambda c: (-c["score"], c["entity_id"]))[:5]
    merged_topic = sorted(topic_by_id.values(),
                          key=lambda r: (-r["score"], r["node_id"]))[:8]
    return merged_link, merged_topic


def _entity_local_waterfall(
    store: GraphStore, question: str, linked: list[dict], topic_hits: list,
    budget_chars: int, impact_depth: int, allow_overview: bool, src: str,
) -> dict:
    """entity_local 路由之下的现四档瀑布（L0 符号→L1 主题→L3 概览兜底），行为不变。

    S6 回退阶梯形式化（落地设计 §5.8 / D-05）：符号空且主题空 → ``build_overview``
    + ``degraded`` + 建议问法（低置信度）；``allow_overview=False`` 时回落 ``mode='none'``
    交后端 L2 决策（保持 v0.1 契约）。裸拒率 0 由 overview 恒兜底达成（P4）。

    分带（§4.6 / D-N1，C2 上线）：``candidates`` = merge_link_candidates(link ∪ bm25_card)
    作 schema v2 观测字段附上；**不改 symbol/topic 路由决策**（改路由会回归 AMB 短名档，
    实测证），过渡规则「仅方法档≥80 自动锚定」由 router.can_auto_anchor 在消歧层（C4）消费。
    """
    # 分带候选合并（观测用，不改路由）：link_entities ∪ bm25-over-卡片（Function/Class 召回）
    card_cands = card_hits_to_candidates(topic_hits)
    candidates = merge_link_candidates(linked, card_cands)

    # L0 符号 + S4 消歧协议（落地设计 §4.5/§4.6 / D-04）
    if linked:
        dis = disambiguate(linked)
        if dis["needs_disambiguation"]:
            # 多合法弱候选近并列 → 交上游澄清（附区分性 candidates，actionable 不裸拒）
            return _build_disambiguation_context(store, linked, question, src)
        # autopick：现符号档聚合上下文，回显解读（P5）+ 单一弱候选降级披露
        ctx = _build_symbol_context(store, linked, budget_chars, impact_depth)
        ctx["candidates"] = candidates
        ctx["needs_disambiguation"] = False
        ctx["resolved_query"] = dis.get("resolved_note") or question
        if dis.get("degraded"):
            ctx["degraded"] = True
        return _finalize(ctx, "entity_local", src)
    # L1 主题（复用已算的 topic_hits，避免二次召回）
    topic = _build_topic_context(store, question, budget_chars, recall=topic_hits)
    if topic is not None:
        topic["candidates"] = candidates
        return _finalize(topic, "entity_local", src)
    # L3 概览兜底（永不失联）+ S6 建议问法；allow_overview=False 回落 none
    if allow_overview:
        return _finalize(build_overview(store), "entity_local", src,
                         degraded=True, suggestions=default_suggestions())
    return _finalize({"mode": "none", "linked": [], "context_text": "", "stats": _stats()},
                     "entity_local", src, degraded=True)


def _build_disambiguation_context(store: GraphStore, linked: list[dict],
                                  question: str, src: str) -> dict:
    """S4 多合法弱候选 → needs_disambiguation + 区分性 candidates（path/doc_head/fan_in）交上游。

    context_text 列出候选（actionable，绝不裸拒 P4）；mode 保持 symbol（确有符号命中，仅歧义）。
    resolved_query 留待上游澄清后回填，本轮回显原问题 + 候选清单。
    """
    cands = enrich_candidates(store, linked)
    lines = ["【候选消歧】（来源: 词面链接多合法候选，需澄清）",
             f"「{question}」匹配到 {len(cands)} 个同名符号，请指明具体是哪一个："]
    for c in cands:
        eid = c.get("entity_id", "")
        lines.append(
            f"- {_qn(store, eid)}  （文件: {c.get('path', '?')}, "
            f"fan_in={c.get('fan_in', 0)}）{c.get('doc_head', '')}".rstrip())
    text = "\n".join(lines) + "\n"
    ctx = {
        "mode": "symbol",
        "linked": linked,
        "context_text": text,
        "stats": _stats(symbols=len(linked)),
        "needs_disambiguation": True,
        "candidates": cands,
        "degraded": False,
        "suggestions": [
            "可以追问：上面第 N 个（或直接点名 类名.方法名）",
        ],
    }
    return _finalize(ctx, "entity_local", src)


def _build_oos_context(store: GraphStore, src: str) -> dict:
    """out_of_scope 路由：界外声明 + ≥1 本仓库可答建议问法 + degraded（P4 绝不裸拒）。"""
    text = (
        "【超出仓库范围】（来源: 路由判定 out_of_scope）\n"
        "这个问题看起来不指向本代码库的具体内容。本系统基于代码知识图谱 RepoGraph，"
        "擅长回答与本仓库结构 / 实现 / 历史相关的问题。"
    )
    return _finalize(
        {"mode": "out_of_scope", "linked": [], "context_text": text, "stats": _stats()},
        "out_of_scope", src, degraded=True, suggestions=default_suggestions())


def _build_symbol_context(
    store: GraphStore,
    linked: list[dict],
    budget_chars: int = 6000,
    impact_depth: int = 2,
) -> dict:
    """L0 符号路径：以命中 Function 为锚求影响面，汇集相关提交与概念。

    流程：对命中的 Function 实体调 ``impact_analysis(calls)`` → 汇集①命中实体卡片
    ②影响面 ③相关提交(MODIFIES) ④相关概念(IMPLEMENTS/DESCRIBES)，按 ``budget_chars``
    以优先级 ①>②>③>④ 截断。返回 ``mode='symbol'``。
    """
    # 注意 True/False 是 int 子类且 True==1，必须显式排除 bool，否则会漏进 impact_analysis
    depth = impact_depth if (
        isinstance(impact_depth, int)
        and not isinstance(impact_depth, bool)
        and impact_depth in _IMPACT_DEPTHS
    ) else 2

    linked_func_ids = [r["entity_id"] for r in linked if r["label"] == "Function"]
    linked_func_set = set(linked_func_ids)

    # ---- ② 影响面：逐个命中 Function 调 impact_analysis，聚合调用方/端点/模块 ----
    impact_blocks: list[dict] = []
    all_callers: set[str] = set()
    for r in linked:
        if r["label"] != "Function":
            continue
        res = impact_analysis(store, r["name"], depth=depth, mode="calls")
        if "error" in res:
            continue
        # 仅在解析结果确实指向该命中实体时采信（qualname 唯一才成立），杜绝张冠李戴
        if res.get("resolved_symbol") != r["entity_id"]:
            continue
        direct = res.get("direct_callers", [])
        transitive = res.get("transitive_callers", [])
        all_callers.update(direct)
        all_callers.update(transitive)
        impact_blocks.append({
            "target": r["entity_id"],
            "direct": direct,
            "transitive": transitive,
            "endpoints": res.get("affected_endpoints", []),
            "modules": res.get("affected_modules", []),
            "truncated": res.get("truncated", False),
            "depth": res.get("depth", depth),
        })

    # ---- ③ 相关提交：经 MODIFIES 指向命中函数的 Commit，取最近若干条 ----
    commit_ids: set[str] = set()
    if linked_func_set:
        for src, _t, dst, _p in store.edges("MODIFIES"):
            if dst in linked_func_set:
                commit_ids.add(src)
    commits: list[dict] = []
    for cid in commit_ids:
        node = store.get_node(cid)
        if node is not None:
            commits.append(node)
    commits.sort(key=lambda n: (n.get("authored_at") or "", n["id"]), reverse=True)
    commits = commits[:_MAX_COMMITS]
    related_commit_ids = {c["id"] for c in commits}

    # ---- ④ 相关概念：命中函数 IMPLEMENTS 的 + 相关提交 DESCRIBES 的 + 直接命中的概念 ----
    concept_ids: list[str] = []
    seen_concepts: set[str] = set()

    def _add_concept(cid: str) -> None:
        if cid not in seen_concepts and store.get_node(cid) is not None:
            seen_concepts.add(cid)
            concept_ids.append(cid)

    for r in linked:
        if r["label"] == "Concept":
            _add_concept(r["entity_id"])
    for src, _t, dst, _p in store.edges("IMPLEMENTS"):
        if src in linked_func_set:
            _add_concept(dst)
    for src, _t, dst, _p in store.edges("DESCRIBES"):
        if src in related_commit_ids:
            _add_concept(dst)
    concept_ids = concept_ids[:_MAX_CONCEPTS]

    # ---- 逐区拼文本（优先级 ①>②>③>④，供 budget 截断）----
    lines: list[str] = []

    # ① 命中实体卡片
    lines.append("【命中实体】（来源: 图谱节点）")
    for r in linked:
        node = store.get_node(r["entity_id"])
        label = r["label"]
        if node is None:
            continue
        if label == "Function":
            lines.append(f"- [Function] {node.get('qualname', r['name'])}"
                         f"  （文件: {node.get('file', '?')}）")
            lines.append(f"    签名: {node.get('signature', '')}")
            doc = _first_line(node.get("docstring"))
            lines.append(f"    说明: {doc or '（无 docstring）'}")
        elif label == "Class":
            bases = ", ".join(node.get("bases") or []) or "（无）"
            lines.append(f"- [Class] {node.get('qualname', r['name'])}"
                         f"  （文件: {node.get('file', '?')}）")
            lines.append(f"    基类: {bases}")
            doc = _first_line(node.get("docstring"))
            lines.append(f"    说明: {doc or '（无 docstring）'}")
        elif label == "Module":
            lines.append(f"- [Module] {r['name']}"
                         f"  （路径: {node.get('path', '?')}, loc={node.get('loc', '?')}）")
            doc = _first_line(node.get("docstring"))
            lines.append(f"    说明: {doc or '（无 docstring）'}")
        elif label == "Concept":
            lines.append(f"- [Concept] {node.get('name', r['name'])}")
            desc = _first_line(node.get("description"))
            if desc:
                lines.append(f"    描述: {desc}")

    # ② 影响面
    if impact_blocks:
        lines.append("")
        lines.append(f"【影响面】（来源: CALLS 反向 BFS, depth={depth}）")
        for b in impact_blocks:
            lines.append(f"- 目标 {_qn(store, b['target'])}:")
            direct = ", ".join(_qn(store, c) for c in b["direct"]) or "（无）"
            lines.append(f"    直接调用方: {direct}")
            trans = ", ".join(_qn(store, c) for c in b["transitive"]) or "（无）"
            lines.append(f"    间接调用方: {trans}")
            eps = ", ".join(_qn(store, e) for e in b["endpoints"]) or "（无）"
            lines.append(f"    受影响端点: {eps}")
            mods = ", ".join(_short_path(m) for m in b["modules"]) or "（无）"
            lines.append(f"    受影响模块: {mods}")
            if b["truncated"]:
                lines.append(f"    注: 调用方闭包在 depth={b['depth']} 处截断，尚有更上游调用方")

    # ③ 相关提交
    if commits:
        lines.append("")
        lines.append("【相关提交】（来源: MODIFIES）")
        for c in commits:
            sha = (c.get("hash") or "")[:8]
            when = c.get("authored_at", "")
            msg = _first_line(c.get("message"))
            lines.append(f"- {sha} {when}: {msg}")

    # ④ 相关概念
    if concept_ids:
        lines.append("")
        lines.append("【相关概念】（来源: 命中概念 / IMPLEMENTS / DESCRIBES）")
        for cid in concept_ids:
            node = store.get_node(cid)
            if node is None:
                continue
            name = node.get("name", "")
            desc = _first_line(node.get("description"))
            lines.append(f"- {name}: {desc}")
            evidence = node.get("evidence") or []
            if evidence:
                quote = _first_line((evidence[0] or {}).get("quote"))
                if quote:
                    lines.append(f"    证据: \"{quote}\"")

    # ---- budget 截断（保高优先级）----
    context_text, ctx_truncated = _apply_budget(lines, budget_chars)

    stats = _stats(
        symbols=len(linked),
        impact_callers=len(all_callers),
        commits=len(commits),
        concepts=len(concept_ids),
    )
    return {
        "mode": "symbol",
        "linked": linked,
        "context_text": context_text,
        "stats": stats,
    }


def _apply_budget(lines: list[str], budget_chars: int) -> tuple[str, bool]:
    """按行累加到 budget_chars 为止；超预算则截断并追加提示。"""
    out: list[str] = []
    total = 0
    truncated = False
    for ln in lines:
        piece = ln + "\n"
        if total + len(piece) > budget_chars:
            truncated = True
            break
        out.append(piece)
        total += len(piece)
    text = "".join(out)
    if truncated:
        text += "…（上下文因预算截断，仅保留高优先级内容）"
    return text, truncated


# ---------------------------------------------------------------------------
# 统一 stats / 元问题识别 / 仓库名
# ---------------------------------------------------------------------------

def _stats(**kw) -> dict:
    """统一 stats 骨架：恒含五键，按 mode 覆盖有意义者（可附加额外键）。"""
    base = {"symbols": 0, "topics": 0, "impact_callers": 0, "commits": 0, "concepts": 0}
    base.update(kw)
    return base


# 元问题标记：问"整体/架构/了解代码库"这类没有具体落点的问题，直接给概览
_META_MARKERS = (
    "了解", "熟悉", "认识", "介绍", "简介", "概览", "概述", "总览",
    "整体", "整个项目", "整个代码", "项目架构", "项目结构", "代码库",
    "这个项目", "这项目", "这个仓库", "这仓库", "架构", "什么项目",
    "干什么的", "是做什么", "做什么的", "有哪些模块", "整个仓库",
)


def _is_meta_question(question: str) -> bool:
    """问题是否为"了解/介绍/整体/架构/这项目"等元问题（子串命中即真）。"""
    q = question or ""
    return any(m in q for m in _META_MARKERS)


def _repo_name(store: GraphStore) -> str:
    """从任一带仓库前缀的节点 id 取仓库名（Concept 的 concept:: 前缀不含仓库，跳过）。"""
    for node in store.nodes():
        nid = node.get("id", "")
        if "::" in nid and not nid.startswith("concept::"):
            return nid.split("::", 1)[0]
    return "(unknown)"


def _label_of(store: GraphStore, node_id: str) -> str:
    node = store.get_node(node_id)
    return node["label"] if node else ""


# ---------------------------------------------------------------------------
# 概念展开核心 —— L1 主题 与 L2 LLM 共用（概念卡片 + IMPLEMENTS 实现 + DESCRIBES 提交）
# ---------------------------------------------------------------------------

_MAX_IMPL_PER_CONCEPT = 8      # 每个概念最多展示的实现函数/模块数
_MAX_DESC_PER_CONCEPT = 4      # 每个概念最多展示的 DESCRIBES 提交数


def _assemble_concept_context(
    store: GraphStore,
    concept_nodes: list[dict],
    budget_chars: int,
    mode: str,
    linked: list[dict],
    recall_meta: Optional[dict] = None,
    extra_commit_hits: Optional[list[dict]] = None,
    extra_module_hits: Optional[list[dict]] = None,
    extra_symbol_hits: Optional[list[dict]] = None,
) -> dict:
    """把一组真实 Concept 节点沿 IMPLEMENTS/DESCRIBES 反向展开成结构化上下文。

    - IMPLEMENTS 反向（概念←函数/模块）→ 实现该概念的真实函数签名 / 模块路径；
    - DESCRIBES 反向（概念←提交）→ 描述该概念的真实提交；
    - ``extra_commit_hits`` / ``extra_module_hits``：主题召回里直接命中的提交/模块
      （非经概念，L1 专用），作为独立证据区附上。

    ``mode`` 决定标签（``topic`` / ``llm``），``recall_meta`` 提供概念的召回分与命中词。
    全部内容来自真实图谱边与节点，按 ``budget_chars`` 截断。
    """
    recall_meta = recall_meta or {}
    concept_ids = [n["id"] for n in concept_nodes]
    cset = set(concept_ids)

    # 反向邻接：概念 → 实现者 / 描述提交（各扫一遍边）
    impl_by: dict[str, list[str]] = {cid: [] for cid in concept_ids}
    desc_by: dict[str, list[str]] = {cid: [] for cid in concept_ids}
    if cset:
        for src, _t, dst, _p in store.edges("IMPLEMENTS"):
            if dst in cset:
                impl_by[dst].append(src)
        for src, _t, dst, _p in store.edges("DESCRIBES"):
            if dst in cset:
                desc_by[dst].append(src)

    all_commit_ids: set[str] = set()
    lines: list[str] = []

    if concept_nodes:
        lines.append("【命中主题概念】（来源: 主题召回 / IMPLEMENTS·DESCRIBES 展开）")
        for node in concept_nodes:
            cid = node["id"]
            head = f"- [Concept] {node.get('name', '')}"
            meta = recall_meta.get(cid)
            if meta:
                mt = "/".join(meta.get("matched_terms", [])[:6])
                head += f"  (相关度 {meta.get('score')}, 命中词: {mt})"
            lines.append(head)
            desc = _first_line(node.get("description"))
            if desc:
                lines.append(f"    描述: {desc}")

            # 实现（IMPLEMENTS 反向）：函数在前（带签名）、模块在后，确定性排序
            impls = set(impl_by.get(cid, []))
            fn_ids = sorted((i for i in impls if _label_of(store, i) == "Function"),
                            key=lambda i: (_qn(store, i), i))
            mod_ids = sorted((i for i in impls if _label_of(store, i) == "Module"),
                             key=lambda i: (_short_path(i), i))
            shown = (fn_ids + mod_ids)[:_MAX_IMPL_PER_CONCEPT]
            if shown:
                lines.append("    实现（IMPLEMENTS 反向）:")
                for iid in shown:
                    n2 = store.get_node(iid)
                    if n2 is None:
                        continue
                    if n2["label"] == "Function":
                        lines.append(
                            f"      · [Function] {n2.get('qualname', '')}"
                            f"  签名: {n2.get('signature', '')}"
                            f"  （文件: {n2.get('file', '?')}）")
                    else:
                        lines.append(f"      · [Module] {n2.get('path', '?')}")

            # 相关提交（DESCRIBES 反向）：按时间倒序取近若干
            cnodes = [store.get_node(c) for c in set(desc_by.get(cid, []))]
            cnodes = [c for c in cnodes if c is not None]
            cnodes.sort(key=lambda c: (c.get("authored_at") or "", c["id"]), reverse=True)
            cnodes = cnodes[:_MAX_DESC_PER_CONCEPT]
            if cnodes:
                lines.append("    相关提交（DESCRIBES）:")
                for c in cnodes:
                    all_commit_ids.add(c["id"])
                    sha = (c.get("hash") or "")[:8]
                    lines.append(
                        f"      · {sha} {c.get('authored_at', '')}: "
                        f"{_first_line(c.get('message'))}")

    # L1 专用：主题召回直接命中的提交（非经概念）
    for r in extra_commit_hits or []:
        c = store.get_node(r["node_id"])
        if c is None:
            continue
        if not any(ln.startswith("【主题直接命中的提交】") for ln in lines):
            lines.append("")
            lines.append("【主题直接命中的提交】（来源: 主题召回 · Commit message）")
        all_commit_ids.add(c["id"])
        sha = (c.get("hash") or "")[:8]
        mt = "/".join(r.get("matched_terms", [])[:6])
        lines.append(f"- {sha} {c.get('authored_at', '')}: "
                     f"{_first_line(c.get('message'))}  (命中词: {mt})")

    # L1 专用：主题召回直接命中的模块（非经概念）
    for r in extra_module_hits or []:
        m = store.get_node(r["node_id"])
        if m is None:
            continue
        if not any(ln.startswith("【主题直接命中的模块】") for ln in lines):
            lines.append("")
            lines.append("【主题直接命中的模块】（来源: 主题召回 · Module docstring）")
        lines.append(f"- {m.get('path', '?')}: {_first_line(m.get('docstring'))}")

    # C2 新增：主题召回直接命中的函数/类（经 zh_desc/zh_aliases 双语卡片入 BM25 语料召回）
    for r in extra_symbol_hits or []:
        n = store.get_node(r["node_id"])
        if n is None:
            continue
        if not any(ln.startswith("【主题直接命中的符号】") for ln in lines):
            lines.append("")
            lines.append("【主题直接命中的符号】（来源: 主题召回 · 双语卡片 zh_desc/zh_aliases）")
        mt = "/".join(r.get("matched_terms", [])[:6])
        zh = n.get("zh_desc") or _first_line(n.get("docstring")) or ""
        lines.append(f"- [{n['label']}] {n.get('qualname', '')}"
                     f"  （文件: {n.get('file', '?')}）  {zh}  (命中词: {mt})")

    context_text, _tr = _apply_budget(lines, budget_chars)
    stats = _stats(
        topics=len(linked),
        concepts=len(concept_nodes),
        commits=len(all_commit_ids),
    )
    return {"mode": mode, "linked": linked, "context_text": context_text, "stats": stats}


# ---------------------------------------------------------------------------
# L1 主题上下文（BM25 召回 → 概念展开）
# ---------------------------------------------------------------------------

def _build_topic_context(
    store: GraphStore, question: str, budget_chars: int,
    recall: Optional[list] = None,
) -> Optional[dict]:
    """无符号命中时的主题召回路径。返回 ``mode='topic'`` 的上下文；召回空返回 ``None``。

    ``recall`` 传入则复用（``build_repo_context`` 已在 route 前算过，避免二次召回）；
    传入空列表等价于「召回为空」→ 返回 ``None``（与现建空召回同义）。
    """
    if recall is None:
        recall = topic_recall(store, question, top_k=8)
    if not recall:
        return None

    concept_nodes: list[dict] = []
    recall_meta: dict[str, dict] = {}
    commit_hits: list[dict] = []
    module_hits: list[dict] = []
    symbol_hits: list[dict] = []
    for r in recall:
        if r["label"] == "Concept":
            node = store.get_node(r["node_id"])
            if node is not None:
                concept_nodes.append(node)
                recall_meta[node["id"]] = {
                    "score": r["score"], "matched_terms": r["matched_terms"]}
        elif r["label"] == "Commit":
            commit_hits.append(r)
        elif r["label"] == "Module":
            module_hits.append(r)
        elif r["label"] in ("Function", "Class"):
            symbol_hits.append(r)

    return _assemble_concept_context(
        store, concept_nodes, budget_chars, mode="topic",
        linked=recall, recall_meta=recall_meta,
        extra_commit_hits=commit_hits, extra_module_hits=module_hits,
        extra_symbol_hits=symbol_hits,
    )


# ---------------------------------------------------------------------------
# L2 LLM 受限链接的展开端（纯函数，后端把 LLM 选中的概念名传进来）
# ---------------------------------------------------------------------------

def expand_concepts(
    store: GraphStore, concept_names: list[str], budget_chars: int = 6000
) -> dict:
    """给定概念名列表 → 规范化匹配到真实 Concept 节点后展开（供后端 L2）。

    反幻觉：只保留能在图谱里按 name/alias（忽略大小写、去空白）匹配到的概念，
    不存在的名字一律丢弃。沿 IMPLEMENTS/DESCRIBES 展开到真实函数与提交，
    返回 ``mode='llm'``。给全不存在的名字则 ``context_text=''``、``stats`` 归零，不崩。
    """
    by_name: dict[str, dict] = {}
    for node in store.nodes("Concept"):
        nm = (node.get("name") or "").strip().lower()
        if nm:
            by_name.setdefault(nm, node)
        for a in node.get("aliases") or []:
            al = (a or "").strip().lower()
            if al:
                by_name.setdefault(al, node)

    wanted: list[dict] = []
    seen: set[str] = set()
    for raw in concept_names or []:
        if not raw:
            continue
        node = by_name.get(str(raw).strip().lower())
        if node is not None and node["id"] not in seen:
            seen.add(node["id"])
            wanted.append(node)

    linked = [{"node_id": n["id"], "label": "Concept", "name": n.get("name", "")}
              for n in wanted]
    return _assemble_concept_context(store, wanted, budget_chars, mode="llm",
                                     linked=linked)


# ---------------------------------------------------------------------------
# L3 概览兜底（永不失联）—— 全部来自真实图谱统计
# ---------------------------------------------------------------------------

_OVERVIEW_TOP_MODULES = 8
_OVERVIEW_TOP_HOT = 5
_OVERVIEW_TOP_CONCEPTS = 8


def build_overview(store: GraphStore) -> dict:
    """真实图谱概览：仓库名 + 节点统计 + 顶层模块 + 热点函数 + 核心概念。

    彻底消灭"我不了解你的代码库"。所有数字与名称均来自对图谱的真实统计，不编造：
    顶层模块按 ``loc`` 降序、热点函数按 MODIFIES 计数、核心概念按 IMPLEMENTS 落点数。
    返回 ``mode='overview'``、``linked=[]``。
    """
    counts = store.counts().get("nodes", {})
    n_mod = counts.get("Module", 0)
    n_cls = counts.get("Class", 0)
    n_fn = counts.get("Function", 0)
    n_commit = counts.get("Commit", 0)
    n_concept = counts.get("Concept", 0)

    lines: list[str] = []
    lines.append("【仓库概览】（来源: 图谱统计）")
    lines.append(f"仓库: {_repo_name(store)}")
    lines.append(f"规模: 模块 {n_mod} · 类 {n_cls} · 函数 {n_fn} · "
                 f"提交 {n_commit} · 概念 {n_concept}")

    # 顶层模块：按 loc 降序 top N，列 path + docstring 首行
    mods = sorted(store.nodes("Module"),
                  key=lambda m: (-(m.get("loc") or 0), m.get("path", "")))
    mods = mods[:_OVERVIEW_TOP_MODULES]
    if mods:
        lines.append("")
        lines.append("【顶层模块】（来源: Module 按 loc 降序）")
        for m in mods:
            doc = _first_line(m.get("docstring"))
            lines.append(f"- {m.get('path', '?')} (loc={m.get('loc', '?')}): "
                         f"{doc or '（无 docstring）'}")

    # 热点函数：被 MODIFIES 指向最多的 top N
    mod_ct: dict[str, int] = {}
    for _s, _t, dst, _p in store.edges("MODIFIES"):
        mod_ct[dst] = mod_ct.get(dst, 0) + 1
    hot = sorted(mod_ct.items(), key=lambda kv: (-kv[1], kv[0]))
    hot = [(fid, c) for fid, c in hot if store.get_node(fid) is not None][:_OVERVIEW_TOP_HOT]
    if hot:
        lines.append("")
        lines.append("【热点函数】（来源: MODIFIES 计数，改动最频繁）")
        for fid, c in hot:
            lines.append(f"- {_qn(store, fid)} (被 {c} 次提交修改)")

    # 核心概念：IMPLEMENTS 落点最多的 top N
    impl_ct: dict[str, int] = {}
    for _s, _t, dst, _p in store.edges("IMPLEMENTS"):
        impl_ct[dst] = impl_ct.get(dst, 0) + 1
    core = sorted(impl_ct.items(), key=lambda kv: (-kv[1], kv[0]))
    core = [(cid, c) for cid, c in core if store.get_node(cid) is not None][:_OVERVIEW_TOP_CONCEPTS]
    if core:
        lines.append("")
        lines.append("【核心概念】（来源: IMPLEMENTS 落点最多）")
        for cid, c in core:
            node = store.get_node(cid)
            lines.append(f"- {node.get('name', '')} (落 {c} 处实现): "
                         f"{_first_line(node.get('description'))}")

    context_text, _tr = _apply_budget(lines, 6000)
    stats = _stats(concepts=n_concept, commits=n_commit,
                   modules=n_mod, functions=n_fn, classes=n_cls)
    return {"mode": "overview", "linked": [], "context_text": context_text, "stats": stats}
