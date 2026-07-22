"""模糊谓词指标预计算（§4.3 / 裁定 D-02/D-03/D-12）——索引期写节点属性，纯标准库。

把「核心 / 热点 / 危险 / 复杂 / 被谁用」这些**模糊谓词**操作化为可预计算、可回溯的确定性
代理，落 `output/graph.json` 的节点属性（幂等回填，**只加/覆盖属性、图结构不动**）：

- ``fan_in``          Function 的 CALLS 入度（多少不同调用方直接调用它）。
- ``heat``            ``commits_all + 2×commits_90d``；90d 窗口基准 = **仓库最新提交日**
                      （确定性、非 wall-clock，落地设计 §4.3）。
- ``commits_all`` / ``commits_90d``  MODIFIES 该函数的（去重）提交数 / 其中落在 90d 窗口内者。
- ``churn_90d``       90d 窗口内 MODIFIES 边的 ``lines_added + lines_deleted`` 之和（真实行变更）。
- ``blast_radius``    反向 CALLS ≤3 跳闭包大小（不含自身，改动该函数会波及的上游函数数）；
                      ``blast_endpoints`` = 该闭包内 ``is_endpoint`` 函数数（本图端点为 0，如实产 0）。
- ``fix_involvement`` FIXES 提交 ∩ MODIFIES 该函数的提交数；本图**无 Issue / 无 FIXES 边**，
                      故如实产 **0**（真实数据铁律：无数据不伪造，非填充）。
- ``pagerank``        Function 级 CALLS 图的幂迭代 PageRank（d=0.85、阈 1e-6、含悬挂质量项，D-12）。
- ``module_pagerank`` （可选）Module 级 IMPORTS 图 PageRank（异构不并图，独立指标，D-12）。
- ``cyclomatic``      圈复杂度：**读源文件 span 重析 AST**（``extract_module`` → ``FunctionFacts.cyclomatic``，
                      §4.3/D-03），按 ``symbol_id`` 回填；不重建图谱（图数据不动，仅补此属性）。

复用 ``retrieve.impact`` 的 ``_reverse_adjacency`` / ``_bfs_levels``（同一反向 BFS 语义，不另造）。

CLI（幂等）：
    python -m repograph.metrics [--graph output/graph.json] [--repo-root <源码根>]
``--repo-root`` 缺省时跳过 cyclomatic（图数据无源码不臆造），其余图导出指标照常写。
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta

from .models import GraphStore, symbol_id
from .retrieve.impact import _reverse_adjacency, _bfs_levels

# 90d 窗口（落地设计 §4.3：基准为仓库最新提交日，确定性）。
_WINDOW_DAYS = 90
# blast_radius 反向闭包深度（落地设计任务 §4.3：≤3 跳）。
_BLAST_DEPTH = 3
# PageRank 幂迭代参数（D-12）。
_PR_DAMPING = 0.85
_PR_TOL = 1e-6
_PR_MAX_ITER = 200


# ---------------------------------------------------------------------------
# 时间窗口
# ---------------------------------------------------------------------------

def _parse_dt(s: str):
    """解析 commit authored_at（ISO 8601 带时区，如 2026-07-06T22:45:09-07:00）。失败返回 None。"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _window_start(store: GraphStore):
    """90d 窗口起点 = 仓库最新提交日 − 90 天（确定性）。无提交返回 None。"""
    latest = None
    for c in store.nodes("Commit"):
        dt = _parse_dt(c.get("authored_at"))
        if dt is not None and (latest is None or dt > latest):
            latest = dt
    if latest is None:
        return None
    return latest - timedelta(days=_WINDOW_DAYS)


# ---------------------------------------------------------------------------
# fan_in（CALLS 入度）
# ---------------------------------------------------------------------------

def compute_fan_in(store: GraphStore) -> dict:
    """每个 Function 的 CALLS 入度（不同直接调用方数）。复用反向邻接。"""
    rev = _reverse_adjacency(store, "CALLS")          # dst → {src}
    out = {}
    for n in store.nodes("Function"):
        out[n["id"]] = len(rev.get(n["id"], ()))
    return out


# ---------------------------------------------------------------------------
# heat / commits_all / commits_90d / churn_90d（MODIFIES + 90d 窗口）
# ---------------------------------------------------------------------------

def compute_heat_churn(store: GraphStore) -> dict:
    """返回 fid → {heat, commits_all, commits_90d, churn_90d}。

    commits_all = 去重的 MODIFIES 提交数；commits_90d = 其中落 90d 窗口者；
    churn_90d = 90d 窗口内 MODIFIES 边 (lines_added+lines_deleted) 之和；
    heat = commits_all + 2×commits_90d。窗口基准 = 仓库最新提交日（确定性）。
    """
    win = _window_start(store)
    commit_dt = {c["id"]: _parse_dt(c.get("authored_at")) for c in store.nodes("Commit")}

    all_commits: dict[str, set] = {}
    win_commits: dict[str, set] = {}
    churn: dict[str, int] = {}
    for src, _t, dst, props in store.edges("MODIFIES"):
        all_commits.setdefault(dst, set()).add(src)
        dt = commit_dt.get(src)
        in_win = (win is not None and dt is not None and dt >= win)
        if in_win:
            win_commits.setdefault(dst, set()).add(src)
            churn[dst] = churn.get(dst, 0) + int(props.get("lines_added", 0) or 0) \
                + int(props.get("lines_deleted", 0) or 0)

    out = {}
    for n in store.nodes("Function"):
        fid = n["id"]
        ca = len(all_commits.get(fid, ()))
        c90 = len(win_commits.get(fid, ()))
        out[fid] = {"commits_all": ca, "commits_90d": c90,
                    "churn_90d": churn.get(fid, 0), "heat": ca + 2 * c90}
    return out


# ---------------------------------------------------------------------------
# blast_radius（反向 CALLS ≤3 跳闭包 + 可达端点）
# ---------------------------------------------------------------------------

def compute_blast_radius(store: GraphStore) -> dict:
    """返回 fid → {blast_radius, blast_endpoints}。

    blast_radius = 反向 CALLS ≤_BLAST_DEPTH 跳闭包大小**不含自身**（改动该函数波及的上游函数数）;
    blast_endpoints = 闭包内 is_endpoint 为真的函数数（本图为 0 则如实 0）。复用 _bfs_levels。
    """
    rev = _reverse_adjacency(store, "CALLS")
    is_ep = {n["id"]: bool(n.get("is_endpoint")) for n in store.nodes("Function")}
    out = {}
    for n in store.nodes("Function"):
        fid = n["id"]
        levels, _tr = _bfs_levels([fid], rev, _BLAST_DEPTH)
        callers = [nid for nid in levels if nid != fid]      # 闭包不含自身
        eps = sum(1 for nid in callers if is_ep.get(nid))
        out[fid] = {"blast_radius": len(callers), "blast_endpoints": eps}
    return out


# ---------------------------------------------------------------------------
# fix_involvement（FIXES 提交 ∩ MODIFIES 该函数）
# ---------------------------------------------------------------------------

def compute_fix_involvement(store: GraphStore) -> dict:
    """FIXES 提交 ∩ MODIFIES 该函数的提交数。

    本图无 Issue、无 FIXES 边（FIXES 是 Commit→Issue），故全体如实为 0（真实数据铁律：
    无数据不伪造）。逻辑完备：一旦引入 Issue/FIXES 数据即自动生效。
    """
    fix_commits = {src for src, _t, _dst, _p in store.edges("FIXES")}
    fid_fix: dict[str, int] = {}
    if fix_commits:
        for src, _t, dst, _p in store.edges("MODIFIES"):
            if src in fix_commits:
                fid_fix[dst] = fid_fix.get(dst, 0) + 1
    return {n["id"]: fid_fix.get(n["id"], 0) for n in store.nodes("Function")}


# ---------------------------------------------------------------------------
# PageRank（纯 Python 幂迭代，D-12：d=0.85、阈 1e-6、含悬挂质量项）
# ---------------------------------------------------------------------------

def _forward_adjacency(store: GraphStore, etype: str, node_label: str) -> tuple:
    """(nodes, out_adj)：out_adj[src] = {dst}，只收 node_label 两端的边。"""
    nodes = [n["id"] for n in store.nodes(node_label)]
    nset = set(nodes)
    out_adj: dict[str, set] = {}
    for src, _t, dst, _p in store.edges(etype):
        if src in nset and dst in nset:
            out_adj.setdefault(src, set()).add(dst)
    return nodes, out_adj


def _power_iteration(nodes: list, out_adj: dict,
                     d: float = _PR_DAMPING, tol: float = _PR_TOL,
                     max_iter: int = _PR_MAX_ITER) -> dict:
    """标准 PageRank 幂迭代（含悬挂节点质量均摊）。rank 沿边方向流动（src→dst）。"""
    n = len(nodes)
    if n == 0:
        return {}
    pr = {x: 1.0 / n for x in nodes}
    outdeg = {x: len(out_adj.get(x, ())) for x in nodes}
    dangling = [x for x in nodes if outdeg[x] == 0]
    base = (1.0 - d) / n
    for _ in range(max_iter):
        dmass = d * sum(pr[x] for x in dangling) / n         # 悬挂质量均摊到全体
        nxt = {x: base + dmass for x in nodes}
        for x in nodes:
            deg = outdeg[x]
            if not deg:
                continue
            share = d * pr[x] / deg
            for m in out_adj[x]:
                nxt[m] += share
        err = sum(abs(nxt[x] - pr[x]) for x in nodes)
        pr = nxt
        if err < tol:
            break
    return pr


def compute_pagerank(store: GraphStore) -> dict:
    """Function 级 CALLS 图 PageRank（rank 沿 CALLS 方向：被重要函数调用者得分高）。"""
    nodes, out_adj = _forward_adjacency(store, "CALLS", "Function")
    return _power_iteration(nodes, out_adj)


def compute_module_pagerank(store: GraphStore) -> dict:
    """Module 级 IMPORTS 图 PageRank（可选独立指标，D-12：异构不并图）。"""
    nodes, out_adj = _forward_adjacency(store, "IMPORTS", "Module")
    return _power_iteration(nodes, out_adj)


# ---------------------------------------------------------------------------
# cyclomatic（读源文件 span 重析 AST；不重建图谱，仅补此属性）
# ---------------------------------------------------------------------------

def _repo_name(store: GraphStore) -> str:
    for n in store.nodes("Function"):
        r = n.get("repo")
        if r:
            return r
    for n in store.nodes():
        nid = n.get("id", "")
        if "::" in nid and not nid.startswith("concept::"):
            return nid.split("::", 1)[0]
    return ""


def compute_cyclomatic(store: GraphStore, repo_root: str) -> tuple:
    """读源文件重析 AST，按 symbol_id 回填 Function 圈复杂度。

    返回 (result: fid→cyclomatic, missing_files: [relpath])。真实数据铁律：只对**真实存在
    且可解析**的源文件计算；源码缺失/漂移时如实记入 missing_files、绝不臆造。
    复用 ``extract_module``——与结构层同一 AST/qualname 逻辑，圈复杂度已由 FunctionFacts 携带。
    """
    from .extract.ast_extractor import extract_module

    repo = _repo_name(store)
    relpaths = {}
    for n in store.nodes("Function"):
        f = n.get("file")
        if f:
            relpaths[f] = True

    result: dict[str, int] = {}
    missing: list[str] = []
    for relpath in sorted(relpaths):
        full = os.path.join(repo_root, relpath.replace("/", os.sep))
        if not os.path.exists(full):
            missing.append(relpath)
            continue
        try:
            with open(full, "r", encoding="utf-8") as fh:
                source = fh.read()
        except (OSError, UnicodeDecodeError):
            missing.append(relpath)
            continue
        mf = extract_module(repo, relpath, source)
        if mf is None:
            missing.append(relpath)
            continue
        for ff in mf.functions:
            result[symbol_id(repo, relpath, ff.qualname)] = ff.cyclomatic
    return result, missing


# ---------------------------------------------------------------------------
# 组装 + 幂等回填
# ---------------------------------------------------------------------------

def compute_all(store: GraphStore, repo_root: str | None = None) -> dict:
    """算全部指标，返回 fid/mid → 属性 dict（供 write_metrics 幂等回填）。含覆盖统计。"""
    fan_in = compute_fan_in(store)
    hc = compute_heat_churn(store)
    blast = compute_blast_radius(store)
    fix = compute_fix_involvement(store)
    pr = compute_pagerank(store)
    mpr = compute_module_pagerank(store)

    cyclo, cyclo_missing = ({}, [])
    if repo_root:
        cyclo, cyclo_missing = compute_cyclomatic(store, repo_root)

    node_props: dict[str, dict] = {}
    for n in store.nodes("Function"):
        fid = n["id"]
        props = {
            "fan_in": fan_in.get(fid, 0),
            "pagerank": round(pr.get(fid, 0.0), 8),
            "fix_involvement": fix.get(fid, 0),
        }
        props.update(hc.get(fid, {}))
        props.update(blast.get(fid, {}))
        if fid in cyclo:
            props["cyclomatic"] = cyclo[fid]
        node_props[fid] = props
    for n in store.nodes("Module"):
        node_props[n["id"]] = {"module_pagerank": round(mpr.get(n["id"], 0.0), 8)}

    stats = {
        "functions": sum(1 for _ in store.nodes("Function")),
        "modules": sum(1 for _ in store.nodes("Module")),
        "cyclomatic_written": len(cyclo),
        "cyclomatic_missing_files": cyclo_missing,
        "fix_involvement_nonzero": sum(1 for v in fix.values() if v),
        "fan_in_max": max(fan_in.values()) if fan_in else 0,
        "pagerank_sum": round(sum(pr.values()), 6),
        "window_start": None,
    }
    ws = _window_start(store)
    stats["window_start"] = ws.isoformat() if ws else None
    return {"node_props": node_props, "stats": stats}


def write_metrics(store: GraphStore, node_props: dict) -> int:
    """把指标属性幂等 merge 进节点（GraphStore.merge_node 只加/覆盖属性、不动结构）。返回写入节点数。"""
    written = 0
    for nid, props in node_props.items():
        node = store.get_node(nid)
        if node is None:
            continue
        store.merge_node(nid, node["label"], props)
        written += 1
    return written


def run(graph_path: str, repo_root: str | None = None) -> dict:
    """加载图谱 → 算全部指标 → 幂等回写同一 graph.json。返回覆盖统计。"""
    store = GraphStore.load(graph_path)
    result = compute_all(store, repo_root=repo_root)
    n_written = write_metrics(store, result["node_props"])
    store.save(graph_path)
    result["stats"]["nodes_written"] = n_written
    result["stats"]["graph_path"] = graph_path
    return result["stats"]


def main(argv=None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root_default = os.path.dirname(os.path.dirname(here))    # 仓库根（本工程）
    parser = argparse.ArgumentParser(
        description="RepoGraph 模糊谓词指标预计算（幂等写节点属性）")
    parser.add_argument("--graph", default=os.path.join(repo_root_default, "output", "graph.json"),
                        help="graph.json 路径（默认 output/graph.json）")
    parser.add_argument("--repo-root", default=None,
                        help="被索引源码根（补 cyclomatic；缺省则跳过 cyclomatic，其余指标照常）")
    args = parser.parse_args(argv)

    if not os.path.exists(args.graph):
        print(f"[FATAL] 缺少图谱 {args.graph}", file=sys.stderr)
        return 2
    stats = run(args.graph, repo_root=args.repo_root)
    print("RepoGraph metrics 幂等回填完成：")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
