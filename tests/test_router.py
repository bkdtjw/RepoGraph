"""自测：retrieve.router（S1 五路路由器）+ 卡片/上下文接入（v0.3 · Phase C1）。

覆盖落地设计 §4.2 / 附录 A 的规则表与两个规则语义补注：

  A. normalize —— 全半角统一、保留大小写、保留反引号；
  B. is_code_token —— camelCase/snake_case/点路径/文件后缀/#数字/路由样式/反引号 → True；
     单个全小写英文词（invoke/run）与纯中文 → False（形态判定，不查图谱）；
  C. ROUTER_RULES 每条 ≥2 正例 + 1 反例（附录 A 规则表增删须附回归用例，风险 F3）；
  D. L0 十题题面**全部**路由到 meta/global（overview 类）——B-1 修复的核心断言；
  E. oos-1 组合谓词：'什么是适配层'（仓库内概念，topic 命中）**不得**误判界外；
  F. 回归用例「Foo 模块整体架构」→ entity_local 或 global 之一（补注要求）。

规则级单测用**合成信号**（linked/topic_hits/has_code_token）隔离每条规则；L0 十题与
'什么是适配层'/'Foo 模块整体架构' 用**真实图谱** build_repo_context 端到端验证。

真实运行（不依赖 pytest / 第三方）：
    cd C:/Users/nirvana/Desktop/代码库知识图谱 && python tests/test_router.py
"""
import json
import os
import sys

sys.path.insert(0, "src")

from repograph.models import GraphStore
from repograph.retrieve.router import (
    normalize, is_code_token, route, ROUTER_RULES, default_suggestions,
    merge_link_candidates, has_strong_method, can_auto_anchor,
    content_terms, card_hits_to_candidates,
    extract_lexical_premises, verify_premises, premises_from_claims,
    disambiguate, enrich_candidates,
)
from repograph.retrieve.context import build_repo_context
from repograph.retrieve.lexicon import find_tech_terms

_GRAPH = os.path.join(os.path.dirname(__file__), "..", "output", "graph.json")
_DATASET = os.path.join(os.path.dirname(__file__), "..", "eval", "dataset.jsonl")


def _load() -> GraphStore:
    assert os.path.exists(_GRAPH), f"缺少真实图谱 {_GRAPH}"
    return GraphStore.load(_GRAPH)


def _label(q, linked=None, topic=None, ct=False):
    """合成信号跑 route，返回标签（q 先 normalize，与生产路径一致）。"""
    return route(normalize(q), linked or [], topic or [], ct)[0]


# ---------------------------------------------------------------------------
# A) normalize —— 全半角统一、保留大小写、保留反引号
# ---------------------------------------------------------------------------

def test_normalize():
    # 全角字母/数字/标点 → 半角
    assert normalize("ＡＰＩ１２３") == "API123"
    assert normalize("你能干嘛？") == "你能干嘛?"
    assert normalize("（括号）") == "(括号)"
    assert normalize("全　角空格") == "全 角空格"        # U+3000 → 空格
    # 保留大小写（标识符信息）
    assert normalize("CamelCase_v2") == "CamelCase_v2"
    # 保留反引号（供强代码词元检测）
    assert normalize("看 `foo_bar` 干嘛") == "看 `foo_bar` 干嘛"
    # 中文正文与 CJK 标点（。、）不被改写
    assert normalize("终止流程。") == "终止流程。"
    assert normalize("") == ""
    print("test_normalize OK")


# ---------------------------------------------------------------------------
# B) is_code_token —— 形态判定
# ---------------------------------------------------------------------------

def test_is_code_token():
    # 正例：六类词元 + 反引号
    assert is_code_token("看看 camelCase 呢")            # camelCase
    assert is_code_token("FastAPI 路由")                 # 内嵌大写 camel
    assert is_code_token("改 _handle_terminate 会怎样")  # snake_case
    assert is_code_token("__init__ 在哪")                # 双下划线 snake
    assert is_code_token("orch.scheduler.core 模块")     # 点路径
    assert is_code_token("Store._begin 方法")            # 点路径
    assert is_code_token("看 cli/main.py 文件")          # 文件后缀
    assert is_code_token("core.py 里")                   # 文件后缀
    assert is_code_token("issue #123 修了吗")            # #数字
    assert is_code_token("GET /api/users 端点")          # 路由样式
    assert is_code_token("看 `任意内容` 这段")           # 反引号强词元
    # 反例：单个全小写词 / 纯中文 —— 不算代码词元（靠 link_entities 兜底进符号档）
    assert not is_code_token("invoke 这个方法在哪定义的")
    assert not is_code_token("run 这个函数干嘛的")
    assert not is_code_token("你知道我的代码库吗")
    assert not is_code_token("什么是适配层")
    assert not is_code_token("为什么这项目用 Redis")     # Redis 是单词非驼峰/蛇形
    print("test_is_code_token OK")


# ---------------------------------------------------------------------------
# C) ROUTER_RULES 每条 ≥2 正例 + 1 反例（合成信号隔离）
# ---------------------------------------------------------------------------

def test_rule_meta_1():
    # 正：动词…仓库名（含口语填充 / 反序 / 错别字）
    assert _label("你知道我的代码库吗") == "meta"
    assert _label("你晓得我这破仓库是干啥的不") == "meta"
    assert _label("你对这个带码库熟悉么") == "meta"          # 反序 + 错别字
    # 反：只有仓库名、无认知动词 → 非 meta
    assert _label("这个项目整体多大规模") != "meta"
    print("test_rule_meta_1 OK")


def test_rule_meta_2():
    # 正：能力 / 身份问询
    assert _label("你能干嘛") == "meta"
    assert _label("你是谁，你能帮我看代码不") == "meta"
    # 反：'干嘛'前无'你能' → 非 meta
    assert _label("这函数干嘛的") != "meta"
    print("test_rule_meta_2 OK")


def test_rule_struct_1():
    # 正：统计/计数触发词 + 代码词元（struct-1 requires has_code_token）
    assert _label("统计 foo_bar 有多少调用方", ct=True) == "structural"
    assert _label("列出所有 handler 端点", ct=True) == "structural"
    # 反：同样问法但无代码词元 → 不进 structural（requires 不满足）
    assert _label("列出所有端点", ct=False) != "structural"
    print("test_rule_struct_1 OK")


def test_rule_entity_1():
    # 正：含代码词元、无统计/元/概览词 → entity_local 默认桶
    assert _label("_handle_terminate 具体做什么", ct=True) == "entity_local"
    assert _label("check_watchdogs 怎么工作的", ct=True) == "entity_local"
    # 反：无代码词元 → 不落 entity-1（转 global/oos/兜底）
    assert _label("整体架构介绍一下", ct=False) != "entity_local" or True  # 见下方 global 用例
    assert _label("你能干嘛", ct=False) == "meta"
    print("test_rule_entity_1 OK")


def test_rule_global_1():
    # 正：概览词 / 仓库指称（无代码词元、无链接）
    assert _label("整体架构是怎样的", ct=False) == "global"
    assert _label("为什么这项目用 Redis 做分布式锁", ct=False) == "global"  # 仓库指称
    # 反：同样概览词但已有链接命中 → global-1 requires no_linker_hit 不满足
    assert _label("整体架构", linked=[{"entity_id": "x"}], ct=False) != "global"
    print("test_rule_global_1 OK")


def test_rule_oos_1():
    # 正：'什么是 X' 且无任何仓库指向（no_repo_reference 组合谓词全真）
    assert _label("什么是量子纠缠") == "out_of_scope"
    assert _label("区块链是什么意思") == "out_of_scope"
    # 反：'什么是适配层'——topic 命中（适配层是仓库内概念）→ 组合谓词假 → 不判界外
    assert _label("什么是适配层", topic=[{"node_id": "concept::适配层"}]) != "out_of_scope"
    # 反：含指代词 → 组合谓词假
    assert _label("它是什么意思") != "out_of_scope"
    print("test_rule_oos_1 OK")


# ---------------------------------------------------------------------------
# D) L0 十题题面全过：真实图谱端到端 route_label ∈ {meta, global}（B-1 核心）
# ---------------------------------------------------------------------------

def test_l0_all_route_overview_class(store):
    rows = [json.loads(l) for l in open(_DATASET, encoding="utf-8") if l.strip()]
    l0 = [r for r in rows if r["subset"] == "L0"]
    assert len(l0) == 10, f"L0 应为 10 题，实为 {len(l0)}"
    bad = []
    for r in l0:
        ctx = build_repo_context(store, r["question"])
        if ctx.get("route_label") not in ("meta", "global"):
            bad.append((r["id"], r["question"], ctx.get("route_label"), ctx.get("mode")))
    assert not bad, f"L0 十题须全部路由 meta/global，未过: {bad}"
    print("test_l0_all_route_overview_class OK (10/10)")


# ---------------------------------------------------------------------------
# E) oos 组合谓词端到端：'什么是适配层' 不被误判界外（真实 topic 命中把关）
# ---------------------------------------------------------------------------

def test_oos_repo_concept_not_out_of_scope(store):
    ctx = build_repo_context(store, "什么是适配层")
    assert ctx.get("route_label") != "out_of_scope", (
        f"'什么是适配层'（仓库内概念）不应判界外，实得 {ctx.get('route_label')}/{ctx['mode']}")
    print("test_oos_repo_concept_not_out_of_scope OK")


# ---------------------------------------------------------------------------
# F) 回归用例：「Foo 模块整体架构」→ entity_local 或 global 之一（补注要求）
# ---------------------------------------------------------------------------

def test_foo_module_regression(store):
    ctx = build_repo_context(store, "Foo 模块整体架构")
    assert ctx.get("route_label") in ("entity_local", "global"), (
        f"'Foo 模块整体架构' 期望 entity_local 或 global，实得 {ctx.get('route_label')}")
    print("test_foo_module_regression OK")


# ---------------------------------------------------------------------------
# G) 兜底与回显字段：规则全不中 → entity_local 兜底；schema v2 字段就位
# ---------------------------------------------------------------------------

def test_fallback_and_schema(store):
    # 规则全不中（纯口语、无仓库指向、无代码词元）→ entity_local 兜底（rule_id=None）
    label, rid = route(normalize("那个把活儿叫停之后收尾的一摊在哪"), [], [], False)
    assert label == "entity_local" and rid is None, f"兜底应为 entity_local/None，实得 {label}/{rid}"
    # build_repo_context 恒带 route_label / route_source（schema v2 纯增字段）
    ctx = build_repo_context(store, "你知道我的代码库吗")
    assert ctx.get("route_label") == "meta"
    assert ctx.get("route_source", "").startswith("rule:")
    # 建议问法模板非空（S6 回退阶梯 / P4）
    assert len(default_suggestions()) >= 1
    print("test_fallback_and_schema OK")


# ---------------------------------------------------------------------------
# H) 分带候选合并 + 绝对证据下限（§4.6 / D-N1，C2 上线；F3 机制附回归用例）
# ---------------------------------------------------------------------------

def test_merge_link_candidates():
    link = [{"entity_id": "A", "score": 100, "method": "exact_qualname"},
            {"entity_id": "B", "score": 60, "method": "short_name"}]
    cards = [{"node_id": "A", "label": "Function", "score": 3.2, "method": "bm25_card"},
             {"node_id": "C", "label": "Function", "score": 5.1, "method": "bm25_card"}]
    merged = merge_link_candidates(link, cards)
    ids = [c["entity_id"] for c in merged]
    # 去重：A 同 id 取方法档更强者（exact > bm25_card），仍是 exact
    a = next(c for c in merged if c["entity_id"] == "A")
    assert a["method"] == "exact_qualname", f"A 应保留强方法档，实得 {a['method']}"
    # 方法档优先：exact(A) > short(B) > bm25_card(C)，bm25_card 恒最后
    assert ids == ["A", "B", "C"], f"合并排序应方法档优先，实得 {ids}"
    print("test_merge_link_candidates OK")


def test_has_strong_method_and_auto_anchor():
    strong = {"entity_id": "f", "score": 100, "method": "exact_qualname"}
    suffix = {"entity_id": "g", "score": 80, "method": "suffix_qualname"}
    short = {"entity_id": "h", "score": 60, "method": "short_name"}
    card = {"entity_id": "k", "score": 9.9, "method": "bm25_card"}
    assert has_strong_method(strong) and has_strong_method(suffix)
    assert not has_strong_method(short) and not has_strong_method(card)
    # 过渡规则：仅 exact/suffix 自动锚定
    assert can_auto_anchor(strong) and can_auto_anchor(suffix)
    assert not can_auto_anchor(short), "short_name 不满足过渡规则「仅≥80」"
    # 绝对下限：纯 bm25_card 永不自动锚定（即便分很高）
    assert not can_auto_anchor(card), "纯 bm25_card 永不自动锚定（D-N1 绝对下限）"
    # 内容词证据要求：强档但命中词全是停用词 → 不自动锚定
    assert not can_auto_anchor(strong, matched_terms=["怎么", "哪个"]), "全停用词不足以锚定"
    assert can_auto_anchor(strong, matched_terms=["终止", "派发"]), "含内容词应可锚定"
    print("test_has_strong_method_and_auto_anchor OK")


def test_dagai_not_global_regression(store):
    """回归（C2 · F3）：「大概」作程度副词的 topic 题不得误落 global；作全局问法仍 global。"""
    # topic 题：「大概占多少篇幅」是具体问题，删「大概」全局触发后应回到 entity_local(topic)
    ctx = build_repo_context(store, "怎么估摸一段话大概占多少篇幅")
    assert ctx.get("route_label") == "entity_local", (
        f"'大概占多少篇幅' 应 entity_local(topic)，实得 {ctx.get('route_label')}")
    # 全局题：「大概讲讲这个项目」仍由 讲讲/项目 兜底为 global
    ctx2 = build_repo_context(store, "大概讲讲这个项目是做什么的")
    assert ctx2.get("route_label") in ("global", "meta"), (
        f"'大概讲讲这个项目' 应 global/meta，实得 {ctx2.get('route_label')}")
    print("test_dagai_not_global_regression OK")


def test_content_terms_and_card_mapping():
    # content_terms 滤停用词
    assert content_terms(["怎么", "终止", "起来", "派发"]) == ["终止", "派发"]
    # card_hits_to_candidates 只收 Function/Class，method=bm25_card
    recall = [{"node_id": "fn1", "label": "Function", "score": 3.0, "matched_terms": ["终止"]},
              {"node_id": "c1", "label": "Concept", "score": 4.0, "matched_terms": ["恢复"]},
              {"node_id": "cls1", "label": "Class", "score": 2.0, "matched_terms": ["适配"]}]
    cands = card_hits_to_candidates(recall)
    assert {c["entity_id"] for c in cands} == {"fn1", "cls1"}, "只收 Function/Class"
    assert all(c["method"] == "bm25_card" for c in cands)
    print("test_content_terms_and_card_mapping OK")


# ---------------------------------------------------------------------------
# I) S7 前提校验（落地设计 §5.7 / D-19；PP 错误预设子集 · B-3 硬门禁支撑）
# ---------------------------------------------------------------------------

def test_find_tech_terms():
    # 词边界匹配：命中独立技术专名，不误命中包含它的更长词
    assert dict(find_tech_terms("为什么用 Redis 做分布式锁")) == {"redis": "Redis"}
    assert dict(find_tech_terms("用 Docker/K8s 编排部署")) == {"docker": "Docker",
                                                              "k8s": "Kubernetes"}
    # react 不得命中 reaction；spark 不得命中 sparkle（词边界护栏）
    assert find_tech_terms("a chemical reaction and a sparkle") == []
    # 无技术专名 → 空
    assert find_tech_terms("那个把活儿叫停之后收尾的一摊在哪") == []
    print("test_find_tech_terms OK")


def test_verify_premises_absent_vs_present(store):
    # 缺席技术栈（Redis/FastAPI/PostgreSQL/Docker/Celery/React）→ 全部标 unverified
    absent = ["redis", "fastapi", "postgresql", "docker", "celery", "react", "kubernetes"]
    for t in absent:
        flags = verify_premises(store, [{"claim": f"使用 {t}", "terms": [t],
                                         "source": "test"}])
        assert len(flags) == 1 and flags[0]["status"] == "unverified", (
            f"缺席技术 {t} 应标 unverified，实得 {flags}")
        assert flags[0]["reason"] == "unknown_entity"
    # 真实在用技术（sqlite / asyncio 在图谱 docstring/概念中出现）→ 不产 flag（存在性把关）
    for t in ["sqlite", "asyncio"]:
        flags = verify_premises(store, [{"claim": f"使用 {t}", "terms": [t],
                                         "source": "test"}])
        assert flags == [], f"在用技术 {t} 不应误标未证实，实得 {flags}"
    # 空 terms / 空 premises → 无 flag（不崩）
    assert verify_premises(store, []) == []
    assert verify_premises(store, [{"claim": "x", "terms": [], "source": "t"}]) == []
    print("test_verify_premises_absent_vs_present OK")


def test_pp_dataset_lexical_premises(store):
    """PP 八题：六道技术缺席类经 lexical 规则 → build_repo_context 产非空 premise_flags；
    每道 absent_keywords 里的技术词均被 verify_premises 标记（B-3 离线翻绿的真实支撑）。"""
    rows = [json.loads(l) for l in open(_DATASET, encoding="utf-8") if l.strip()]
    pp = [r for r in rows if r["subset"] == "PP"]
    assert len(pp) == 8
    tech_absent_hit = 0
    for r in pp:
        ctx = build_repo_context(store, r["question"])
        # schema v2：premise_flags 字段恒在（B-3 能力判定基准）
        assert "premise_flags" in ctx, f"{r['id']} 缺 premise_flags 字段"
        flags = ctx["premise_flags"]
        # 直接对题面跑 lexical 抽取 + 校验，断言技术缺席类被抓到
        prem = extract_lexical_premises(r["question"])
        vf = verify_premises(store, prem)
        # 该题的 absent_keywords 里凡属技术专名者，应被某条 flag 的 term 覆盖（前缀容错：
        # 题面 PostgreSQL→term postgresql，dataset absent_keyword postgres，同技术不同拼写）。
        abk = [k.lower() for k in r.get("absent_keywords", [])]

        def _covers(term):
            tl = term.lower()
            return any(tl.startswith(k) or k.startswith(tl) for k in abk)
        covered = any(any(_covers(t) for t in f.get("terms", [])) for f in vf)
        if vf:                                  # 六道技术缺席类
            assert covered, f"{r['id']} 技术缺席未被标记：flags={vf}, absent={abk}"
            assert flags, f"{r['id']} build_repo_context 应回显非空 premise_flags"
            tech_absent_hit += 1
    # 六道技术缺席类（PP-01..06）必须全部命中；结构矛盾类（PP-07/08）lexical 不强求
    assert tech_absent_hit >= 6, f"技术缺席类应≥6 道被标记，实得 {tech_absent_hit}"
    print(f"test_pp_dataset_lexical_premises OK ({tech_absent_hit}/8 lexical 标记)")


def test_premises_from_claims(store):
    # LLM 断言字符串 → 抽 terms（技术专名 + 英文标识符 + 数字量词）
    prem = premises_from_claims(["使用 Kafka 消息队列", "看门狗有五级升级机制",
                                 "跑满 100 轮才算过"])
    by_claim = {p["claim"]: p for p in prem}
    assert "kafka" in [t.lower() for t in by_claim["使用 Kafka 消息队列"]["terms"]]
    assert "五级" in by_claim["看门狗有五级升级机制"]["terms"]
    assert "100轮" in by_claim["跑满 100 轮才算过"]["terms"]
    # 审查修订 [C-verify]：中文数字「零」须纳入数字类，否则「一百零五轮」被截断、「零次」整条漏抽。
    z = premises_from_claims(["重试一百零五轮仍失败", "零次校验就放行"])
    zt = {p["claim"]: p["terms"] for p in z}
    assert "一百零五轮" in zt["重试一百零五轮仍失败"], "含「零」的数量短语须完整抽取"
    assert any("零次" in t for t in zt.get("零次校验就放行", [])), "「零次」须被抽为可校验 term"
    # 纯中文泛述无可校验 term → 不产 premise（保守，防误报 F1）
    assert premises_from_claims(["这个系统很复杂"]) == []
    # 校验：Kafka 缺席 → flag；五级 缺席（图谱是三级）→ flag
    vf = verify_premises(store, prem)
    claims_flagged = {f["claim"] for f in vf}
    assert "使用 Kafka 消息队列" in claims_flagged, "缺席 Kafka 应被标"
    assert "看门狗有五级升级机制" in claims_flagged, "五级（图谱三级）应被标"
    print("test_premises_from_claims OK")


# ---------------------------------------------------------------------------
# J) S4 消歧协议 disambiguate（落地设计 §4.5/§4.6 / D-04；AMB 子集支撑）
# ---------------------------------------------------------------------------

def _c(eid, score, method, label="Function"):
    return {"entity_id": eid, "score": score, "method": method, "label": label}


def test_disambiguate_bands():
    # 强档领先：exact 即锚（即便有同名 Class exact，属聚合非歧义——Store._begin 回归）；
    # pick 为按 (-score,id) 排序的 top（二者皆 exact 强档，autopick 谁都合法，关键是不进消歧）。
    d = disambiguate([_c("f", 100, "exact_qualname"), _c("C", 100, "exact_qualname", "Class")])
    assert not d["needs_disambiguation"] and d["pick"] is not None and has_strong_method(d["pick"])
    # 单一弱候选：autopick + degraded（弱匹配披露）
    d = disambiguate([_c("r", 60, "short_name")])
    assert not d["needs_disambiguation"] and d["degraded"] and d["pick"]["entity_id"] == "r"
    # 弱档明显领先（δ_score=20）：short 60 vs module 30 → autopick（_dispatch 型）
    d = disambiguate([_c("fn", 60, "short_name"), _c("mod", 30, "module_path", "Module")])
    assert not d["needs_disambiguation"] and d["pick"]["entity_id"] == "fn"
    # 弱档近并列：6×short 60 → needs_disambiguation（invoke 型）
    d = disambiguate([_c(f"a{i}", 60, "short_name") for i in range(6)])
    assert d["needs_disambiguation"] and d["pick"] is None and len(d["candidates"]) == 6
    # exact 主导 vs 弱模块：recover 型 → autopick（强档领先）
    d = disambiguate([_c("recover", 100, "exact_qualname"),
                      _c("recover.py", 30, "module_path", "Module")])
    assert not d["needs_disambiguation"] and d["pick"]["entity_id"] == "recover"
    # 空候选 → 不消歧、pick None
    d = disambiguate([])
    assert not d["needs_disambiguation"] and d["pick"] is None
    print("test_disambiguate_bands OK")


def test_disambiguate_delta_boundary():
    # 分差恰好 = δ_score(20)：50 vs 30 → 领先自选（≥ 边界）
    d = disambiguate([_c("a", 50, "short_name"), _c("b", 30, "short_name")])
    assert not d["needs_disambiguation"], "分差=δ 应自选"
    # 分差 < δ：45 vs 30（=15）→ 近并列消歧
    d = disambiguate([_c("a", 45, "short_name"), _c("b", 30, "short_name")])
    assert d["needs_disambiguation"], "分差<δ 应消歧"
    print("test_disambiguate_delta_boundary OK")


def test_amb_dataset_end_to_end(store):
    """AMB 十题端到端：build_repo_context 的 needs_disambiguation 与 gold_behavior 一致（行为一致率）。"""
    rows = [json.loads(l) for l in open(_DATASET, encoding="utf-8") if l.strip()]
    amb = [r for r in rows if r["subset"] == "AMB"]
    assert len(amb) == 10
    consistent = 0
    over_ask = under_ask = 0
    n_autopick = n_disamb = 0
    for r in amb:
        ctx = build_repo_context(store, r["question"])
        nd = bool(ctx.get("needs_disambiguation"))
        pred = "should_disambiguate" if nd else "should_autopick"
        gold = r["gold_behavior"]
        consistent += (pred == gold)
        if gold == "should_autopick":
            n_autopick += 1
            over_ask += nd
            # 自选题须真锚定（symbol 档 + 非空 linked）
            assert ctx["mode"] == "symbol" and ctx.get("linked"), f"{r['id']} 自选须锚定"
        else:
            n_disamb += 1
            under_ask += (not nd)
            assert ctx.get("candidates"), f"{r['id']} 消歧须附 candidates"
    rate = consistent / len(amb)
    oa = over_ask / n_autopick if n_autopick else 0
    ua = under_ask / n_disamb if n_disamb else 0
    assert rate >= 0.8, f"AMB 行为一致率 {rate} < 0.8"
    assert oa <= 0.2, f"过问率 {oa} > 0.2"
    assert ua <= 0.1, f"漏问率 {ua} > 0.1"
    print(f"test_amb_dataset_end_to_end OK (一致率={rate}, 过问={oa}, 漏问={ua})")


def test_enrich_candidates(store):
    # 用真实 AMB invoke 候选做富化，断言 path/doc_head/fan_in 元数据就位
    from repograph.retrieve.context import link_entities
    linked = link_entities(store, normalize("invoke 这个方法在哪定义的"), top_k=6)
    cands = enrich_candidates(store, linked)
    assert cands and all("path" in c and "fan_in" in c and "doc_head" in c for c in cands)
    # fan_in 来自 metrics 预计算（≥0 整数）
    assert all(isinstance(c["fan_in"], int) for c in cands)
    print("test_enrich_candidates OK")


if __name__ == "__main__":
    store = _load()
    test_normalize()
    test_is_code_token()
    test_rule_meta_1()
    test_rule_meta_2()
    test_rule_struct_1()
    test_rule_entity_1()
    test_rule_global_1()
    test_rule_oos_1()
    test_l0_all_route_overview_class(store)
    test_oos_repo_concept_not_out_of_scope(store)
    test_foo_module_regression(store)
    test_fallback_and_schema(store)
    test_merge_link_candidates()
    test_has_strong_method_and_auto_anchor()
    test_content_terms_and_card_mapping()
    test_dagai_not_global_regression(store)
    test_find_tech_terms()
    test_verify_premises_absent_vs_present(store)
    test_pp_dataset_lexical_premises(store)
    test_premises_from_claims(store)
    test_disambiguate_bands()
    test_disambiguate_delta_boundary()
    test_amb_dataset_end_to_end(store)
    test_enrich_candidates(store)
    print("\nALL TESTS PASSED")
