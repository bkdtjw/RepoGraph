"""viz/render.py — RepoGraph 可视化产出层。

产出并返回文件路径列表：
1) graph.html        自包含交互式知识图（内嵌图 JSON + vis-network CDN）
2) import_graph.png/.svg  模块级 IMPORTS 依赖图
3) call_graph.png/.svg     函数级 CALLS 调用图
4) hotspots.png            变更热点函数（MODIFIES 次数 Top15）；无 MODIFIES 边则跳过

仅依赖标准库 + networkx + matplotlib。GraphStore 契约见 repograph.models。
"""
from __future__ import annotations

import html
import json
import os
from collections import defaultdict
from typing import TYPE_CHECKING

# ---- matplotlib 必须在导入 pyplot 之前锁定 Agg 后端（无显示环境安全）----
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import networkx as nx  # noqa: E402

if TYPE_CHECKING:  # 仅类型检查期引用，运行期用鸭子类型，避免任何耦合
    from ..models import GraphStore

# ---- 中文渲染 ----
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

# ---------------------------------------------------------------------------
# 配色与中文标签
# ---------------------------------------------------------------------------

NODE_COLORS = {
    "Module": "#4E79A7",    # 蓝
    "Class": "#59C3C3",     # 青
    "Function": "#59A14F",  # 绿
    "Commit": "#F28E2B",    # 橙
    "Issue": "#E15759",     # 红
    "Concept": "#B07AA1",   # 紫
}
NODE_LABELS_CN = {
    "Module": "模块", "Class": "类", "Function": "函数",
    "Commit": "提交", "Issue": "议题", "Concept": "概念",
}
EDGE_COLORS = {
    "CONTAINS": "#BAB0AC",
    "IMPORTS": "#4E79A7",
    "CALLS": "#59A14F",
    "MODIFIES": "#F28E2B",
    "TOUCHES": "#F1CE63",
    "FIXES": "#E15759",
    "PROPOSES": "#B07AA1",
    "DESCRIBES": "#9D7660",
    "IMPLEMENTS": "#76B7B2",
}
EDGE_LABELS_CN = {
    "CONTAINS": "包含", "IMPORTS": "导入", "CALLS": "调用", "MODIFIES": "修改",
    "TOUCHES": "触及", "FIXES": "修复", "PROPOSES": "提出",
    "DESCRIBES": "描述", "IMPLEMENTS": "实现",
}

_TOP_N_CALL = 12       # 调用图中标红加大的度数 Top-N
_TOP_N_HOTSPOT = 15    # 热点条形图条数


# ---------------------------------------------------------------------------
# 公共小工具
# ---------------------------------------------------------------------------

def _short_id(nid: str) -> str:
    return nid.rsplit("::", 1)[-1] if "::" in nid else nid


def _repo_name(store) -> str:
    for n in store.nodes():
        r = n.get("repo")
        if r:
            return str(r)
        nid = n.get("id", "")
        if "::" in nid:
            return nid.split("::", 1)[0]
    return "repo"


def _degrees(store) -> dict[str, int]:
    deg: dict[str, int] = defaultdict(int)
    for src, _t, dst, _p in store.edges():
        deg[src] += 1
        deg[dst] += 1
    return deg


def _module_label(node: dict) -> str:
    """模块显示标签：去掉 src/ 前缀（并去掉 .py 尾缀便于阅读）。"""
    path = node.get("path") or _short_id(node.get("id", ""))
    path = str(path).replace("\\", "/")
    if path.startswith("src/"):
        path = path[len("src/"):]
    if path.endswith(".py"):
        path = path[:-3]
    return path or node.get("id", "?")


# ---------------------------------------------------------------------------
# 1) graph.html —— 自包含交互式图
# ---------------------------------------------------------------------------

def _node_display(n: dict) -> str:
    label = n["label"]
    if label == "Module":
        return n.get("name") or _module_label(n)
    if label in ("Class", "Function"):
        return n.get("qualname") or _short_id(n["id"])
    if label == "Commit":
        h = n.get("hash") or _short_id(n["id"])
        return str(h)[:8]
    if label == "Issue":
        return f"#{n.get('number', '?')}"
    if label == "Concept":
        return n.get("name") or _short_id(n["id"])
    return _short_id(n["id"])


def _node_title(n: dict, deg: int) -> str:
    """hover 提示（HTML 片段，vis-network 以 innerHTML 渲染）。"""
    label = n["label"]
    esc = html.escape
    rows = [f"<b>{esc(NODE_LABELS_CN.get(label, label))}</b>"]

    def add(k: str, v) -> None:
        if v is None or v == "":
            return
        rows.append(f"{esc(str(k))}: {esc(str(v))}")

    if label == "Module":
        add("路径", n.get("path"))
        add("包", n.get("package"))
        add("LOC", n.get("loc"))
    elif label == "Class":
        add("限定名", n.get("qualname"))
        add("文件", n.get("file"))
        if n.get("span_start") is not None:
            add("行", f"{n.get('span_start')}–{n.get('span_end')}")
        if n.get("bases"):
            add("基类", ", ".join(map(str, n["bases"])))
    elif label == "Function":
        add("限定名", n.get("qualname"))
        add("签名", n.get("signature"))
        if n.get("is_endpoint"):
            add("端点", f"{n.get('http_method', '') or ''} {n.get('route_path', '') or ''}".strip())
        flags = []
        if n.get("is_async"):
            flags.append("async")
        if n.get("is_method"):
            flags.append("method")
        if flags:
            add("标志", " ".join(flags))
    elif label == "Commit":
        add("hash", (str(n.get("hash") or ""))[:10])
        add("作者", n.get("author"))
        add("时间", n.get("authored_at"))
        if n.get("message"):
            add("信息", str(n["message"]).splitlines()[0][:80])
    elif label == "Issue":
        add("编号", f"#{n.get('number')}")
        add("标题", n.get("title"))
        add("状态", n.get("state"))
    elif label == "Concept":
        add("名称", n.get("name"))
        add("类型", n.get("ctype"))
        if n.get("description"):
            add("描述", str(n["description"])[:120])
    add("度数", deg)
    return "<br>".join(rows)


def _edge_title(etype: str, p: dict) -> str:
    base = f"{EDGE_LABELS_CN.get(etype, etype)}（{etype}）"
    extra: list[str] = []
    if etype == "CALLS" and p.get("count") is not None:
        extra.append(f"次数 {p['count']}")
    if etype == "MODIFIES":
        if p.get("lines_added") is not None:
            extra.append(f"+{p['lines_added']}")
        if p.get("lines_deleted") is not None:
            extra.append(f"-{p['lines_deleted']}")
    if etype == "IMPORTS" and p.get("names"):
        extra.append("符号: " + ", ".join(map(str, list(p["names"])[:5])))
    if etype == "FIXES" and p.get("pattern"):
        extra.append(str(p["pattern"]))
    return html.escape(base + (" · " + " ".join(extra) if extra else ""))


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  :root { --fg:#1f2933; --muted:#6b7785; --panel:#f7f9fb; --border:#e2e8f0; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:"Microsoft YaHei","PingFang SC",sans-serif; color:var(--fg); }
  header { padding:12px 18px; border-bottom:1px solid var(--border); background:#fff; }
  header h1 { font-size:18px; margin:0 0 4px; }
  header .stats { font-size:13px; color:var(--muted); }
  .layout { display:flex; height:calc(100vh - 62px); }
  #panel { width:250px; flex:0 0 250px; padding:14px; overflow-y:auto;
           background:var(--panel); border-right:1px solid var(--border); font-size:13px; }
  #panel h2 { font-size:13px; margin:14px 0 6px; color:var(--muted); text-transform:uppercase; letter-spacing:.5px; }
  #panel h2:first-child { margin-top:0; }
  .chk, .lg { display:flex; align-items:center; gap:6px; margin:4px 0; cursor:pointer; }
  .chk input { cursor:pointer; }
  .swatch { display:inline-block; width:13px; height:13px; border-radius:3px; flex:0 0 auto; }
  #net { flex:1; min-width:0; background:#fdfdfe; }
  #phys-hint { display:none; margin:8px 0; padding:8px 10px; background:#fff4e5;
               border:1px solid #f0c78a; border-radius:6px; color:#8a5a12; font-size:12px; }
  #phys-hint button { margin-top:6px; cursor:pointer; }
  .btnrow button { font-size:12px; padding:4px 8px; margin:2px 4px 2px 0; cursor:pointer; }
</style>
</head>
<body>
<header>
  <h1>RepoGraph 交互式知识图谱 · __REPO__</h1>
  <div class="stats">节点 __N_NODES__ 个 · 边 __N_EDGES__ 条　|　__NODE_SUMMARY__</div>
</header>
<div class="layout">
  <div id="panel">
    <div id="phys-hint">节点较多（&gt;300），已默认关闭物理布局以保证流畅。
      <br><button onclick="setPhysics(true)">▶ 手动开启物理布局</button></div>
    <h2>节点图例</h2>
    __LEGEND__
    <h2>边类型开关</h2>
    __EDGE_CHECKS__
    <h2>视图</h2>
    <label class="chk"><input type="checkbox" id="phys-toggle" onchange="setPhysics(this.checked)"> 物理布局</label>
    <div class="btnrow">
      <button onclick="network.fit({animation:true})">适应窗口</button>
    </div>
  </div>
  <div id="net"></div>
</div>
<script>
  const graphData = __GRAPH_JSON__;
  const nodeCount = graphData.nodes.length;
  const nodes = new vis.DataSet(graphData.nodes);
  const allEdges = new vis.DataSet(graphData.edges);
  const visibleTypes = new Set(graphData.edges.map(function(e){ return e.etype; }));
  const edgesView = new vis.DataView(allEdges, { filter: function(e){ return visibleTypes.has(e.etype); } });

  const physicsDefault = __PHYSICS_DEFAULT__;
  const options = {
    nodes: {
      shape: "dot",
      scaling: { min: 6, max: 44, label: { enabled: true, min: 11, max: 26 } },
      font: { face: "Microsoft YaHei, PingFang SC, sans-serif", size: 14 }
    },
    edges: {
      arrows: { to: { enabled: true, scaleFactor: 0.6 } },
      smooth: { enabled: true, type: "dynamic" },
      width: 1.2
    },
    physics: {
      enabled: physicsDefault,
      solver: "barnesHut",
      barnesHut: { gravitationalConstant: -8000, centralGravity: 0.3, springLength: 130,
                   springConstant: 0.04, damping: 0.09, avoidOverlap: 0.15 },
      stabilization: { enabled: true, iterations: 200 }
    },
    interaction: { hover: true, tooltipDelay: 120, navigationButtons: true, keyboard: true, multiselect: true }
  };

  const container = document.getElementById("net");
  const network = new vis.Network(container, { nodes: nodes, edges: edgesView }, options);

  document.getElementById("phys-toggle").checked = physicsDefault;
  if (nodeCount > 300) { document.getElementById("phys-hint").style.display = "block"; }

  function toggleEdge(cb) {
    const t = cb.getAttribute("data-etype");
    if (cb.checked) { visibleTypes.add(t); } else { visibleTypes.delete(t); }
    edgesView.refresh();
  }
  function setPhysics(on) {
    network.setOptions({ physics: { enabled: on } });
    document.getElementById("phys-toggle").checked = on;
  }
</script>
</body>
</html>
"""


def _render_html(store, outdir: str, repo: str) -> str:
    deg = _degrees(store)

    nodes_json = []
    for n in store.nodes():
        nid = n["id"]
        d = deg.get(nid, 0)
        nodes_json.append({
            "id": nid,
            "label": _node_display(n),
            "title": _node_title(n, d),
            "color": NODE_COLORS.get(n["label"], "#888888"),
            "value": d + 1,
            "group": n["label"],
        })

    edges_json = []
    for src, etype, dst, p in store.edges():
        edges_json.append({
            "from": src,
            "to": dst,
            "etype": etype,
            "color": {"color": EDGE_COLORS.get(etype, "#999999"), "opacity": 0.75},
            "title": _edge_title(etype, p),
        })

    counts = store.counts()
    n_nodes = counts["total_nodes"]
    n_edges = counts["total_edges"]

    node_summary = " · ".join(
        f"{NODE_LABELS_CN.get(lbl, lbl)} {cnt}"
        for lbl, cnt in counts["nodes"].items()
    ) or "（空）"

    legend = "".join(
        f'<span class="lg"><span class="swatch" style="background:{NODE_COLORS.get(lbl, "#888")}"></span>'
        f'{NODE_LABELS_CN.get(lbl, lbl)}（{cnt}）</span>'
        for lbl, cnt in counts["nodes"].items()
    ) or '<span class="lg">（无节点）</span>'

    edge_checks = "".join(
        f'<label class="chk"><input type="checkbox" checked data-etype="{etype}" onchange="toggleEdge(this)">'
        f'<span class="swatch" style="background:{EDGE_COLORS.get(etype, "#999")}"></span>'
        f'{EDGE_LABELS_CN.get(etype, etype)}（{etype} · {cnt}）</label>'
        for etype, cnt in counts["edges"].items()
    ) or '<span class="chk">（无边）</span>'

    graph_json = json.dumps({"nodes": nodes_json, "edges": edges_json}, ensure_ascii=False)
    # 防止字符串中的 "</..." 提前闭合 <script>；JS 字符串里 <\/ 等价于 </
    graph_json = graph_json.replace("</", "<\\/")

    page_title = f"RepoGraph 知识图谱 · {repo}"
    htmltext = (
        _HTML_TEMPLATE
        .replace("__TITLE__", html.escape(page_title))
        .replace("__REPO__", html.escape(repo))
        .replace("__N_NODES__", str(n_nodes))
        .replace("__N_EDGES__", str(n_edges))
        .replace("__NODE_SUMMARY__", node_summary)
        .replace("__LEGEND__", legend)
        .replace("__EDGE_CHECKS__", edge_checks)
        .replace("__PHYSICS_DEFAULT__", "true" if n_nodes <= 300 else "false")
        .replace("__GRAPH_JSON__", graph_json)
    )

    out = os.path.join(outdir, "graph.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(htmltext)
    return os.path.abspath(out)


# ---------------------------------------------------------------------------
# 布局与尺寸工具（matplotlib 图共用）
# ---------------------------------------------------------------------------

def _layout(G: "nx.Graph") -> dict:
    """布局优先级：graphviz(nx_agraph) → kamada_kawai(节点少) → spring(seed=42)。"""
    n = G.number_of_nodes()
    if n == 0:
        return {}
    try:
        from networkx.drawing.nx_agraph import graphviz_layout
        return graphviz_layout(G, prog="dot")
    except Exception:
        pass
    if n <= 40:
        try:
            return nx.kamada_kawai_layout(G)
        except Exception:
            pass
    try:
        return nx.spring_layout(G, seed=42)
    except Exception:
        return nx.circular_layout(G)


def _figsize(n: int) -> tuple[float, float]:
    s = max(8.0, min(40.0, 6.0 + n * 0.35))
    return (s, s)


def _save_fig(fig, outdir: str, stem: str, svg: bool = True) -> list[str]:
    paths = []
    png = os.path.join(outdir, stem + ".png")
    fig.savefig(png, dpi=200, bbox_inches="tight")
    paths.append(os.path.abspath(png))
    if svg:
        sv = os.path.join(outdir, stem + ".svg")
        fig.savefig(sv, bbox_inches="tight")
        paths.append(os.path.abspath(sv))
    plt.close(fig)
    return paths


# ---------------------------------------------------------------------------
# 2) import_graph —— 模块级 IMPORTS 图
# ---------------------------------------------------------------------------

def _render_import_graph(store, outdir: str) -> list[str]:
    G = nx.DiGraph()
    labels: dict[str, str] = {}
    for n in store.nodes("Module"):
        G.add_node(n["id"])
        labels[n["id"]] = _module_label(n)
    for src, _t, dst, _p in store.edges("IMPORTS"):
        for nid in (src, dst):
            if nid not in G:
                G.add_node(nid)
                node = store.get_node(nid)
                labels[nid] = _module_label(node) if node else _short_id(nid)
        G.add_edge(src, dst)

    n = G.number_of_nodes()
    fig, ax = plt.subplots(figsize=_figsize(n))
    if n == 0:
        ax.text(0.5, 0.5, "无模块 / 无 IMPORTS 边", ha="center", va="center", fontsize=16)
        ax.axis("off")
    else:
        pos = _layout(G)
        deg = dict(G.degree())
        sizes = [320 + deg[x] * 240 for x in G.nodes()]
        nx.draw_networkx_edges(
            G, pos, edge_color="#9AA7B4", arrows=True, arrowstyle="-|>",
            arrowsize=18, width=1.2, connectionstyle="arc3,rad=0.05",
            min_source_margin=6, min_target_margin=10, ax=ax,
        )
        nx.draw_networkx_nodes(G, pos, node_size=sizes, node_color="#4E79A7",
                               alpha=0.92, edgecolors="#2f4b6b", linewidths=0.6, ax=ax)
        nx.draw_networkx_labels(G, pos, labels=labels, font_size=8,
                                font_family="Microsoft YaHei", ax=ax)
        ax.set_title(f"模块导入图 · {G.number_of_nodes()} 模块 / {G.number_of_edges()} 条依赖",
                     fontsize=15)
        ax.axis("off")
    fig.tight_layout()
    return _save_fig(fig, outdir, "import_graph")


# ---------------------------------------------------------------------------
# 3) call_graph —— 函数级 CALLS 图
# ---------------------------------------------------------------------------

def _render_call_graph(store, outdir: str) -> list[str]:
    G = nx.DiGraph()
    qname: dict[str, str] = {}
    is_ep: dict[str, bool] = {}

    for src, _t, dst, _p in store.edges("CALLS"):
        G.add_edge(src, dst)
    for nid in list(G.nodes()):
        node = store.get_node(nid)
        qname[nid] = (node.get("qualname") if node else None) or _short_id(nid)
        is_ep[nid] = bool(node.get("is_endpoint")) if node else False

    n = G.number_of_nodes()
    fig, ax = plt.subplots(figsize=_figsize(max(n, 6)))
    if n == 0:
        ax.text(0.5, 0.5, "无 CALLS 边（未解析到可静态判定的调用）",
                ha="center", va="center", fontsize=15)
        ax.axis("off")
    else:
        pos = _layout(G)
        deg = dict(G.degree())
        top = sorted(deg, key=lambda k: deg[k], reverse=True)[:_TOP_N_CALL]
        topset = set(top)

        nontop_dot = [x for x in G.nodes() if x not in topset and not is_ep[x]]
        nontop_tri = [x for x in G.nodes() if x not in topset and is_ep[x]]
        top_dot = [x for x in top if not is_ep[x]]
        top_tri = [x for x in top if is_ep[x]]

        nx.draw_networkx_edges(
            G, pos, edge_color="#D2D6DB", arrows=True, arrowstyle="-|>",
            arrowsize=10, width=0.7, alpha=0.6,
            connectionstyle="arc3,rad=0.05", min_target_margin=6, ax=ax,
        )
        # 非 Top：小号浅色，不出标签，防拥挤
        if nontop_dot:
            nx.draw_networkx_nodes(G, pos, nodelist=nontop_dot, node_color="#BFD9B4",
                                   node_size=110, node_shape="o", alpha=0.7, ax=ax)
        if nontop_tri:
            nx.draw_networkx_nodes(G, pos, nodelist=nontop_tri, node_color="#BFD9B4",
                                   node_size=150, node_shape="^", alpha=0.85,
                                   edgecolors="#5B8C3E", linewidths=0.6, ax=ax)
        # Top12：标红加大
        if top_dot:
            nx.draw_networkx_nodes(G, pos, nodelist=top_dot, node_color="#E15759",
                                   node_size=760, node_shape="o", alpha=0.95,
                                   edgecolors="#7A1F20", linewidths=1.0, ax=ax)
        if top_tri:
            nx.draw_networkx_nodes(G, pos, nodelist=top_tri, node_color="#E15759",
                                   node_size=920, node_shape="^", alpha=0.95,
                                   edgecolors="#7A1F20", linewidths=1.0, ax=ax)
        nx.draw_networkx_labels(G, pos, labels={x: qname[x] for x in top},
                                font_size=10, font_color="#7A1F20",
                                font_family="Microsoft YaHei", ax=ax)
        ax.set_title(
            f"函数调用图 · {n} 函数 / {G.number_of_edges()} 次调用"
            f"（红=度数 Top{min(_TOP_N_CALL, n)}，▲=端点）",
            fontsize=15,
        )
        ax.axis("off")
    fig.tight_layout()
    return _save_fig(fig, outdir, "call_graph")


# ---------------------------------------------------------------------------
# 4) hotspots —— MODIFIES 次数 Top15 函数横向条形图
# ---------------------------------------------------------------------------

def _render_hotspots(store, outdir: str) -> list[str]:
    counter: dict[str, int] = defaultdict(int)
    for _src, _t, dst, _p in store.edges("MODIFIES"):
        counter[dst] += 1
    if not counter:
        return []  # 无 MODIFIES 边：跳过该图，返回值中不含它

    items = sorted(counter.items(), key=lambda kv: kv[1], reverse=True)[:_TOP_N_HOTSPOT]
    labels, vals = [], []
    for nid, c in items:
        node = store.get_node(nid)
        q = (node.get("qualname") if node else None) or _short_id(nid)
        labels.append(str(q))
        vals.append(c)
    # 反转使最高值位于顶部
    labels.reverse()
    vals.reverse()

    fig, ax = plt.subplots(figsize=(11, max(4.0, 0.5 * len(vals) + 1.6)))
    ypos = range(len(vals))
    ax.barh(list(ypos), vals, color="#F28E2B", edgecolor="#b5661a")
    ax.set_yticks(list(ypos))
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlabel("被修改次数（MODIFIES 边数）")
    ax.set_title(f"变更热点函数 Top{len(vals)}", fontsize=15)
    vmax = max(vals) if vals else 1
    for i, v in enumerate(vals):
        ax.text(v + vmax * 0.01, i, str(v), va="center", fontsize=8, color="#7a4a10")
    ax.grid(axis="x", alpha=0.3)
    ax.margins(x=0.08)
    fig.tight_layout()
    return _save_fig(fig, outdir, "hotspots", svg=False)


# ---------------------------------------------------------------------------
# 对外入口（API 约定）
# ---------------------------------------------------------------------------

def render_all(store: "GraphStore", outdir: str) -> list[str]:
    """渲染全部可视化产物到 outdir，返回产出文件的绝对路径列表。"""
    os.makedirs(outdir, exist_ok=True)
    repo = _repo_name(store)

    produced: list[str] = []
    produced.append(_render_html(store, outdir, repo))
    produced.extend(_render_import_graph(store, outdir))
    produced.extend(_render_call_graph(store, outdir))
    produced.extend(_render_hotspots(store, outdir))
    return produced
