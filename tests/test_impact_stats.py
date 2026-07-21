"""自测：build.build_graph / stats.write_stats / retrieve.impact.impact_analysis
/ store.age.run_cypher。

真实运行（不依赖 pytest）：
    cd C:/Users/nirvana/Desktop/代码库知识图谱 && python tests/test_impact_stats.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, "src")

from repograph.models import (
    ClassFacts,
    FunctionFacts,
    GraphStore,
    ImportFacts,
    ModuleFacts,
    PipelineStats,
    commit_id,
    issue_id,
    module_id,
    symbol_id,
)
from repograph.build import build_graph
from repograph.stats import write_stats
from repograph.retrieve.impact import impact_analysis
from repograph.store import age

REPO = "testrepo"


# ---------------------------------------------------------------------------
# 手工组装小仓库：3 模块 / 6 函数 / 2 端点 / 1 类 / 调用链
#   src/app/api.py     handler_a(GET /a), handler_b(POST /b)   [2 端点]
#   src/app/service.py Service.run(方法), helper
#   src/app/core.py    compute, util
# 调用链（caller→callee）：
#   handler_a → Service.run → compute → util
#   handler_b → helper      → compute → util
# ---------------------------------------------------------------------------

def _fn(qualname, start, end, is_method=False, docstring=None,
        is_endpoint=False, http_method=None, route_path=None):
    return FunctionFacts(
        qualname=qualname, span_start=start, span_end=end,
        signature="()", is_async=False, is_method=is_method,
        docstring=docstring, is_endpoint=is_endpoint,
        http_method=http_method, route_path=route_path,
    )


def _make_modules():
    api = ModuleFacts(
        repo=REPO, relpath="src/app/api.py", name="api", package="app",
        dotted="app.api", loc=40, docstring="API module.\nSecond line ignored.",
        classes=[],
        functions=[
            _fn("handler_a", 5, 9, docstring="Handle A.\n\nMore detail.",
                is_endpoint=True, http_method="GET", route_path="/a"),
            _fn("handler_b", 11, 15, is_endpoint=True, http_method="POST", route_path="/b"),
        ],
        imports=ImportFacts(
            from_import_map={
                "Service": ("app.service", "Service"),
                "util": ("app.core", "util"),
            },
            external_imports=["fastapi"],
        ),
    )
    service = ModuleFacts(
        repo=REPO, relpath="src/app/service.py", name="service", package="app",
        dotted="app.service", loc=30, docstring=None,
        classes=[ClassFacts(qualname="Service", span_start=3, span_end=20,
                            docstring="Service class.", bases=["object"])],
        functions=[
            _fn("Service.run", 6, 12, is_method=True, docstring="Run it."),
            _fn("helper", 22, 26),
        ],
        imports=ImportFacts(
            from_import_map={"compute": ("app.core", "compute")},
            import_map={"core_alias": "app.core"},   # import app.core as core_alias → 整体导入
            external_imports=["typing"],
        ),
    )
    core = ModuleFacts(
        repo=REPO, relpath="src/app/core.py", name="core", package="app",
        dotted="app.core", loc=25, docstring="Core.",
        classes=[],
        functions=[_fn("compute", 3, 10, docstring="Compute."), _fn("util", 12, 15)],
        imports=ImportFacts(external_imports=["os"]),
    )
    return [api, service, core]


def _fid(relpath, qualname):
    return symbol_id(REPO, relpath, qualname)


def _make_calls():
    api, svc, core = "src/app/api.py", "src/app/service.py", "src/app/core.py"
    props = {"count": 1, "call_sites": [1]}
    return [
        (_fid(api, "handler_a"), _fid(svc, "Service.run"), dict(props)),
        (_fid(api, "handler_b"), _fid(svc, "helper"), dict(props)),
        (_fid(svc, "Service.run"), _fid(core, "compute"), dict(props)),
        (_fid(svc, "helper"), _fid(core, "compute"), dict(props)),
        (_fid(core, "compute"), _fid(core, "util"), dict(props)),
    ]


def _make_gitdata():
    cid = commit_id(REPO, "abc123")
    iid = issue_id(REPO, 7)
    core_mod = module_id(REPO, "src/app/core.py")
    compute = _fid("src/app/core.py", "compute")
    return {
        "commits": [{"id": cid, "hash": "abc123", "author": "x", "message": "fix #7"}],
        "issues": [{"id": iid, "number": 7, "title": "bug", "state": "closed"}],
        "modifies": [(cid, compute, {"lines_added": 3, "lines_deleted": 1, "overlap_lines": 2})],
        "touches": [(cid, core_mod, {"lines_added": 3, "lines_deleted": 1})],
        "fixes": [(cid, iid, {"pattern": "fixes"})],
    }


def _edge_set(store, etype):
    return {(s, d) for s, _t, d, _p in store.edges(etype)}


def _edge_props(store, etype):
    return {(s, d): p for s, _t, d, p in store.edges(etype)}


# ---------------------------------------------------------------------------
# 1) build_graph
# ---------------------------------------------------------------------------

def test_build_graph():
    store = build_graph(_make_modules(), _make_calls(), _make_gitdata(), REPO)
    counts = store.counts()

    assert counts["nodes"] == {"Module": 3, "Class": 1, "Function": 6,
                               "Commit": 1, "Issue": 1}, counts["nodes"]
    assert counts["total_nodes"] == 12, counts
    assert counts["edges"] == {"CONTAINS": 7, "IMPORTS": 3, "CALLS": 5,
                               "MODIFIES": 1, "TOUCHES": 1, "FIXES": 1}, counts["edges"]
    assert counts["total_edges"] == 18, counts

    api_mod = module_id(REPO, "src/app/api.py")
    svc_mod = module_id(REPO, "src/app/service.py")
    core_mod = module_id(REPO, "src/app/core.py")
    svc_class = _fid("src/app/service.py", "Service")
    run_id = _fid("src/app/service.py", "Service.run")
    helper_id = _fid("src/app/service.py", "helper")

    contains = _edge_set(store, "CONTAINS")
    # 方法挂 Class，而非挂 Module
    assert (svc_class, run_id) in contains
    assert (svc_mod, run_id) not in contains
    # 顶层函数挂 Module
    assert (svc_mod, helper_id) in contains
    # Module→Class
    assert (svc_mod, svc_class) in contains

    # IMPORTS names（仓库内解析 + 整体导入 '*module*' 合并）
    iprops = _edge_props(store, "IMPORTS")
    assert set(iprops[(api_mod, svc_mod)]["names"]) == {"Service"}
    assert set(iprops[(api_mod, core_mod)]["names"]) == {"util"}
    assert set(iprops[(svc_mod, core_mod)]["names"]) == {"compute", "*module*"}

    # Module 属性：external_imports + docstring 首行
    api_node = store.get_node(api_mod)
    assert api_node["external_imports"] == ["fastapi"]
    assert api_node["docstring"] == "API module."   # 仅首行

    # 端点函数属性
    ha = store.get_node(_fid("src/app/api.py", "handler_a"))
    assert ha["is_endpoint"] is True
    assert ha["http_method"] == "GET" and ha["route_path"] == "/a"
    assert ha["docstring"] == "Handle A."           # docstring 首行

    # Git 边落点
    cid = commit_id(REPO, "abc123")
    assert (cid, _fid("src/app/core.py", "compute")) in _edge_set(store, "MODIFIES")
    assert (cid, core_mod) in _edge_set(store, "TOUCHES")
    assert (cid, issue_id(REPO, 7)) in _edge_set(store, "FIXES")

    print("test_build_graph OK")
    return store


# ---------------------------------------------------------------------------
# 2) impact_analysis —— calls 模式分层
# ---------------------------------------------------------------------------

def test_impact_calls(store):
    api = "src/app/api.py"
    svc = "src/app/service.py"
    core = "src/app/core.py"
    handler_a = _fid(api, "handler_a")
    handler_b = _fid(api, "handler_b")
    run_id = _fid(svc, "Service.run")
    helper_id = _fid(svc, "helper")
    compute = _fid(core, "compute")
    util = _fid(core, "util")
    api_mod = module_id(REPO, api)
    svc_mod = module_id(REPO, svc)
    core_mod = module_id(REPO, core)

    # 改 compute：直接调用方 = Service.run, helper；传递 = handler_a, handler_b
    r = impact_analysis(store, "compute", depth=3, mode="calls")
    assert r["resolved_symbol"] == compute
    assert r["direct_callers"] == sorted([run_id, helper_id]), r["direct_callers"]
    assert r["transitive_callers"] == sorted([handler_a, handler_b]), r["transitive_callers"]
    assert r["affected_endpoints"] == sorted([handler_a, handler_b])
    assert r["affected_modules"] == sorted([api_mod, svc_mod, core_mod])
    assert r["truncated"] is False

    # 改 util，depth=1：只到直接调用方 compute，且应标记 truncated（上游未穷尽）
    r1 = impact_analysis(store, "util", depth=1, mode="calls")
    assert r1["direct_callers"] == [compute]
    assert r1["transitive_callers"] == []
    assert r1["affected_modules"] == [core_mod]     # util 与 compute 同属 core
    assert r1["affected_endpoints"] == []
    assert r1["truncated"] is True

    # 改 util，depth=3：整链穷尽，不截断
    r3 = impact_analysis(store, "util", depth=3, mode="calls")
    assert r3["direct_callers"] == [compute]
    assert r3["transitive_callers"] == sorted([run_id, helper_id, handler_a, handler_b])
    assert r3["affected_endpoints"] == sorted([handler_a, handler_b])
    assert r3["affected_modules"] == sorted([api_mod, svc_mod, core_mod])
    assert r3["truncated"] is False

    print("test_impact_calls OK")


# ---------------------------------------------------------------------------
# 3) impact_analysis —— 符号解析（精确 / 后缀 / 歧义 / 未命中）
# ---------------------------------------------------------------------------

def test_symbol_resolution(store):
    svc = "src/app/service.py"
    core = "src/app/core.py"

    # 精确 qualname
    assert impact_analysis(store, "compute")["resolved_symbol"] == _fid(core, "compute")
    # 唯一后缀：'run' → 'Service.run'
    assert impact_analysis(store, "run")["resolved_symbol"] == _fid(svc, "Service.run")
    # 精确的带类前缀
    assert impact_analysis(store, "Service.run")["resolved_symbol"] == _fid(svc, "Service.run")
    # 未命中
    assert impact_analysis(store, "does_not_exist") == {"error": "not_found"}

    # 歧义：同 qualname 精确多命中
    amb = GraphStore()
    amb.merge_node("r::a.py::process", "Function", {"qualname": "process", "is_endpoint": False})
    amb.merge_node("r::b.py::process", "Function", {"qualname": "process", "is_endpoint": False})
    res = impact_analysis(amb, "process")
    assert res["error"] == "ambiguous"
    assert res["candidates"] == sorted(["r::a.py::process", "r::b.py::process"])

    # 歧义：后缀多命中
    amb2 = GraphStore()
    amb2.merge_node("r::a.py::A.run", "Function", {"qualname": "A.run", "is_endpoint": False})
    amb2.merge_node("r::b.py::B.run", "Function", {"qualname": "B.run", "is_endpoint": False})
    res2 = impact_analysis(amb2, "run")
    assert res2["error"] == "ambiguous"
    assert res2["candidates"] == sorted(["r::a.py::A.run", "r::b.py::B.run"])

    print("test_symbol_resolution OK")


# ---------------------------------------------------------------------------
# 4) impact_analysis —— imports 模式
# ---------------------------------------------------------------------------

def test_impact_imports(store):
    api_mod = module_id(REPO, "src/app/api.py")
    svc_mod = module_id(REPO, "src/app/service.py")
    core_mod = module_id(REPO, "src/app/core.py")
    handler_a = _fid("src/app/api.py", "handler_a")
    handler_b = _fid("src/app/api.py", "handler_b")

    # compute 在 core：谁（传递）导入 core → api, service；受影响模块 = 全部 3 个
    r = impact_analysis(store, "compute", depth=3, mode="imports")
    assert r["resolved_symbol"] == _fid("src/app/core.py", "compute")
    assert r["direct_callers"] == sorted([api_mod, svc_mod])
    assert r["transitive_callers"] == []       # api 亦直接导入 core，故无 2 跳新增
    assert r["affected_modules"] == sorted([api_mod, svc_mod, core_mod])
    # 受影响模块内的端点
    assert r["affected_endpoints"] == sorted([handler_a, handler_b])
    assert r["truncated"] is False

    print("test_impact_imports OK")


# ---------------------------------------------------------------------------
# 5) 参数白名单 / mode 校验
# ---------------------------------------------------------------------------

def test_guards(store):
    for bad in (0, 5, -1, 3.0, True, "3"):
        raised = False
        try:
            impact_analysis(store, "compute", depth=bad)
        except ValueError:
            raised = True
        assert raised, f"depth={bad!r} 应被白名单拒绝"

    raised = False
    try:
        impact_analysis(store, "compute", mode="bogus")
    except ValueError:
        raised = True
    assert raised, "非法 mode 应报错"

    print("test_guards OK")


# ---------------------------------------------------------------------------
# 6) write_stats
# ---------------------------------------------------------------------------

def test_write_stats(store):
    ps = PipelineStats(call_resolved=8, call_unresolved=2,
                       modifies_commits=1, code_commits=1,
                       parse_skips_worktree=0, parse_skips_blob=0)
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "sub", "stats.json")   # 子目录不存在，验证自动创建
    out = write_stats(store, ps, path)

    assert out["total_nodes"] == 12
    assert out["total_edges"] == 18
    assert out["call_resolved"] == 8               # 来自 pstats
    assert out["call_resolved_rate"] == 0.8
    assert out["modifies_coverage"] == 1.0
    assert "nodes" in out and "edges" in out       # 来自 counts
    assert os.path.exists(path)

    with open(path, encoding="utf-8") as f:
        reloaded = json.load(f)
    assert reloaded == out

    print("test_write_stats OK")


# ---------------------------------------------------------------------------
# 7) store.age —— 形态预留：run_cypher raise NotImplementedError
# ---------------------------------------------------------------------------

def test_age_placeholder():
    raised = False
    try:
        age.run_cypher(None, "MATCH (n) RETURN n", {}, ["n"])
    except NotImplementedError as e:
        raised = True
        assert "6.3" in str(e), "错误信息应指向文档 §6.3 切换点"
    assert raised, "run_cypher 应抛 NotImplementedError"
    assert isinstance(age.has_psycopg(), bool)
    print("test_age_placeholder OK")


if __name__ == "__main__":
    store = test_build_graph()
    test_impact_calls(store)
    test_symbol_resolution(store)
    test_impact_imports(store)
    test_guards(store)
    test_write_stats(store)
    test_age_placeholder()
    print("\nALL TESTS PASSED")
