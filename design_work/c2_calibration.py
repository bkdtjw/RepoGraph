# -*- coding: utf-8 -*-
"""C2 后 V0 分带校准**复跑**（强制，落地设计 §4.6 FROZEN 注 / calibration §4）。

C2 中文卡片入库（enrich 写 zh_desc/zh_aliases 到 output/graph.json）后，用**富化后的真实
`topic.topic_recall`** 在 FZ-dev 上重跑 V0 网格，看能否产出可行（消歧率≤0.2 且 hit@1>0）的
分带参数以**替换**过渡规则「仅方法档≥80 自动锚定」。

维度（D-N1 分数语义，同 v0_calibration.py）：
  rel_margin ∈ {0.10,0.15,0.20,0.25} × min_score ∈ {0.5,1.0,1.5,2.0} × single_weak_autopick ∈ {on,off}
目标 max hit@1；约束 消歧触发率（过问代理）≤0.2。hit 判定复刻 eval/gate.py（gold_equiv 1 跳）。

与 v0_calibration.py 的差异：本脚本**直接调用生产 `topic.topic_recall`**（读富化后 graph.json），
不再用 rg_exp_lib 复刻——因 C2 已把卡片写进真实图谱，生产召回即事实源（更强的自校验）。

用法：python design_work/c2_calibration.py   （须先跑 enrich 使 graph.json 富化）
产物：design_work/c2_calibration.json
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
GRAPH = os.path.join(ROOT, "output", "graph.json")
DATASET = os.path.join(ROOT, "eval", "dataset.jsonl")
OUT = os.path.join(ROOT, "design_work", "c2_calibration.json")

if SRC not in sys.path:
    sys.path.insert(0, SRC)

from repograph.models import GraphStore                        # noqa: E402
from repograph.retrieve.topic import topic_recall, build_corpus_index  # noqa: E402

MARGINS = [0.10, 0.15, 0.20, 0.25]
MIN_SCORES = [0.5, 1.0, 1.5, 2.0]
AUTOPICK = [True, False]
TOP_K = 8
OVER_ASK_CAP = 0.2


def load_rows(subsets):
    rows = []
    with open(DATASET, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                if r["subset"] in subsets:
                    rows.append(r)
    return rows


def gold_equiv_ids(gold_id, edges):
    eq = {gold_id}
    for e in edges:
        if e["type"] in ("IMPLEMENTS", "DESCRIBES"):
            if e["dst"] == gold_id:
                eq.add(e["src"])
            if e["src"] == gold_id:
                eq.add(e["dst"])
    return eq


def eval_cell(rows, store, index, edges, margin, min_score, autopick):
    h1 = h3 = over = 0
    per_q = []
    for r in rows:
        rec = topic_recall(store, r["question"], top_k=TOP_K, index=index, min_score=min_score)
        s1 = rec[0]["score"] if rec else 0.0
        s2 = rec[1]["score"] if len(rec) >= 2 else 0.0
        over_ask = (len(rec) >= 2 and s1 > 0 and (s1 - s2) / s1 < margin)
        over += int(over_ask)
        if not rec:
            anchors = []
        elif len(rec) == 1:
            anchors = [rec[0]["node_id"]] if autopick else []
        else:
            anchors = [x["node_id"] for x in rec]
        eq = gold_equiv_ids(r["gold_entity"], edges)
        hit1 = bool(eq & set(anchors[:1]))
        hit3 = bool(eq & set(anchors[:3]))
        h1 += hit1
        h3 += hit3
        per_q.append({"id": r["id"], "n_cand": len(rec), "s1": round(s1, 3),
                      "s2": round(s2, 3), "over_ask": over_ask,
                      "hit@1": hit1, "hit@3": hit3,
                      "top3": [x["node_id"].rsplit("::", 1)[-1][:22] for x in rec[:3]]})
    n = len(rows)
    return {"hit@1": round(h1 / n, 4), "hit@3": round(h3 / n, 4),
            "over_ask_rate": round(over / n, 4), "per_q": per_q}


def main():
    store = GraphStore.load(GRAPH)
    edges = json.load(open(GRAPH, encoding="utf-8"))["edges"]
    dev = load_rows(("FZ_dev",))
    index = build_corpus_index(store)   # 富化后语料（含 Function/Class zh_desc/zh_aliases）

    grid = []
    for ms in MIN_SCORES:
        for mg in MARGINS:
            for ap in AUTOPICK:
                cell = eval_cell(dev, store, index, edges, mg, ms, ap)
                grid.append({"min_score": ms, "rel_margin": mg, "single_weak_autopick": ap,
                             "hit@1": cell["hit@1"], "hit@3": cell["hit@3"],
                             "over_ask_rate": cell["over_ask_rate"],
                             "feasible": cell["over_ask_rate"] <= OVER_ASK_CAP})

    feasible = [c for c in grid if c["feasible"]]
    ratifiable = [c for c in feasible if c["hit@1"] > 0]
    if ratifiable:
        best = sorted(ratifiable, key=lambda c: (-c["hit@1"], -c["hit@3"], -c["min_score"],
                                                 -c["rel_margin"], c["single_weak_autopick"]))[0]
        ratified = True
    else:
        best = None
        ratified = False
    least_bad = sorted(grid, key=lambda c: (-c["hit@1"], -c["hit@3"], -c["min_score"],
                                            -c["rel_margin"], c["single_weak_autopick"]))[0]

    # 参考读数：某代表单元逐题（min_score=1.0, rel_margin=0.15）
    ref = eval_cell(dev, store, index, edges, 0.15, 1.0, False)

    out = {
        "meta": {"subset": "FZ_dev", "n": len(dev), "source": "C2 富化后真实 topic_recall",
                 "dims": {"rel_margin": MARGINS, "min_score": MIN_SCORES,
                          "single_weak_autopick": ["on", "off"]},
                 "objective": "max hit@1", "constraint": "over_ask_rate<=0.2"},
        "grid": grid,
        "n_feasible": len(feasible), "n_ratifiable": len(ratifiable),
        "ratified": ratified, "selected": best, "least_bad_cell": least_bad,
        "transition_rule_retired": ratified,
        "ref_cell_per_q": ref["per_q"],
        "hit1_range": [min(c["hit@1"] for c in grid), max(c["hit@1"] for c in grid)],
        "hit3_range": [min(c["hit@3"] for c in grid), max(c["hit@3"] for c in grid)],
        "over_ask_range": [min(c["over_ask_rate"] for c in grid),
                           max(c["over_ask_rate"] for c in grid)],
    }
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("网格单元:", len(grid), " 可行(≤0.2):", len(feasible),
          " 可批准(可行且hit@1>0):", len(ratifiable))
    print("ratified:", ratified, " selected:", best)
    print("hit@1 范围:", out["hit1_range"], " hit@3 范围:", out["hit3_range"],
          " over_ask 范围:", out["over_ask_range"])
    print("least_bad(仅参考):", least_bad)
    print("写出:", os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
