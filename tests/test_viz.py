"""viz/render.py 自测：构造 20 节点小 store，跑 render_all，断言产物齐全。

运行：cd <repo> && python tests/test_viz.py
不依赖 pytest，纯断言。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, "src")

from repograph.models import (  # noqa: E402
    GraphStore, module_id, symbol_id, commit_id, issue_id, concept_id,
)
from repograph.viz.render import render_all  # noqa: E402

REPO = "demo-repo"
OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_viz_out")


def _build_store() -> GraphStore:
    """20 节点：4 Module + 3 Class + 6 Function + 3 Commit + 2 Issue + 2 Concept。"""
    st = GraphStore()

    # --- Modules ---
    mods = {
        "core": "src/pkg/core.py",
        "util": "src/pkg/util.py",
        "api": "src/pkg/api.py",
        "cli": "src/cli.py",
    }
    mid = {}
    for name, rel in mods.items():
        i = module_id(REPO, rel)
        mid[name] = i
        st.merge_node(i, "Module", {
            "repo": REPO, "path": rel, "name": name,
            "package": "pkg" if rel.startswith("src/pkg/") else "",
            "loc": 40 + len(name) * 7, "docstring": f"{name} 模块文档",
        })

    # --- Classes ---
    cls_specs = [
        ("core", "Engine"), ("api", "Handler"), ("util", "Cache"),
    ]
    cid = {}
    for mod, qn in cls_specs:
        rel = mods[mod]
        i = symbol_id(REPO, rel, qn)
        cid[qn] = i
        st.merge_node(i, "Class", {
            "repo": REPO, "qualname": qn, "file": rel,
            "span_start": 1, "span_end": 30, "docstring": f"{qn} 类", "bases": [],
        })
        st.merge_edge(mid[mod], "CONTAINS", i)

    # --- Functions（含方法 / 端点）---
    fn_specs = [
        ("core", "Engine.run", True, False),
        ("core", "Engine.step", True, False),
        ("util", "helper", False, False),
        ("util", "Cache.get", True, False),
        ("api", "get_status", False, True),   # 端点
        ("cli", "main", False, False),
    ]
    fid = {}
    for mod, qn, is_method, is_ep in fn_specs:
        rel = mods[mod]
        i = symbol_id(REPO, rel, qn)
        fid[qn] = i
        st.merge_node(i, "Function", {
            "repo": REPO, "qualname": qn, "file": rel,
            "span_start": 5, "span_end": 12, "signature": "(self)" if is_method else "()",
            "is_async": False, "is_method": is_method,
            "is_endpoint": is_ep,
            "http_method": "GET" if is_ep else None,
            "route_path": "/status" if is_ep else None,
            "docstring": f"{qn} 文档",
        })
        st.merge_edge(mid[mod], "CONTAINS", i)

    # --- Commits ---
    kid = {}
    for n in range(3):
        sha = f"abc{n:04d}sha"
        i = commit_id(REPO, sha)
        kid[n] = i
        st.merge_node(i, "Commit", {
            "repo": REPO, "hash": sha, "author": f"dev{n}",
            "authored_at": f"2026-0{n + 1}-10T09:00:00",
            "message": f"提交 {n}：修复若干问题\n\n详情略",
            "files_changed": 2 + n, "insertions": 10 * (n + 1), "deletions": 3 * n,
        })

    # --- Issues ---
    iid = {}
    for num in (7, 42):
        i = issue_id(REPO, num)
        iid[num] = i
        st.merge_node(i, "Issue", {
            "repo": REPO, "number": num, "title": f"议题 #{num} 标题",
            "state": "closed", "labels": ["bug"], "created_at": "2026-01-01T00:00:00",
            "body_excerpt": "问题描述……",
        })

    # --- Concepts ---
    for slug, nm, ct in (("layered-arch", "分层架构", "design_decision"),
                         ("cache-policy", "缓存策略", "domain_concept")):
        i = concept_id(slug)
        st.merge_node(i, "Concept", {
            "name": nm, "ctype": ct, "description": f"{nm}的说明文本",
            "aliases": [], "confidence": 0.9, "evidence": [],
        })

    # --- IMPORTS (Module→Module) ---
    for a, b in [("cli", "core"), ("core", "util"), ("api", "core"), ("api", "util")]:
        st.merge_edge(mid[a], "IMPORTS", mid[b], {"names": ["*"]})

    # --- CALLS (Function→Function) ---
    calls = [
        ("main", "Engine.run", 2), ("Engine.run", "Engine.step", 5),
        ("Engine.run", "helper", 1), ("Engine.step", "Cache.get", 3),
        ("get_status", "Engine.run", 1), ("get_status", "helper", 2),
        ("main", "helper", 1),
    ]
    for a, b, cnt in calls:
        st.merge_edge(fid[a], "CALLS", fid[b], {"count": cnt, "call_sites": [cnt]})

    # --- MODIFIES (Commit→Function) —— 让 Engine.run 成为热点 ---
    modifies = [
        (0, "Engine.run"), (1, "Engine.run"), (2, "Engine.run"),
        (0, "Engine.step"), (1, "helper"), (2, "Cache.get"),
    ]
    for c, fn in modifies:
        st.merge_edge(kid[c], "MODIFIES", fid[fn],
                      {"lines_added": 3, "lines_deleted": 1, "overlap_lines": 2})

    # --- FIXES (Commit→Issue) ---
    st.merge_edge(kid[0], "FIXES", iid[7], {"pattern": "fixes #7"})
    st.merge_edge(kid[2], "FIXES", iid[42], {"pattern": "closes #42"})

    return st


def main() -> None:
    st = _build_store()
    counts = st.counts()
    assert counts["total_nodes"] == 20, f"期望 20 节点，实得 {counts['total_nodes']}"
    print("[store] nodes:", counts["nodes"])
    print("[store] edges:", counts["edges"])

    produced = render_all(st, OUTDIR)
    print("[render_all] 产出:")
    for p in produced:
        print("   ", p)

    # 全部文件真实存在且非空
    for p in produced:
        assert os.path.isfile(p), f"文件不存在: {p}"
        assert os.path.getsize(p) > 0, f"文件为空: {p}"

    names = {os.path.basename(p) for p in produced}
    expected = {"graph.html", "import_graph.png", "import_graph.svg",
                "call_graph.png", "call_graph.svg", "hotspots.png"}
    assert expected <= names, f"缺失产物: {expected - names}"
    assert len(produced) == 6, f"期望 6 个文件（含 hotspots），实得 {len(produced)}: {names}"

    # HTML 内容校验
    html_path = os.path.join(OUTDIR, "graph.html")
    with open(html_path, encoding="utf-8") as f:
        htmltext = f.read()
    assert "vis-network" in htmltext, "HTML 未引用 vis-network"
    assert "unpkg.com/vis-network/standalone/umd/vis-network.min.js" in htmltext, "CDN 脚本缺失"
    assert "const graphData" in htmltext, "HTML 未内嵌 graphData"
    assert '"nodes"' in htmltext and '"edges"' in htmltext, "内嵌图 JSON 结构缺失"
    assert "Engine.run" in htmltext, "节点数据未出现在 HTML 中"
    assert "barnesHut" in htmltext, "物理布局 barnesHut 未配置"
    assert "toggleEdge" in htmltext, "边类型开关逻辑缺失"
    assert REPO in htmltext, "页面标题未含仓库名"
    # 节点/边统计出现在标题区
    assert "节点 20" in htmltext, "标题区未含节点统计"

    # 无 MODIFIES 时 hotspots 应被跳过
    st2 = GraphStore()
    st2.merge_node(module_id(REPO, "src/a.py"), "Module",
                   {"repo": REPO, "path": "src/a.py", "name": "a", "package": "", "loc": 1})
    out2 = render_all(st2, os.path.join(OUTDIR, "no_modifies"))
    names2 = {os.path.basename(p) for p in out2}
    assert "hotspots.png" not in names2, "无 MODIFIES 边时不应产出 hotspots.png"
    assert "graph.html" in names2 and "import_graph.png" in names2, "基础产物仍应生成"
    print("[edge-case] 无 MODIFIES：产出", sorted(names2), "（正确跳过 hotspots）")

    print("\n全部断言通过 [OK]")


if __name__ == "__main__":
    main()
