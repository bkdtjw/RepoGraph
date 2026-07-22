"""自测：retrieve.topic（BM25-lite 主题召回）+ context 四层瀑布（契约 A · L1/L2/L3）。

用真实 output/graph.json（multi-agent-orch，Module22/Class15/Function259/Commit75/
Concept139）验证"模糊语义"配合能力——没有具体符号名的问题也能落到真实代码证据：

  L0 符号优先  "修改 _handle_terminate 会影响哪些函数" → mode='symbol'（不被主题层截胡）
  L1 主题召回  "终止流程是怎么处理的" → mode='topic'，经 概念→IMPLEMENTS 落到 _handle_terminate
  L3 概览兜底  "了解这个代码库吗" / "介绍一下整个项目架构" → mode='overview'，含真实统计+顶层模块名
  L2 概念展开  expand_concepts(真实概念名) → mode='llm' 非空；不存在概念名被丢弃不崩（反幻觉）

外加 zh_terms / build_corpus_index / topic_recall 三个纯函数单测。全部断言基于对真实
图谱的真实检索，无假数据。

真实运行（不依赖 pytest / 第三方）：
    cd C:/Users/nirvana/Desktop/代码库知识图谱 && python tests/test_topic.py

测试措辞先经 grep 确认可达：图谱中多个"终止"类 Concept 经 IMPLEMENTS 反向落到真实函数
_handle_terminate（src/orch/scheduler/core.py），故"终止流程..."能沿概念桥接回该函数。
"""
import os
import sys

sys.path.insert(0, "src")

from repograph.models import GraphStore
from repograph.retrieve.topic import zh_terms, build_corpus_index, topic_recall
from repograph.retrieve.context import (
    build_repo_context, build_overview, expand_concepts,
)

_GRAPH = os.path.join(os.path.dirname(__file__), "..", "output", "graph.json")


def _load() -> GraphStore:
    assert os.path.exists(_GRAPH), f"缺少真实图谱 {_GRAPH}"
    return GraphStore.load(_GRAPH)


def _concepts_implementing(store: GraphStore, qualname: str):
    """返回 (函数 id 集合, 经 IMPLEMENTS 反向落到该函数的概念 id 集合)。

    IMPLEMENTS 边方向为 Function|Module→Concept，故"实现某函数的概念"= 以该函数为
    src 的边的 dst。用来在测试里确认"终止 → _handle_terminate"这条桥真实可达。
    """
    fids = {n["id"] for n in store.nodes("Function") if n.get("qualname") == qualname}
    assert fids, f"图谱中不存在函数 {qualname}"
    cids = set()
    for src, _t, dst, _p in store.edges("IMPLEMENTS"):
        if src in fids:
            cids.add(dst)
    return fids, cids


# ---------------------------------------------------------------------------
# 1) zh_terms：中文 2/3/4-gram 滑窗 + 英文词（小写、丢纯数字/单字符）
# ---------------------------------------------------------------------------

def test_zh_terms():
    t = zh_terms("终止流程")
    assert "终止" in t and "流程" in t            # 2-gram
    assert "终止流" in t and "止流程" in t          # 3-gram
    assert "终止流程" in t                          # 4-gram
    # 英文统一小写；长度<2 或纯数字丢弃
    te = zh_terms("Handle TERMINATE v2 3")
    assert "handle" in te and "terminate" in te and "v2" in te
    assert "3" not in te
    # 无可切词面
    assert zh_terms("") == []
    assert zh_terms("！！！。。。？？？") == []
    print("test_zh_terms OK")


# ---------------------------------------------------------------------------
# 2) build_corpus_index：236 篇微型文档、倒排 + DF，三类 label 齐备
# ---------------------------------------------------------------------------

def test_corpus_index(store):
    idx = build_corpus_index(store)
    # 语料 = 139 概念 + 75 提交 + 22 模块 + 127 带 zh_desc 的 Function/Class 双语卡片 = 363。
    # （C2/D-11 corpus 扩容：Function/Class 经 enrich 写入 zh_desc/zh_aliases 后入档；
    #  未富化的函数无 zh_desc 不入档。pre-C2 基线为 236，此处随语料扩容更新为 363。）
    assert idx.n_docs == 363, f"语料应为 363 篇（含 Function/Class 卡片），实为 {idx.n_docs}"
    assert idx.avgdl > 0
    # 高信息 term "终止"入倒排，DF 与真实语料自洽（≥ 5 个概念名含之）
    assert "终止" in idx.postings
    assert idx.df["终止"] == len(idx.postings["终止"]) >= 5
    # 五类文档 label 都被收录（C2/D-11：新增 Function/Class 双语卡片档）
    labels = {m["label"] for m in idx.meta.values()}
    assert labels == {"Concept", "Commit", "Module", "Function", "Class"}, labels
    print("test_corpus_index OK")


# ---------------------------------------------------------------------------
# 3) topic_recall：BM25 降序、结构完整、命中终止类概念；index 可复用；容错同义词缺口为空
# ---------------------------------------------------------------------------

def test_topic_recall(store):
    idx = build_corpus_index(store)
    res = topic_recall(store, "终止流程是怎么处理的", top_k=8, index=idx)
    assert res, "主题召回不应为空"
    r0 = res[0]
    assert set(r0.keys()) == {"node_id", "label", "name", "score", "matched_terms"}
    assert r0["score"] > 0 and r0["matched_terms"]
    # 分数降序
    scores = [r["score"] for r in res]
    assert scores == sorted(scores, reverse=True)
    # 召回集须含"实现 _handle_terminate 的终止类概念"之一（主题→代码桥的起点）
    _fids, term_cids = _concepts_implementing(store, "_handle_terminate")
    recalled = {r["node_id"] for r in res}
    assert recalled & term_cids, "召回应含实现 _handle_terminate 的终止类概念"
    # index 传入复用 与 现建 结果一致（确定性 & 可缓存）
    res2 = topic_recall(store, "终止流程是怎么处理的", top_k=8)
    assert [r["node_id"] for r in res2] == [r["node_id"] for r in res]
    # 词面追不到的同义词缺口（"容错"在概念里写作"恢复/兜底"）→ 确定性召回为空，
    # 这正是后端 L2 LLM 受限链接的触发条件，而非编造
    assert topic_recall(store, "怎么做容错的", index=idx) == []
    print("test_topic_recall OK")


# ---------------------------------------------------------------------------
# 4) L1 主题瀑布：mode='topic'，经 概念→IMPLEMENTS 落到真实 _handle_terminate
# ---------------------------------------------------------------------------

def test_waterfall_topic(store):
    ctx = build_repo_context(store, "终止流程是怎么处理的")
    assert ctx["mode"] == "topic", f"应走主题路径，实为 {ctx['mode']}"
    text = ctx["context_text"]
    assert text, "topic 模式 context_text 不应为空"
    # 关键：模糊问题最终落到真实函数 _handle_terminate（经 概念 IMPLEMENTS 反向）
    assert "_handle_terminate" in text, "主题上下文应展开到真实函数 _handle_terminate"
    assert "【命中主题概念】" in text
    assert "IMPLEMENTS" in text, "上下文须带 IMPLEMENTS 来源标注"
    # linked = 召回概念节点，带 score / matched_terms
    assert ctx["linked"], "topic 模式 linked 不应为空"
    assert any(l["label"] == "Concept" for l in ctx["linked"])
    top = ctx["linked"][0]
    assert "score" in top and top.get("matched_terms")
    # stats：主题命中数与概念展开数均 > 0
    assert ctx["stats"]["topics"] > 0 and ctx["stats"]["concepts"] > 0
    print("test_waterfall_topic OK")


# ---------------------------------------------------------------------------
# 5) L3 概览兜底：元问题 → mode='overview'，含真实统计与真实顶层模块名
# ---------------------------------------------------------------------------

def test_waterfall_overview(store):
    # v0.3 Phase C1：S1 五路路由器把元问题**精确分诊**——"了解这个代码库吗"→ route_label=meta
    # （注入 repo_card），"介绍一下整个项目架构"→ route_label=global（build_overview）。二者
    # 展示 mode 仍为 'overview'（spec §5.1：build_repo_context 恒返回 overview 类展示 mode，
    # meta/global 事件 mode 改写在网关侧据 route_label 完成），且仍逐字承载真实概览事实
    # （259/22/顶层模块路径/仓库名）。故断言：mode 仍 overview + route_label 精确 + 事实仍在。
    expected_route = {"了解这个代码库吗": "meta", "介绍一下整个项目架构": "global"}
    for q, want in expected_route.items():
        ctx = build_repo_context(store, q)
        assert ctx["mode"] == "overview", f"{q!r} 展示 mode 应为 overview，实为 {ctx['mode']}"
        assert ctx["route_label"] == want, f"{q!r} route_label 应为 {want}，实为 {ctx.get('route_label')}"
        text = ctx["context_text"]
        assert text, f"{want} 路由必须有真实概览/卡片文本"
        # 真实节点统计：259 个函数、22 个模块（meta 卡片与 global 概览均含规模事实行）
        assert "259" in text and "22" in text, "上下文须含真实节点统计"
        # 真实顶层模块名（loc 最大者）与仓库名
        assert "src/orch/cli/main.py" in text, "上下文须含真实顶层模块路径"
        assert "multi-agent-orch" in text
        assert ctx["linked"] == []
    # build_overview 直接调用自洽：统计数字来自真实图谱（本函数契约不变，仍 mode='overview'）
    ov = build_overview(store)
    assert ov["mode"] == "overview" and ov["linked"] == []
    assert ov["stats"]["concepts"] == 139
    assert ov["stats"]["functions"] == 259
    assert ov["stats"]["modules"] == 22
    print("test_waterfall_overview OK")


# ---------------------------------------------------------------------------
# 6) L0 符号优先：含符号名的问题必须走 symbol，不被主题层截胡（不回归）
# ---------------------------------------------------------------------------

def test_waterfall_symbol_priority(store):
    ctx = build_repo_context(store, "修改 _handle_terminate 会影响哪些函数")
    assert ctx["mode"] == "symbol", "含符号名的问题必须走符号路径"
    assert "_handle_terminate" in ctx["context_text"]
    assert ctx["stats"]["symbols"] > 0
    print("test_waterfall_symbol_priority OK")


# ---------------------------------------------------------------------------
# 7) L2 概念展开：真实概念名 → mode='llm' 非空；不存在概念名被丢弃不崩（反幻觉）
# ---------------------------------------------------------------------------

def test_expand_concepts(store):
    _fids, term_cids = _concepts_implementing(store, "_handle_terminate")
    assert term_cids, "应存在实现 _handle_terminate 的概念"
    real_name = store.get_node(sorted(term_cids)[0])["name"]

    ctx = expand_concepts(store, [real_name])
    assert ctx["mode"] == "llm", "expand_concepts 应返回 mode='llm'"
    assert ctx["context_text"], "真实概念名展开后上下文不应为空"
    # 该概念实现 _handle_terminate，展开应逐字含之
    assert "_handle_terminate" in ctx["context_text"]
    assert ctx["linked"] and ctx["linked"][0]["name"] == real_name
    assert ctx["stats"]["concepts"] >= 1

    # 反幻觉：全不存在的概念名被丢弃，不崩、上下文空、linked 空
    ghost = expand_concepts(store, ["这个概念根本不存在xyz", "", "另一个不存在"])
    assert ghost["mode"] == "llm"
    assert ghost["context_text"] == ""
    assert ghost["linked"] == []
    assert ghost["stats"]["concepts"] == 0

    # 真实名 + 幻觉名混合：只保留真实概念
    mixed = expand_concepts(store, ["这个不存在abc", real_name])
    assert len(mixed["linked"]) == 1 and mixed["linked"][0]["name"] == real_name
    print("test_expand_concepts OK")


if __name__ == "__main__":
    store = _load()
    test_zh_terms()
    test_corpus_index(store)
    test_topic_recall(store)
    test_waterfall_topic(store)
    test_waterfall_overview(store)
    test_waterfall_symbol_priority(store)
    test_expand_concepts(store)
    print("\nALL TESTS PASSED")
