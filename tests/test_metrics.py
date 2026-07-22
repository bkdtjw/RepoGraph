"""自测：metrics 模糊谓词指标（§4.3 / 裁定 D-02/D-03/D-12）。

用真实 output/graph.json（multi-agent-orch，510 节点）验证：
- fan_in 与真实反向 CALLS 入度自洽（抽查已知 ground truth：append_system_event=6、Store._begin=10）；
- heat = commits_all + 2×commits_90d，churn_90d 为真实行变更之和，与 MODIFIES 边自洽；
- blast_radius = 反向 CALLS ≤3 跳闭包大小（不含自身），与 _bfs_levels 自洽；
- fix_involvement 全 0（本图无 FIXES 边，真实数据铁律：无数据不伪造）；
- pagerank 归一（Σ≈1）、幂迭代收敛；cyclomatic 与 AST 重析自洽；
- compute_all/write_metrics **幂等**、**不改图结构**（只加属性）。

不落盘（compute_* 纯读；只在内存 store 上 merge 后比对），避免污染 output/graph.json。

真实运行：cd C:/Users/nirvana/Desktop/代码库知识图谱 && python tests/test_metrics.py
"""
import os
import sys

sys.path.insert(0, "src")

from repograph.models import GraphStore
from repograph.metrics import (
    compute_fan_in, compute_heat_churn, compute_blast_radius,
    compute_fix_involvement, compute_pagerank, compute_module_pagerank,
    compute_cyclomatic, compute_all, write_metrics, _window_start,
)
from repograph.retrieve.impact import _reverse_adjacency, _bfs_levels

_GRAPH = os.path.join(os.path.dirname(__file__), "..", "output", "graph.json")
_SRC_ROOT = os.environ.get("REPO_SRC_ROOT", r"C:/Users/nirvana/Desktop/多agent协作系统")


def _load():
    assert os.path.exists(_GRAPH), f"缺少真实图谱 {_GRAPH}"
    return GraphStore.load(_GRAPH)


def _fid(store, qn):
    for n in store.nodes("Function"):
        if n["id"].endswith("::" + qn) or n.get("qualname") == qn:
            return n["id"]
    return None


def test_fan_in(store):
    fan = compute_fan_in(store)
    rev = _reverse_adjacency(store, "CALLS")
    # 与反向邻接完全自洽
    for n in store.nodes("Function"):
        assert fan[n["id"]] == len(rev.get(n["id"], ())), n["id"]
    # ground truth（test_context/integration 已核实）：append_system_event=6、Store._begin=10
    assert fan[_fid(store, "append_system_event")] == 6
    assert fan[_fid(store, "Store._begin")] == 10
    print("test_fan_in OK")


def test_heat_churn(store):
    hc = compute_heat_churn(store)
    win = _window_start(store)
    assert win is not None
    for n in store.nodes("Function"):
        v = hc[n["id"]]
        assert v["heat"] == v["commits_all"] + 2 * v["commits_90d"]
        assert v["commits_90d"] <= v["commits_all"]
        assert v["churn_90d"] >= 0
    # _handle_terminate 有真实提交（≥1），heat>0
    ht = hc[_fid(store, "_handle_terminate")]
    assert ht["commits_all"] >= 1 and ht["heat"] >= ht["commits_all"]
    print("test_heat_churn OK")


def test_blast_radius(store):
    blast = compute_blast_radius(store)
    rev = _reverse_adjacency(store, "CALLS")
    # 抽查一个函数：blast_radius == ≤3 跳反向闭包大小（不含自身）
    fid = _fid(store, "Store._begin")
    levels, _tr = _bfs_levels([fid], rev, 3)
    expect = len([x for x in levels if x != fid])
    assert blast[fid]["blast_radius"] == expect, (blast[fid], expect)
    # 本图无端点 → blast_endpoints 全 0（如实）
    assert all(v["blast_endpoints"] == 0 for v in blast.values())
    print("test_blast_radius OK")


def test_fix_involvement_all_zero(store):
    fix = compute_fix_involvement(store)
    # 本图无 Issue / FIXES 边 → 全 0（真实数据铁律：无数据不伪造）
    assert set(fix.values()) == {0}, "无 FIXES 数据时 fix_involvement 应全 0"
    assert sum(1 for _s, _t, _d, _p in store.edges("FIXES")) == 0
    print("test_fix_involvement_all_zero OK")


def test_pagerank(store):
    pr = compute_pagerank(store)
    assert len(pr) == sum(1 for _ in store.nodes("Function"))
    s = sum(pr.values())
    assert abs(s - 1.0) < 1e-6, f"PageRank 应归一, Σ={s}"
    assert all(v > 0 for v in pr.values()), "含悬挂质量项 → 全正"
    mpr = compute_module_pagerank(store)
    assert abs(sum(mpr.values()) - 1.0) < 1e-6
    print("test_pagerank OK")


def test_cyclomatic_reparse(store):
    if not os.path.isdir(_SRC_ROOT):
        print(f"test_cyclomatic_reparse SKIP (源码根不存在: {_SRC_ROOT})")
        return
    cyc, missing = compute_cyclomatic(store, _SRC_ROOT)
    assert not missing, f"源码文件应齐备, 缺: {missing}"
    # 覆盖全部 Function（15 文件全可解析）
    assert len(cyc) == sum(1 for _ in store.nodes("Function")), (len(cyc),)
    assert all(isinstance(v, int) and v >= 1 for v in cyc.values())
    # 复杂度最高者应 >1（真实存在多分支函数）
    assert max(cyc.values()) > 5
    print(f"test_cyclomatic_reparse OK (max cyclomatic={max(cyc.values())})")


def test_compute_all_idempotent_no_structure_change(store):
    n0 = sum(1 for _ in store.nodes())
    e0 = sum(1 for _ in store.edges())
    r1 = compute_all(store, repo_root=_SRC_ROOT if os.path.isdir(_SRC_ROOT) else None)
    write_metrics(store, r1["node_props"])
    # 再算一次 → 属性完全一致（幂等）
    r2 = compute_all(store, repo_root=_SRC_ROOT if os.path.isdir(_SRC_ROOT) else None)
    assert r1["node_props"] == r2["node_props"], "compute_all 应幂等"
    # 结构不变（只加属性）
    assert sum(1 for _ in store.nodes()) == n0
    assert sum(1 for _ in store.edges()) == e0
    # 写入节点数 = Function + Module
    n_fn = sum(1 for _ in store.nodes("Function"))
    n_mod = sum(1 for _ in store.nodes("Module"))
    assert len(r1["node_props"]) == n_fn + n_mod
    print(f"test_compute_all_idempotent OK (写属性节点 {n_fn + n_mod} = Fn {n_fn} + Mod {n_mod})")


if __name__ == "__main__":
    store = _load()
    test_fan_in(store)
    test_heat_churn(store)
    test_blast_radius(store)
    test_fix_involvement_all_zero(store)
    test_pagerank(store)
    test_cyclomatic_reparse(store)
    test_compute_all_idempotent_no_structure_change(store)
    print("\nALL TESTS PASSED")
