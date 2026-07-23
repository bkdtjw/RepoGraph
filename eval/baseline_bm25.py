# -*- coding: utf-8 -*-
"""组 A 基线：BM25-only 无图谱检索（D-R2 / 计划书 §4 Phase D · D2）。

单一自变量对照：与组 B（``build_repo_context`` 完整 v0.3 混合检索）**共用**
同一分词器（``topic.zh_terms``）与同一倒排语料（``topic.build_corpus_index``——
含 C2 富化的 Function/Class 双语卡片，语料层已拉平），从而把「图谱结构 + 检索
编排」的贡献从「底层 BM25 召回」中隔离出来。

组 A 相对组 B **刻意抹除**的全部能力（口径写死，报告须明示）：
  - 无 S1 五路路由（route）           - 无 link_entities 符号链接
  - 无 impact_analysis 影响面闭包      - 无 IMPLEMENTS/DESCRIBES 概念展开
  - 无 repo_card / meta 概览            - 无 S7 前提校验（premise_flags）
  - 无消歧协议（needs_disambiguation） - 无 S2 改写二次召回

检索 = 纯 BM25（k1=1.5, b=0.75，与 topic.py 同式）top-``k`` 文档，取命中文档的
**原文**（即建索引所用的 ``_doc_text``）拼成上下文。为使基线拿满 top-k 预算、
不被 v0.3 的精度门（``_MIN_SCORE=1.0``）反向掣肘，此处 ``min_score=0.0`` —— 即
「取分数最高的 k 篇」纯截断，倾向于**强化**基线（反稻草人）。

只消费 src/repograph 现有检索函数，绝不修改 src；纯标准库。输出接口对齐
``build_repo_context``：``{mode:'bm25_only', linked, context_text, stats, route_label}``，
``linked[].node_id`` 承载 BM25 命中锚点，供门禁 ``anchors_of`` 消费。
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from repograph.models import GraphStore                       # noqa: E402
from repograph.retrieve.topic import (                        # noqa: E402
    CorpusIndex, build_corpus_index, topic_recall,
    _doc_text, _doc_name,
)

# 基线上下文字符预算：与组 B build_repo_context 默认 budget_chars 对齐，保证两组
# 上下文容量同量级（差异只来自内容组织，不来自预算）。
_BUDGET_CHARS = 6000
_TOP_K = 8


def build_bm25_index(store: GraphStore) -> CorpusIndex:
    """复用组 B 同一倒排语料（Concept/Commit/Module/Function·Class 双语卡片）。"""
    return build_corpus_index(store)


def build_bm25_context(
    store: GraphStore,
    question: str,
    top_k: int = _TOP_K,
    budget_chars: int = _BUDGET_CHARS,
    index: CorpusIndex | None = None,
) -> dict:
    """组 A 检索：纯 BM25 top-``k`` 文档原文拼接为上下文。

    返回与 ``build_repo_context`` 同构的 dict：``mode='bm25_only'``、
    ``linked``=命中文档（node_id/label/name/score）、``context_text``=原文拼接、
    ``stats``（symbols 恒 0，topics=命中数，commits/concepts 按命中计）、
    ``route_label='bm25_only'``（自描述，绝不伪装成 meta/global，避免门禁误绿）。
    """
    if index is None:
        index = build_bm25_index(store)

    # 纯 BM25 top-k：min_score=0.0 去掉 v0.3 精度门，拿满 top-k（反稻草人，强化基线）
    hits = topic_recall(store, question, top_k=top_k, index=index, min_score=0.0)

    lines: list[str] = ["【BM25 Top-8 文档】（来源: 纯 BM25 检索，无图谱结构 / 无符号链接 / 无影响面）"]
    n_commit = 0
    n_concept = 0
    for i, h in enumerate(hits, 1):
        node = store.get_node(h["node_id"])
        if node is None:
            continue
        label = h["label"]
        if label == "Commit":
            n_commit += 1
        elif label == "Concept":
            n_concept += 1
        text = _doc_text(node) or ""
        name = _doc_name(node)
        # 附路径/文件定位（若有），使原文可回溯到真实位置——仍属该文档已有字段，非图谱展开
        loc = node.get("path") or node.get("file") or ""
        loc_s = f"  ⟨{loc}⟩" if loc else ""
        lines.append(f"[{i}] [{label}] {name}{loc_s}  (BM25={h['score']})")
        lines.append(f"    {text}")

    context_text = _apply_budget(lines, budget_chars)

    linked = [
        {"node_id": h["node_id"], "label": h["label"],
         "name": h["name"], "score": h["score"]}
        for h in hits
    ]
    stats = {
        "symbols": 0,
        "topics": len(hits),
        "impact_callers": 0,
        "commits": n_commit,
        "concepts": n_concept,
    }
    return {
        "mode": "bm25_only",
        "route_label": "bm25_only",
        "linked": linked,
        "context_text": context_text,
        "stats": stats,
    }


def _apply_budget(lines: list[str], budget_chars: int) -> str:
    """按行累加到 budget_chars 为止（与 context.py 同策略）；超预算截断并提示。"""
    out: list[str] = []
    total = 0
    truncated = False
    for ln in lines:
        piece = ln + "\n"
        if total + len(piece) > budget_chars:
            truncated = True
            break
        out.append(piece)
        total += len(piece)
    text = "".join(out)
    if truncated:
        text += "…（上下文因预算截断，仅保留高分文档）"
    return text


if __name__ == "__main__":
    # 独立巡检：python eval/baseline_bm25.py "你的问题"
    import json
    q = sys.argv[1] if len(sys.argv) > 1 else "终止流程是怎么处理的？"
    st = GraphStore.load(os.path.join(_ROOT, "output", "graph.json"))
    ctx = build_bm25_context(st, q)
    print(json.dumps({k: v for k, v in ctx.items() if k != "context_text"},
                     ensure_ascii=False, indent=2))
    print("---- context_text ----")
    print(ctx["context_text"])
