# -*- coding: utf-8 -*-
"""V1 步骤3-4：FZ 三配置 hit@3 实测 + 基线保真自校验。

三配置（min_score=1.0，top_k=8，与 topic_recall 默认一致）：
  base     : base 语料（无卡片）+ ngram(zh_terms)   —— 应≈gate 红值 hit@3=0.1
  a_ngram  : 卡片增广语料 + ngram(zh_terms)
  b_jieba  : 卡片增广语料 + jieba

保真自校验：base 配置（我复刻的 BM25）逐题 node_id 序列必须等于真实
repograph.retrieve.topic.topic_recall —— 等价则 (a)/(b) 数值可信。

命中判定复刻 eval/gate.py（gold_entity + IMPLEMENTS/DESCRIBES 1 跳等价集，anchors=
recall node_id 有序，hit@k=gold_equiv∩anchors[:k]）。FZ_dev 用于裁定；FZ_test 仅
作冻结留出读数（不参与任何裁定/调参）。
"""
from __future__ import annotations

import json
import os
import sys

import rg_exp_lib as L

sys.path.insert(0, L.SRC)
from repograph.retrieve.topic import topic_recall            # noqa: E402  真实实现（保真基准）
from repograph.retrieve.context import link_entities         # noqa: E402  路由守卫

OUT = os.path.join(L.ROOT, "design_work", "v1_tokenize_eval.json")
MIN_SCORE = 1.0
TOP_K = 8


def eval_config(rows, index, tokenizer_name, edges):
    tok = L.TOKENIZERS[tokenizer_name]
    per_q = []
    h1 = h3 = 0
    for r in rows:
        rec = L.recall(index, r["question"], tok, top_k=TOP_K, min_score=MIN_SCORE)
        anchors = [x["node_id"] for x in rec]
        eq = L.gold_equiv_ids(r["gold_entity"], edges)
        hit1 = bool(eq & set(anchors[:1]))
        hit3 = bool(eq & set(anchors[:3]))
        h1 += hit1
        h3 += hit3
        per_q.append({"id": r["id"], "hit@1": hit1, "hit@3": hit3,
                      "anchors_top3": anchors[:3],
                      "top3_detail": [(x["node_id"].rsplit("::", 1)[-1][:24],
                                       x["score"]) for x in rec[:3]]})
    n = len(rows)
    return {"n": n, "hit@1": round(h1 / n, 4), "hit@3": round(h3 / n, 4),
            "hit@1_n": h1, "hit@3_n": h3, "per_q": per_q}


def fidelity_check(rows, base_index, edges):
    """base(我的BM25) 逐题 node_id 序列 vs 真实 topic_recall。返回 (all_match, diffs)。"""
    store = L.load_store()
    diffs = []
    for r in rows:
        mine = [x["node_id"] for x in L.recall(base_index, r["question"],
                                               L.ngram_terms, top_k=TOP_K, min_score=MIN_SCORE)]
        real = [x["node_id"] for x in topic_recall(store, r["question"], top_k=TOP_K)]
        if mine != real:
            diffs.append({"id": r["id"], "mine": mine[:5], "real": real[:5]})
    return (len(diffs) == 0), diffs


def route_guard(rows):
    """确认 FZ 题 link_entities 恒空（否则真实系统走 symbol 而非 topic）。"""
    store = L.load_store()
    hits = []
    for r in rows:
        linked = link_entities(store, r["question"])
        if linked:
            hits.append({"id": r["id"], "linked": [x["entity_id"] for x in linked]})
    return hits


def main():
    store = L.load_store()
    edges = L.load_graph_raw()["edges"]
    dev = L.load_dataset(("FZ_dev",))
    test = L.load_dataset(("FZ_test",))

    cards, cmeta = L.load_accepted_cards()
    if cmeta.get("blocked"):
        print("[BLOCKED] v1_cards.json 记录网关不可用 → V1 判定 blocked，不出分词裁定。")
        json.dump({"blocked": True, "cards_meta": cmeta}, open(OUT, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        return 3
    print("接受卡片数:", len(cards), " | 卡片 meta:", {k: cmeta.get(k) for k in
          ("n_accepted", "n_discarded", "n_netfail", "n_targets")})

    base_docs = L.build_base_docs(store)
    aug_docs, aug_info = L.augment_with_cards(base_docs, cards)
    print("base 文档数:", len(base_docs), " | 增广后:", len(aug_docs), aug_info)

    # 索引
    base_idx = L.build_index(base_docs, L.ngram_terms)
    a_idx = L.build_index(aug_docs, L.ngram_terms)
    jieba_ok = L.jieba_available()
    b_idx = L.build_index(aug_docs, L.jieba_terms) if jieba_ok else None

    # 保真自校验 + 路由守卫
    fid_ok, diffs = fidelity_check(dev + test, base_idx, edges)
    guard = route_guard(dev + test)
    print("保真自校验(base==真实topic_recall):", "PASS" if fid_ok else "FAIL", diffs[:3])
    print("路由守卫(FZ link_entities 应空):", "OK" if not guard else ("非空! " + str(guard)))

    results = {}
    for name, rows in (("FZ_dev", dev), ("FZ_test", test)):
        cfgs = {
            "base": eval_config(rows, base_idx, "ngram", edges),
            "a_ngram": eval_config(rows, a_idx, "ngram", edges),
        }
        cfgs["b_jieba"] = (eval_config(rows, b_idx, "jieba", edges)
                           if jieba_ok else {"skipped": "jieba 未安装"})
        results[name] = cfgs

    out = {
        "meta": {"min_score": MIN_SCORE, "top_k": TOP_K,
                 "cards_accepted": len(cards), "cards_meta": cmeta,
                 "corpus": {"base_docs": len(base_docs), "aug_docs": len(aug_docs), **aug_info},
                 "jieba": jieba_ok,
                 "fidelity_base_eq_real": fid_ok, "fidelity_diffs": diffs,
                 "route_guard_link_hits": guard},
        "results": results,
    }
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 控制台摘要
    for name in ("FZ_dev", "FZ_test"):
        c = results[name]
        b = c["b_jieba"].get("hit@3", "—") if isinstance(c["b_jieba"], dict) else "—"
        print("\n[%s] hit@3  base=%s  a_ngram=%s  b_jieba=%s   (hit@1: base=%s a=%s b=%s)" % (
            name, c["base"]["hit@3"], c["a_ngram"]["hit@3"], b,
            c["base"]["hit@1"], c["a_ngram"]["hit@1"],
            c["b_jieba"].get("hit@1", "—") if isinstance(c["b_jieba"], dict) else "—"))
    print("\n写出:", os.path.relpath(OUT, L.ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
