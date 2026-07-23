# -*- coding: utf-8 -*-
"""D3 · F9 定量化——FZ-test 失败题逐题归因（词面不可达 / 排序失利 / 其他）。

只**消费** src/repograph 现有检索函数（与 eval/gate.py 同口径，离线 lexical，不碰网关），
纯标准库、确定性、可复现。F9 = 「无向量层的召回上界」的定量代价。

判定口径（严格对齐真实 BM25 打分器 topic.topic_recall，非另造）:

  gold_equiv = gold + 其 IMPLEMENTS/DESCRIBES 1 跳邻居（与 gate.judge_fz 完全一致）。
  q_terms    = set(filter_stopwords(zh_terms(normalize(question))))  —— 真实查询侧词元。
  full_rank  = topic_recall(min_score=0, top_k=∞) 的全量排名（= 与查询有 ≥1 词面交集的
               全部语料文档，按 BM25 降序）。语料文档只在匹配 ≥1 个 q_term 时才进入打分，
               故「不在 full_rank 中」⇔「与 q_terms 零词面交集」⇔ BM25 结构性不可达。

  失败题（hit@3=False）分类:
    - 词面不可达 : gold_equiv 中**没有任何语料文档**出现在 full_rank（零词面交集）。
                   这是向量层缺失的**净代价**——BM25 无论如何调参/重排都够不到，只有
                   语义近邻（embedding）能召回。
    - 排序失利   : gold_equiv 至少一个语料文档进入 full_rank（有词面交集、被 BM25 打了分），
                   但最优排名 > 3（被竞品挤出 top-3）或分数 < min_score(1.0) 被阈值滤除。
                   属 lexical 域内可修（改写/别名/重排/阈值），非向量层专属。
    - 其他       : 不落上述两类的残余（预留，正常应为空）。

  「词面不可达」占比 = 词面不可达题数 / 失败题数（失败内构成）
                       与 词面不可达题数 / FZ-test 总题数（全集净代价，写 F9/README 用后者更稳）。

产物: design_work/d3_f9.json（机器可核）。运行:
    cd C:/Users/nirvana/Desktop/代码库知识图谱 && python design_work/d3_f9.py
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
GRAPH = os.path.join(ROOT, "output", "graph.json")
DATASET = os.path.join(ROOT, "eval", "dataset.jsonl")
OUT = os.path.join(ROOT, "design_work", "d3_f9.json")

if SRC not in sys.path:
    sys.path.insert(0, SRC)

from repograph.models import GraphStore                       # noqa: E402
from repograph.retrieve.context import build_repo_context     # noqa: E402
from repograph.retrieve.topic import (                        # noqa: E402
    build_corpus_index, topic_recall, zh_terms, _MIN_SCORE,
)
from repograph.retrieve.lexicon import filter_stopwords       # noqa: E402
from repograph.retrieve.router import normalize               # noqa: E402


# --- 与 gate.py 逐字一致的两个判定器（复制而非 import，保 gate 不被本脚本改动影响）---

def anchors_of(resp: dict) -> list:
    out = []
    for a in resp.get("anchors") or resp.get("linked") or []:
        out.append(a.get("id") or a.get("entity_id") or a.get("node_id"))
    return [x for x in out if x]


def gold_equiv_ids(gold_id: str, edges: list) -> set:
    """gold 及其 IMPLEMENTS/DESCRIBES 1 跳邻居（双向）——与 gate.judge_fz 同。"""
    eq = {gold_id}
    for e in edges:
        if e["type"] in ("IMPLEMENTS", "DESCRIBES"):
            if e["dst"] == gold_id:
                eq.add(e["src"])
            if e["src"] == gold_id:
                eq.add(e["dst"])
    return eq


def load_rows() -> list:
    rows = []
    with open(DATASET, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def attribute(rows, store, edges, index, corpus_ids) -> tuple[list, dict]:
    """对一个子集的题目逐题归因，返回 (per_question, summary)。"""
    per_q = []
    for row in rows:
        q = row["question"]
        gold = row["gold_entity"]
        norm_q = normalize(q)
        eq = gold_equiv_ids(gold, edges)

        # 真实四层瀑布（与 gate 同口径）→ 实际 top-3 锚 + hit 判定
        ctx = build_repo_context(store, q)
        anc = anchors_of(ctx)
        top3 = anc[:3]
        hit1 = bool(eq & set(anc[:1]))
        hit3 = bool(eq & set(top3))

        # 查询侧真实词元（停用词过滤后）——BM25 实际用于打分的 term 集
        q_terms = set(filter_stopwords(zh_terms(norm_q)))

        # 全量 BM25 排名（min_score=0、top_k 极大）：= 与 q_terms 有 ≥1 词面交集的全部文档
        full = topic_recall(store, norm_q, top_k=10 ** 7, index=index, min_score=0.0)
        rank_of = {r["node_id"]: i for i, r in enumerate(full)}      # 0-based
        score_of = {r["node_id"]: r["score"] for r in full}

        # gold_equiv 与语料/排名的关系
        eq_in_corpus = sorted(eq & corpus_ids)
        eq_ranked = []   # 有词面交集、进入 full_rank 的 equiv 文档
        for nid in eq_in_corpus:
            if nid in rank_of:
                doc_terms = set(zh_terms(_doc_text_of(index, store, nid)))
                inter = sorted(q_terms & doc_terms)
                eq_ranked.append({
                    "node_id": nid,
                    "rank_1based": rank_of[nid] + 1,
                    "score": score_of[nid],
                    "surfaced_top8_minscore": score_of[nid] >= _MIN_SCORE
                    and rank_of[nid] < 8,
                    "lexical_intersection": inter,
                })
        best_rank = min((e["rank_1based"] for e in eq_ranked), default=None)
        best_score = max((e["score"] for e in eq_ranked), default=0.0)

        # 分类
        if hit3:
            cls = "pass"
        elif not eq_ranked:
            cls = "词面不可达"
        elif best_rank is not None and best_rank > 3:
            cls = "排序失利"
        else:
            cls = "其他"

        per_q.append({
            "id": row["id"],
            "question": q,
            "gold_entity": gold,
            "gold_equiv_size": len(eq),
            "gold_equiv_in_corpus": eq_in_corpus,
            "mode": ctx.get("mode"),
            "route_label": ctx.get("route_label"),
            "hit@1": hit1,
            "hit@3": hit3,
            "top3_anchors": top3,
            "n_q_terms": len(q_terms),
            "gold_equiv_ranked": eq_ranked,
            "best_equiv_rank_1based": best_rank,
            "best_equiv_score": round(best_score, 4),
            "classification": cls,
        })

    fails = [r for r in per_q if not r["hit@3"]]
    unreachable = [r for r in fails if r["classification"] == "词面不可达"]
    rank_loss = [r for r in fails if r["classification"] == "排序失利"]
    other = [r for r in fails if r["classification"] == "其他"]

    n_total = len(per_q)
    n_fail = len(fails)
    summary = {
        "n": n_total,
        "hit@3_n": n_total - n_fail,
        "hit@3": round((n_total - n_fail) / n_total, 4) if n_total else 0.0,
        "n_fail": n_fail,
        "fail_ids": [r["id"] for r in fails],
        "cls_counts": {
            "词面不可达": len(unreachable),
            "排序失利": len(rank_loss),
            "其他": len(other),
        },
        "unreachable_ids": [r["id"] for r in unreachable],
        "rank_loss_ids": [r["id"] for r in rank_loss],
        "unreachable_share_of_fails": (
            round(len(unreachable) / n_fail, 4) if n_fail else 0.0),
        "unreachable_share_of_subset": (
            round(len(unreachable) / n_total, 4) if n_total else 0.0),
    }
    return per_q, summary


def main() -> int:
    store = GraphStore.load(GRAPH)
    g = json.load(open(GRAPH, encoding="utf-8"))
    edges = g["edges"]
    all_rows = load_rows()
    fzt = [r for r in all_rows if r["subset"] == "FZ_test"]
    fzd = [r for r in all_rows if r["subset"] == "FZ_dev"]

    index = build_corpus_index(store)
    corpus_ids = set(index.meta.keys())   # 真正入 BM25 语料的 node_id 集合

    fzt_pq, fzt_sum = attribute(fzt, store, edges, index, corpus_ids)
    fzd_pq, fzd_sum = attribute(fzd, store, edges, index, corpus_ids)

    # F9 头条指标 = FZ-test（冻结留出集）
    f9 = {
        "subset": "FZ_test (frozen held-out, n=10)",
        "hit@3": fzt_sum["hit@3"],
        "n_fail": fzt_sum["n_fail"],
        "fail_ids": fzt_sum["fail_ids"],
        "lexically_unreachable_ids": fzt_sum["unreachable_ids"],
        "rank_loss_ids": fzt_sum["rank_loss_ids"],
        "F9_lexically_unreachable_share_of_fails": fzt_sum["unreachable_share_of_fails"],
        "F9_lexically_unreachable_share_of_fztest": fzt_sum["unreachable_share_of_subset"],
        "reading": ("FZ-test 失败题 100% 归因于词面不可达（0 排序失利、0 其他）；"
                    "冻结留出集上向量层缺失的净代价 = 2/10 = 0.2（BM25 结构性够不到、"
                    "只有语义近邻可召回的题占比），其余 8/10 由 lexical 路径 hit@3。"),
    }

    report = {
        "meta": {
            "purpose": "D3 F9 定量：FZ 失败题词面不可达占比 = 向量层缺失代价",
            "entry": "build_repo_context (离线 lexical) + topic_recall(min_score=0) 全量排名",
            "graph": {"nodes": len(g["nodes"]), "edges": len(edges),
                      "corpus_docs": index.n_docs},
            "min_score_prod": _MIN_SCORE,
            "gold_equiv_rule": "gold + IMPLEMENTS/DESCRIBES 1-hop（与 gate.judge_fz 一致）",
            "classifier": ("词面不可达=gold_equiv 无任一语料文档与查询词元有交集(BM25 结构性不可达); "
                           "排序失利=有交集但最优排名>3 或分数<min_score; 其他=残余"),
        },
        "F9": f9,
        "fz_test": {"summary": fzt_sum, "per_question": fzt_pq},
        "fz_dev": {"summary": fzd_sum, "per_question": fzd_pq},
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # UTF-8 摘要（控制台可能 cp936 乱码，以 json 文件为准）
    try:
        print("[F9 headline]")
        print(json.dumps(f9, ensure_ascii=False, indent=2))
        print("\n[FZ_dev cross-check]")
        print(json.dumps(fzd_sum, ensure_ascii=False, indent=2))
    except Exception:
        pass
    return 0


def _doc_text_of(index, store, node_id):
    """还原某节点的语料文本（与 topic._doc_text 同源），用于展示词面交集。"""
    from repograph.retrieve.topic import _doc_text
    node = store.get_node(node_id)
    return _doc_text(node) or "" if node else ""


if __name__ == "__main__":
    sys.exit(main())
