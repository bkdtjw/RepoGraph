# -*- coding: utf-8 -*-
"""V0 步骤6-7：分带参数网格搜索（FZ-dev 10 题，用 V1 胜出配置的卡片增广语料）。

维度（D-N1 新分数语义：离散/BM25 边际，原 τ_hi/τ_lo 网格作废）：
  rel_margin τ    ∈ {0.10, 0.15, 0.20, 0.25}   —— topic Top-2 相对边际 (s1-s2)/s1，<τ 判进消歧
  min_score       ∈ {0.5, 1.0, 1.5, 2.0}        —— topic_recall 分阈
  single_weak     ∈ {on, off}                    —— 单一弱候选（len==1）是否自动锚定（§4.6）
目标 max hit@1，约束 消歧触发率（过问代理）≤ 0.2。

消歧触发（过问代理）= 存在 Top-2 且 (s1-s2)/s1 < τ。hit 判定复刻 gate.py。
D-N1 绝对证据下限单列诊断：对每题 Top-1 候选记 idf_max 与是否 gold 命中，
据此给「≥1 高 IDF 内容词」的阈值建议（校准生效即替换「仅档≥80」过渡规则）。

用法：python design_work/v0_calibration.py [ngram|jieba]   （默认读 v1 裁定文件）
"""
from __future__ import annotations

import json
import os
import sys

import rg_exp_lib as L

OUT = os.path.join(L.ROOT, "design_work", "v0_calibration.json")
DECISION = os.path.join(L.ROOT, "design_work", "v1_decision.json")

MARGINS = [0.10, 0.15, 0.20, 0.25]
MIN_SCORES = [0.5, 1.0, 1.5, 2.0]
AUTOPICK = [True, False]
TOP_K = 8
OVER_ASK_CAP = 0.2


def winning_tokenizer():
    if len(sys.argv) > 1 and sys.argv[1] in ("ngram", "jieba"):
        return sys.argv[1]
    if os.path.exists(DECISION):
        return json.load(open(DECISION, encoding="utf-8")).get("winning_tokenizer", "ngram")
    return "ngram"


def eval_cell(rows, index, tok, edges, margin, min_score, autopick):
    h1 = h3 = over = 0
    per_q = []
    for r in rows:
        rec = L.recall(index, r["question"], tok, top_k=TOP_K, min_score=min_score)
        s1 = rec[0]["score"] if rec else 0.0
        s2 = rec[1]["score"] if len(rec) >= 2 else 0.0
        over_ask = (len(rec) >= 2 and s1 > 0 and (s1 - s2) / s1 < margin)
        over += int(over_ask)
        # 单一弱候选（len==1）受 autopick 开关约束；≥2 候选正常给锚
        if not rec:
            anchors = []
        elif len(rec) == 1:
            anchors = [rec[0]["node_id"]] if autopick else []
        else:
            anchors = [x["node_id"] for x in rec]
        eq = L.gold_equiv_ids(r["gold_entity"], edges)
        hit1 = bool(eq & set(anchors[:1]))
        hit3 = bool(eq & set(anchors[:3]))
        h1 += hit1
        h3 += hit3
        per_q.append({"id": r["id"], "n_cand": len(rec), "s1": s1, "s2": s2,
                      "over_ask": over_ask, "hit@1": hit1, "hit@3": hit3})
    n = len(rows)
    return {"hit@1": round(h1 / n, 4), "hit@3": round(h3 / n, 4),
            "over_ask_rate": round(over / n, 4),
            "hit@1_n": h1, "hit@3_n": h3, "over_ask_n": over, "per_q": per_q}


def dn1_diag(rows, index, tok, edges, min_score):
    """D-N1 诊断：每题 Top-1 的 idf_max 与是否 gold 命中，给高 IDF 阈值建议。"""
    diag = []
    for r in rows:
        rec = L.recall(index, r["question"], tok, top_k=TOP_K, min_score=min_score)
        if not rec:
            diag.append({"id": r["id"], "top1": None})
            continue
        top = rec[0]
        eq = L.gold_equiv_ids(r["gold_entity"], edges)
        diag.append({"id": r["id"], "top1": top["node_id"].rsplit("::", 1)[-1][:24],
                     "idf_max": top["idf_max"], "score": top["score"],
                     "is_gold": top["node_id"] in eq,
                     "matched": top["matched_terms"][:6]})
    hits = [d["idf_max"] for d in diag if d.get("top1") and d["is_gold"]]
    miss = [d["idf_max"] for d in diag if d.get("top1") and not d["is_gold"]]
    return {"per_q": diag,
            "idf_gold_top1": sorted(hits),
            "idf_nongold_top1": sorted(miss),
            "min_gold_idf": min(hits) if hits else None}


def main():
    tok_name = winning_tokenizer()
    if tok_name == "jieba" and not L.jieba_available():
        print("[WARN] 裁定为 jieba 但未安装，回退 ngram")
        tok_name = "ngram"
    tok = L.TOKENIZERS[tok_name]
    print("V0 校准分词器:", tok_name)

    store = L.load_store()
    edges = L.load_graph_raw()["edges"]
    dev = L.load_dataset(("FZ_dev",))

    cards, cmeta = L.load_accepted_cards()
    if cmeta.get("blocked"):
        print("[BLOCKED] 卡片不可用，V0 依赖 V1 语料，无法校准。")
        json.dump({"blocked": True}, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return 3
    base_docs = L.build_base_docs(store)
    aug_docs, _ = L.augment_with_cards(base_docs, cards)
    index = L.build_index(aug_docs, tok)

    grid = []
    for ms in MIN_SCORES:
        for mg in MARGINS:
            for ap in AUTOPICK:
                cell = eval_cell(dev, index, tok, edges, mg, ms, ap)
                grid.append({"min_score": ms, "rel_margin": mg, "single_weak_autopick": ap,
                             "hit@1": cell["hit@1"], "hit@3": cell["hit@3"],
                             "over_ask_rate": cell["over_ask_rate"],
                             "feasible": cell["over_ask_rate"] <= OVER_ASK_CAP})

    feasible = [c for c in grid if c["feasible"]]
    # 选择：仅在可行域内、且目标 hit@1>0 才批准；否则不批准（过渡规则不退休）
    least_bad = sorted(grid, key=lambda c: (-c["hit@1"], -c["hit@3"], -c["min_score"],
                                            -c["rel_margin"], c["single_weak_autopick"]))[0]
    ratifiable = [c for c in feasible if c["hit@1"] > 0]
    if ratifiable:
        best = sorted(ratifiable, key=lambda c: (-c["hit@1"], -c["hit@3"], -c["min_score"],
                                                 -c["rel_margin"], c["single_weak_autopick"]))[0]
        ratified = True
    else:
        best = None
        ratified = False
    transition_rule_retired = ratified  # 校准生效（批准）才替换「仅档≥80」过渡规则

    # D-N1 诊断在参考 min_score（选定或 least_bad）上算
    diag = dn1_diag(dev, index, tok, edges, (best or least_bad)["min_score"])

    out = {
        "meta": {"tokenizer": tok_name, "subset": "FZ_dev", "n": len(dev),
                 "dims": {"rel_margin": MARGINS, "min_score": MIN_SCORES,
                          "single_weak_autopick": ["on", "off"]},
                 "objective": "max hit@1", "constraint": "over_ask_rate<=0.2",
                 "over_ask_def": "存在Top-2且(s1-s2)/s1<rel_margin",
                 "cards_accepted": len(cards)},
        "grid": grid,
        "n_feasible": len(feasible),
        "ratified": ratified,
        "selected": best,
        "least_bad_cell": least_bad,
        "transition_rule_retired": transition_rule_retired,
        "dn1_diag": diag,
    }
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("\n网格单元数:", len(grid), " 可行(消歧率<=0.2):", len(feasible),
          " 可批准(可行且hit@1>0):", len(ratifiable))
    print("ratified:", ratified, " 过渡规则退休:", transition_rule_retired)
    print("选定参数:", best, " | least_bad(仅参考):", least_bad)
    print("D-N1: gold-top1 idf_max 集:", diag["idf_gold_top1"],
          " 非gold-top1 idf_max:", diag["idf_nongold_top1"][:6],
          " 建议高IDF下限(min gold idf):", diag["min_gold_idf"])
    print("写出:", os.path.relpath(OUT, L.ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
