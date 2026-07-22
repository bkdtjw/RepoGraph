# -*- coding: utf-8 -*-
"""V1 步骤1：圈定目标实体清单（40-60 个）。

从 output/graph.json 抽 FZ dev+test 20 题的 gold_entity(+alt) 及其 1 跳
IMPLEMENTS/DESCRIBES 邻域的核心函数/概念，产出 design_work/v1_targets.json。

只读图谱与数据集，不改任何 src。纯标准库。

产物 v1_targets.json 结构：
  {
    "gold_ids": [...],              # 20 题 gold + 存在的 alt（去重）
    "gold_equiv": {gold_id: [equiv...]},  # 每 gold 的命中等价集（同 gate.py）
    "targets": [ {id,label,name,tier,input_qualname,input_doc} ... ],  # 待生成卡片实体
    "counts": {...}
  }
tier: "gold" | "neighbor"
"""
from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAPH = os.path.join(ROOT, "output", "graph.json")
DATASET = os.path.join(ROOT, "eval", "dataset.jsonl")
OUT = os.path.join(ROOT, "design_work", "v1_targets.json")

MAX_TARGETS = 58            # 40-60 区间上沿留白
MAX_IMPL_FN_PER_CONCEPT = 3  # 每个 gold 概念最多取几个实现函数进邻域


def load_graph():
    g = json.load(open(GRAPH, encoding="utf-8"))
    nodes = {n["id"]: n for n in g["nodes"]}
    edges = g["edges"]
    return nodes, edges


def load_fz_rows():
    rows = []
    with open(DATASET, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r["subset"] in ("FZ_dev", "FZ_test"):
                rows.append(r)
    return rows


def gold_equiv_ids(gold_id, edges):
    """gate.py 同法：gold 及其 IMPLEMENTS/DESCRIBES 1 跳邻居（双向）。"""
    eq = {gold_id}
    for e in edges:
        if e["type"] in ("IMPLEMENTS", "DESCRIBES"):
            if e["dst"] == gold_id:
                eq.add(e["src"])
            if e["src"] == gold_id:
                eq.add(e["dst"])
    return eq


def first_line(text):
    if not text:
        return ""
    for ln in text.splitlines():
        s = ln.strip()
        if s:
            return s
    return ""


def make_input(node):
    """构造卡片输入摘要：Function/Class=qualname+docstring首行；Concept=name+description。"""
    label = node["label"]
    if label in ("Function", "Class"):
        qn = node.get("qualname", "")
        doc = first_line(node.get("docstring"))
        return qn, doc
    if label == "Concept":
        return node.get("name", ""), first_line(node.get("description"))
    if label == "Module":
        return node.get("name") or node.get("path", ""), first_line(node.get("docstring"))
    return node.get("name", node["id"]), ""


def main():
    nodes, edges = load_graph()
    rows = load_fz_rows()

    # ---- 1) 收集 gold + 存在的 alt ----
    gold_ids = []
    seen = set()
    missing = []
    for r in rows:
        for gid in [r["gold_entity"]] + list(r.get("alt_gold_entities") or []):
            if gid in seen:
                continue
            seen.add(gid)
            if gid in nodes:
                gold_ids.append(gid)
            else:
                missing.append(gid)

    # ---- 2) 每 gold 的命中等价集（诊断用，理解「命中」需要什么）----
    gold_equiv = {gid: sorted(gold_equiv_ids(gid, edges)) for gid in gold_ids}

    # ---- 3) 1 跳邻域核心函数/概念 ----
    # 预建 IMPLEMENTS 反向邻接：concept <- function/module
    impl_src_by_dst = {}   # concept_id -> [impl_src...]
    impl_dst_by_src = {}   # func_id -> [concept...]
    for e in edges:
        if e["type"] == "IMPLEMENTS":
            impl_src_by_dst.setdefault(e["dst"], []).append(e["src"])
            impl_dst_by_src.setdefault(e["src"], []).append(e["dst"])

    target_ids = list(gold_ids)   # gold 优先全部入
    tset = set(target_ids)

    def add(nid):
        if nid in nodes and nid not in tset and len(target_ids) < MAX_TARGETS:
            tset.add(nid)
            target_ids.append(nid)

    # gold=Concept → 加实现它的 Function（这些函数在 gold_equiv 内，命中即算 hit）
    for gid in gold_ids:
        node = nodes[gid]
        if node["label"] == "Concept":
            impls = [s for s in impl_src_by_dst.get(gid, []) if nodes.get(s, {}).get("label") == "Function"]
            impls = sorted(impls)[:MAX_IMPL_FN_PER_CONCEPT]
            for s in impls:
                add(s)
    # gold=Function → 加它实现的 Concept（在 gold_equiv 内）
    for gid in gold_ids:
        node = nodes[gid]
        if node["label"] == "Function":
            for c in sorted(impl_dst_by_src.get(gid, [])):
                add(c)

    # ---- 4) 组装 targets（带卡片输入摘要）----
    targets = []
    for nid in target_ids:
        node = nodes[nid]
        inp_qn, inp_doc = make_input(node)
        targets.append({
            "id": nid,
            "label": node["label"],
            "name": node.get("qualname") or node.get("name") or nid.rsplit("::", 1)[-1],
            "tier": "gold" if nid in set(gold_ids) else "neighbor",
            "input_qualname": inp_qn,
            "input_doc": inp_doc,
        })

    by_label = {}
    for t in targets:
        by_label[t["label"]] = by_label.get(t["label"], 0) + 1

    out = {
        "source_graph": os.path.relpath(GRAPH, ROOT),
        "gold_ids": gold_ids,
        "missing_gold_or_alt": missing,
        "gold_equiv": gold_equiv,
        "targets": targets,
        "counts": {
            "gold": len(gold_ids),
            "targets_total": len(targets),
            "by_label": by_label,
        },
    }
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("gold 数:", len(gold_ids), " | 缺失 gold/alt:", missing)
    print("targets 合计:", len(targets), " | 分布:", by_label)
    print("\n目标实体清单：")
    for t in targets:
        doc = (t["input_doc"] or "")[:34]
        print(f'  [{t["tier"]:8}] {t["label"]:8} {t["name"]:42} | doc: {doc}')
    print("\n每 gold 命中等价集大小（gate.py 同法，hit 需落其中）：")
    for gid in gold_ids:
        print(f'  {gid.rsplit("::",1)[-1][:40]:42} equiv={len(gold_equiv[gid])}')
    print("\n写出:", os.path.relpath(OUT, ROOT))


if __name__ == "__main__":
    main()
