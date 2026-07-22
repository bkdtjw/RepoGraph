"""自测：retrieve.context.link_entities / build_repo_context（契约 A）。

用真实 output/graph.json（multi-agent-orch 图谱，510 节点 / 1698 边）验证：
词面链接命中真实符号、build_repo_context 返回 mode='symbol' 且上下文含**真实**调用方
qualname、stats 数字与 impact_analysis 自洽、乱码问题在 allow_overview=False 时回落
mode='none'（默认则回落 mode='overview'，永不失联）。

注：四层瀑布落地后 L0 符号路径的 mode 由旧值 'graph' 改名为 'symbol'，stats 统一新增
'topics' 键；主题/概览/LLM 三路的断言见 test_topic.py。

真实运行（不依赖 pytest / 第三方）：
    cd C:/Users/nirvana/Desktop/代码库知识图谱 && python tests/test_context.py

测试用符号均先经 grep graph.json 确认真实存在：
    _handle_terminate / _render_for_dispatch / Store._begin / append_system_event
    _make_handler.Handler._send_json（3 段 qualname，用于 suffix_qualname）
    orch.scheduler.core（Module 点分名，用于 module_path）
"""
import os
import sys

sys.path.insert(0, "src")

from repograph.models import GraphStore
from repograph.retrieve.impact import impact_analysis
from repograph.retrieve.context import link_entities, build_repo_context, _tokenize

_GRAPH = os.path.join(os.path.dirname(__file__), "..", "output", "graph.json")


def _load() -> GraphStore:
    assert os.path.exists(_GRAPH), f"缺少真实图谱 {_GRAPH}"
    return GraphStore.load(_GRAPH)


def _find(linked, *, endswith=None, method=None, label=None):
    """在 link_entities 结果里找符合条件的记录。"""
    for r in linked:
        if endswith is not None and not r["entity_id"].endswith(endswith):
            continue
        if method is not None and r["method"] != method:
            continue
        if label is not None and r["label"] != label:
            continue
        return r
    return None


# ---------------------------------------------------------------------------
# 1) 词面切分：中文里夹的英文标识符与点分名要能抽出来
# ---------------------------------------------------------------------------

def test_tokenize():
    assert "_handle_terminate" in _tokenize("如果我改动 _handle_terminate 会怎样？")
    toks = _tokenize("Store._begin 这个方法")
    assert "Store._begin" in toks and "_begin" in toks
    # 空格写法能拼回下划线标识符
    assert "append_system_event" in _tokenize("append system event 发生了什么")
    # 纯中文（无英文标识符）不产出可匹配候选
    assert _tokenize("今天天气不错适合出门散步呀") == set()
    print("test_tokenize OK")


# ---------------------------------------------------------------------------
# 2) link_entities：精确 qualname（中文夹带英文标识符）
# ---------------------------------------------------------------------------

def test_link_exact_qualname(store):
    linked = link_entities(store, "如果我改动 _handle_terminate 会有什么影响？")
    hit = _find(linked, endswith="::_handle_terminate",
                method="exact_qualname", label="Function")
    assert hit is not None, f"未精确命中 _handle_terminate: {linked}"
    assert hit["name"] == "_handle_terminate"
    assert hit["matched"] == "_handle_terminate"
    assert hit["score"] == 100
    print("test_link_exact_qualname OK")


# ---------------------------------------------------------------------------
# 3) link_entities：点分名 + 短名 + 后缀 三种方式
# ---------------------------------------------------------------------------

def test_link_dotted_short_suffix(store):
    # 点分全名 Store._begin → exact_qualname；同时把 Class Store 也精确命中
    linked = link_entities(store, "Store._begin 这个方法是干什么的")
    fn = _find(linked, endswith="::Store._begin", method="exact_qualname",
               label="Function")
    assert fn is not None, f"未精确命中 Store._begin: {linked}"
    cls = _find(linked, endswith="::Store", method="exact_qualname", label="Class")
    assert cls is not None, "问题含 Store 应同时精确命中 Class Store"

    # 仅给短名 _begin → short_name（图中 _begin 唯一）
    linked2 = link_entities(store, "改一下 _begin 方法")
    sh = _find(linked2, endswith="::Store._begin", method="short_name")
    assert sh is not None, f"短名 _begin 应命中 Store._begin: {linked2}"
    assert sh["matched"] == "_begin"

    # 多段后缀 Handler._send_json → suffix_qualname（真实 qualname 为
    # _make_handler.Handler._send_json，3 段）
    linked3 = link_entities(store, "Handler._send_json 会不会有问题")
    su = _find(linked3, endswith="::_make_handler.Handler._send_json",
               method="suffix_qualname")
    assert su is not None, f"后缀 Handler._send_json 应命中: {linked3}"
    assert su["matched"] == "Handler._send_json"
    # 打分顺序：精确 > 后缀 > 短名
    assert su["score"] < 100
    print("test_link_dotted_short_suffix OK")


# ---------------------------------------------------------------------------
# 4) link_entities：Module 点分名 → module_path；top_k 去重与截断
# ---------------------------------------------------------------------------

def test_link_module_and_topk(store):
    linked = link_entities(store, "orch.scheduler.core", top_k=10)
    mod = _find(linked, endswith="::src/orch/scheduler/core.py",
                method="module_path", label="Module")
    assert mod is not None, f"点分名应命中 Module orch.scheduler.core: {linked}"
    assert mod["name"] == "orch.scheduler.core"

    # top_k 生效：同实体只保留一条、结果不超过 top_k
    small = link_entities(store, "Store._begin append_system_event _handle_terminate",
                          top_k=2)
    assert len(small) <= 2
    ids = [r["entity_id"] for r in small]
    assert len(ids) == len(set(ids)), "同一 entity 不应重复出现"
    # 分数降序
    scores = [r["score"] for r in small]
    assert scores == sorted(scores, reverse=True)
    print("test_link_module_and_topk OK")


# ---------------------------------------------------------------------------
# 5) build_repo_context：mode='graph'，上下文含真实调用方 qualname，stats 自洽
# ---------------------------------------------------------------------------

def test_build_context_graph(store):
    q = "改 _handle_terminate 会影响哪些调用方"
    ctx = build_repo_context(store, q, budget_chars=6000, impact_depth=2)

    assert ctx["mode"] == "symbol"
    assert ctx["linked"], "linked 不应为空"
    assert ctx["linked"][0]["method"] == "exact_qualname"

    text = ctx["context_text"]
    assert text, "graph 模式 context_text 不应为空"
    # 分区标题 + 来源标注
    assert "【命中实体】" in text
    assert "【影响面】" in text
    assert "【相关提交】" in text
    assert "来源" in text, "上下文必须带 [来源] 标注"

    # 真实调用方 qualname 必须逐字出现（来自 impact_analysis，非编造）
    res = impact_analysis(store, "_handle_terminate", depth=2, mode="calls")
    assert "error" not in res
    direct_qns = [store.get_node(c)["qualname"] for c in res["direct_callers"]]
    transitive_qns = [store.get_node(c)["qualname"] for c in res["transitive_callers"]]
    assert direct_qns, "该符号应有真实直接调用方"
    for qn in direct_qns:
        assert qn in text, f"上下文缺失真实直接调用方 {qn}"
    for qn in transitive_qns:
        assert qn in text, f"上下文缺失真实间接调用方 {qn}"
    # 抽查一个众所周知的真实调用方
    assert "_dispatch_group" in text and "run_thread" in text

    # stats 全部 > 0，且 impact_callers 与 impact_analysis 完全自洽
    st = ctx["stats"]
    assert st["symbols"] > 0 and st["impact_callers"] > 0
    assert st["commits"] > 0 and st["concepts"] > 0
    expected_callers = len(set(res["direct_callers"]) | set(res["transitive_callers"]))
    assert st["impact_callers"] == expected_callers, (
        f"impact_callers={st['impact_callers']} 应等于真实闭包调用方数 {expected_callers}")
    print("test_build_context_graph OK")


# ---------------------------------------------------------------------------
# 6) build_repo_context：多命中聚合（Store._begin，含 Class + Function）
# ---------------------------------------------------------------------------

def test_build_context_aggregate(store):
    ctx = build_repo_context(store, "Store._begin 有哪些调用方", impact_depth=2)
    assert ctx["mode"] == "symbol"
    text = ctx["context_text"]
    # 真实直接调用方（Store 内部众多方法）应出现
    assert "Store.append_event" in text
    assert "Store.upsert_session" in text

    # impact_callers 等于 Store._begin 闭包调用方的真实数量
    res = impact_analysis(store, "Store._begin", depth=2, mode="calls")
    expected = len(set(res["direct_callers"]) | set(res["transitive_callers"]))
    assert ctx["stats"]["impact_callers"] == expected
    # symbols 计命中实体数（此问同时命中 Class Store 与 Function Store._begin）
    assert ctx["stats"]["symbols"] == len(ctx["linked"]) >= 2
    print("test_build_context_aggregate OK")


# ---------------------------------------------------------------------------
# 7) build_repo_context：相关提交与概念来自真实边
# ---------------------------------------------------------------------------

def test_build_context_commits_concepts(store):
    ctx = build_repo_context(store, "append_system_event 的提交与概念")
    assert ctx["mode"] == "symbol"
    fid = ctx["linked"][0]["entity_id"]

    # 相关提交：真实 MODIFIES → 命中函数
    real_commits = {s for s, _t, d, _p in store.edges("MODIFIES") if d == fid}
    assert ctx["stats"]["commits"] > 0
    assert ctx["stats"]["commits"] <= len(real_commits)
    # 上下文里的提交 sha 前缀确来自真实提交
    text = ctx["context_text"]
    any_sha = False
    for cid in real_commits:
        sha8 = store.get_node(cid)["hash"][:8]
        if sha8 in text:
            any_sha = True
            break
    assert any_sha, "相关提交区应含真实 commit sha 前缀"

    # 相关概念：真实 IMPLEMENTS(命中函数) ∪ DESCRIBES(相关提交)
    impl = {d for s, _t, d, _p in store.edges("IMPLEMENTS") if s == fid}
    assert ctx["stats"]["concepts"] > 0
    assert impl, "append_system_event 应至少 IMPLEMENTS 一个概念"
    print("test_build_context_commits_concepts OK")


# ---------------------------------------------------------------------------
# 8) budget 截断：小预算下优先保留 ①命中实体，且不超预算
# ---------------------------------------------------------------------------

def test_budget_truncation(store):
    q = "改 _handle_terminate 会影响哪些调用方"
    full = build_repo_context(store, q, budget_chars=6000)
    tight = build_repo_context(store, q, budget_chars=300)
    assert tight["mode"] == "symbol"
    # 命中实体（最高优先级）应仍在
    assert "【命中实体】" in tight["context_text"]
    # 低优先级的相关概念区应被裁掉
    assert "【相关概念】" not in tight["context_text"]
    assert len(tight["context_text"]) < len(full["context_text"])
    # stats 反映真实汇集量，不随文本截断而改变
    assert tight["stats"]["concepts"] == full["stats"]["concepts"]
    print("test_budget_truncation OK")


# ---------------------------------------------------------------------------
# 9) 乱码 / 无关问题：allow_overview=False → mode='none'（供后端 L2 决策）；
#    默认 allow_overview=True → 回落 mode='overview'，永不失联
# ---------------------------------------------------------------------------

def test_none_mode(store):
    # 无符号、无主题、无 overview：确定性全空回落 none（词面与主题召回均须为空）
    for q in ["今天天气不错适合出门散步呀啦啦啦", "！！！。。。？？？"]:
        ctx = build_repo_context(store, q, allow_overview=False)
        assert ctx["mode"] == "none", f"{q!r} 不应命中任何符号/主题"
        assert ctx["context_text"] == ""
        assert ctx["linked"] == []
        # stats 统一五键（含新增 topics），全零
        assert ctx["stats"] == {"symbols": 0, "topics": 0, "impact_callers": 0,
                                "commits": 0, "concepts": 0}
    # 同样的乱码问题，默认 allow_overview=True 时回落 overview，不再"失联"
    ov = build_repo_context(store, "今天天气不错适合出门散步呀啦啦啦")
    assert ov["mode"] == "overview", "默认应回落 overview 而非 none"
    assert ov["context_text"], "overview 兜底必须有真实概览文本"
    assert link_entities(store, "完全无关的中文问题没有符号") == []
    print("test_none_mode OK")


# ---------------------------------------------------------------------------
# 10) 稳健性：impact_depth 非法值回落、不崩溃
# ---------------------------------------------------------------------------

def test_robust_depth(store):
    # 非白名单 depth（如 0 / 9 / bool）不应抛错，回落到默认 2
    for bad in (0, 9, 99, True):
        ctx = build_repo_context(store, "改 _handle_terminate", impact_depth=bad)
        assert ctx["mode"] == "symbol"
        assert ctx["stats"]["impact_callers"] > 0
    print("test_robust_depth OK")


if __name__ == "__main__":
    store = _load()
    test_tokenize()
    test_link_exact_qualname(store)
    test_link_dotted_short_suffix(store)
    test_link_module_and_topk(store)
    test_build_context_graph(store)
    test_build_context_aggregate(store)
    test_build_context_commits_concepts(store)
    test_budget_truncation(store)
    test_none_mode(store)
    test_robust_depth(store)
    print("\nALL TESTS PASSED")
