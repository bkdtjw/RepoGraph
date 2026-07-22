# -*- coding: utf-8 -*-
"""V1/V0 实验共享库：语料装配 + BM25（精确复刻 topic.py）+ 卡片增广 + 分词器切换 + 命中判定。

不改任何 src；从 repograph.retrieve.topic 复用 zh_terms（ngram 方案），BM25 打分
逐公式复刻 topic.py（k1=1.5,b=0.75,IDF 现算,min_score 过滤,同排序），确保
「基线（base 语料 + zh_terms + min_score=1.0）」严格等于真实 topic_recall（自校验）。

命中判定复刻 eval/gate.py：anchors=topic recall 的 node_id 有序；gold_equiv=gold 及其
IMPLEMENTS/DESCRIBES 1 跳（双向）；hit@k = gold_equiv ∩ anchors[:k]。
"""
from __future__ import annotations

import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
GRAPH = os.path.join(ROOT, "output", "graph.json")
DATASET = os.path.join(ROOT, "eval", "dataset.jsonl")
CARDS = os.path.join(ROOT, "design_work", "v1_cards.json")

if SRC not in sys.path:
    sys.path.insert(0, SRC)

from repograph.models import GraphStore                     # noqa: E402
from repograph.retrieve.topic import zh_terms               # noqa: E402  ngram 方案（现有分词）

_K1 = 1.5
_B = 0.75


# --------------------------------------------------------------------------- #
# 分词器
# --------------------------------------------------------------------------- #

def ngram_terms(text):
    """方案(a)：直接用现有 topic.zh_terms（CJK 2/3/4-gram 滑窗 + 英文词）。"""
    return zh_terms(text)


_jieba = None
_jieba_ok = None
import re as _re
_EN = _re.compile(r"[a-z0-9]+")
_CJK = _re.compile(r"[一-鿿㐀-䶿]")


def jieba_available():
    global _jieba, _jieba_ok
    if _jieba_ok is None:
        try:
            import jieba  # noqa
            jieba.setLogLevel(20)
            _jieba = jieba
            _jieba_ok = True
        except Exception:
            _jieba_ok = False
    return _jieba_ok


def jieba_terms(text):
    """方案(b)：jieba 精确切词。中文取 jieba 词；英文/数字段同 zh_terms 规则
    （len>=2 且非纯数字）；丢弃纯标点/空白。索引侧与查询侧同法。"""
    if not text:
        return []
    if not jieba_available():
        raise RuntimeError("jieba 未安装")
    out = []
    for tok in _jieba.lcut(text.lower()):
        t = tok.strip()
        if not t:
            continue
        if _EN.fullmatch(t):
            if len(t) >= 2 and not t.isdigit():
                out.append(t)
        elif _CJK.search(t):
            out.append(t)          # 保留 jieba 切出的中文词（含单字词）
    return out


TOKENIZERS = {"ngram": ngram_terms, "jieba": jieba_terms}


# --------------------------------------------------------------------------- #
# 语料装配（base 部分逐字复刻 topic.py 的 _concept_text/_doc_text/_corpus_nodes）
# --------------------------------------------------------------------------- #

_DOC_LABELS = ("Concept", "Commit", "Module")


def _concept_text(node):
    parts = [node.get("name", ""), node.get("description", "")]
    parts.extend(node.get("aliases") or [])
    for ev in node.get("evidence") or []:
        quote = (ev or {}).get("quote")
        if quote:
            parts.append(quote)
    return " ".join(p for p in parts if p)


def _doc_text(node):
    label = node["label"]
    if label == "Concept":
        return _concept_text(node)
    if label == "Commit":
        msg = node.get("message")
        return msg if (msg and msg.strip()) else None
    if label == "Module":
        doc = node.get("docstring")
        return doc if (doc and doc.strip()) else None
    return None


def build_base_docs(store):
    """返回 {doc_id: {'label','text'}}，与 topic._corpus_nodes 完全一致。"""
    docs = {}
    for label in _DOC_LABELS:
        for node in store.nodes(label):
            text = _doc_text(node)
            if text:
                docs[node["id"]] = {"label": node["label"], "text": text}
    return docs


def load_accepted_cards():
    """读 v1_cards.json，返回 (accepted_list, meta)。blocked 时 accepted=[]。"""
    if not os.path.exists(CARDS):
        return [], {"missing": True}
    d = json.load(open(CARDS, encoding="utf-8"))
    if d.get("blocked"):
        return [], {"blocked": True, **d.get("meta", {})}
    acc = [c for c in d.get("cards", []) if c.get("accepted") and c.get("card_text")]
    return acc, d.get("meta", {})


def augment_with_cards(base_docs, cards):
    """卡片入语料：Concept 卡片 append 到既有概念文档；Function/Class 卡片新建文档
    （doc_id=实体 id）。返回新 docs（不改 base_docs）。"""
    docs = {k: dict(v) for k, v in base_docs.items()}
    n_append = n_new = 0
    for c in cards:
        cid, label, text = c["id"], c["label"], c["card_text"]
        if label == "Concept":
            if cid in docs:
                docs[cid]["text"] = docs[cid]["text"] + " " + text
                n_append += 1
            else:
                docs[cid] = {"label": "Concept", "text": text}
                n_new += 1
        else:  # Function / Class：base 语料本无，新建文档
            if cid in docs:
                docs[cid]["text"] = docs[cid]["text"] + " " + text
                n_append += 1
            else:
                docs[cid] = {"label": label, "text": text}
                n_new += 1
    return docs, {"appended": n_append, "new_docs": n_new}


# --------------------------------------------------------------------------- #
# BM25 索引 + 召回（复刻 topic.py）
# --------------------------------------------------------------------------- #

def build_index(docs, tokenizer):
    postings, df, doclen, meta = {}, {}, {}, {}
    total = 0
    for doc_id, d in docs.items():
        tf = {}
        for term in tokenizer(d["text"]):
            tf[term] = tf.get(term, 0) + 1
        if not tf:
            continue
        meta[doc_id] = {"label": d["label"]}
        doclen[doc_id] = sum(tf.values())
        total += doclen[doc_id]
        for term, freq in tf.items():
            b = postings.get(term)
            if b is None:
                postings[term] = {doc_id: freq}
                df[term] = 1
            else:
                b[doc_id] = freq
                df[term] = len(b)
    n = len(doclen)
    return {"postings": postings, "df": df, "doclen": doclen, "meta": meta,
            "n_docs": n, "avgdl": (total / n) if n else 0.0}


def _idf(n_docs, df):
    return math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))


def recall(index, question, tokenizer, top_k=8, min_score=1.0):
    """返回 [{node_id,label,score,matched_terms}]，排序/过滤同 topic_recall。"""
    if index["n_docs"] == 0:
        return []
    q_terms = set(tokenizer(question))
    if not q_terms:
        return []
    scores, matched = {}, {}
    avgdl = index["avgdl"]
    for term in q_terms:
        bucket = index["postings"].get(term)
        if not bucket:
            continue
        idf = _idf(index["n_docs"], index["df"][term])
        for doc_id, freq in bucket.items():
            dl = index["doclen"][doc_id]
            denom = freq + _K1 * (1.0 - _B + _B * dl / avgdl)
            scores[doc_id] = scores.get(doc_id, 0.0) + idf * (freq * (_K1 + 1.0)) / denom
            matched.setdefault(doc_id, set()).add(term)
    ranked = sorted((d for d, s in scores.items() if s >= min_score),
                    key=lambda d: (-scores[d], d))[:top_k]
    return [{"node_id": d, "label": index["meta"][d]["label"],
             "score": round(scores[d], 4),
             "matched_terms": sorted(matched[d]),
             "idf_max": round(max(_idf(index["n_docs"], index["df"][t])
                                  for t in matched[d]), 4)}
            for d in ranked]


# --------------------------------------------------------------------------- #
# 命中判定（复刻 gate.py）
# --------------------------------------------------------------------------- #

def load_graph_raw():
    return json.load(open(GRAPH, encoding="utf-8"))


def gold_equiv_ids(gold_id, edges):
    eq = {gold_id}
    for e in edges:
        if e["type"] in ("IMPLEMENTS", "DESCRIBES"):
            if e["dst"] == gold_id:
                eq.add(e["src"])
            if e["src"] == gold_id:
                eq.add(e["dst"])
    return eq


def load_dataset(subsets=("FZ_dev",)):
    rows = []
    with open(DATASET, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                if r["subset"] in subsets:
                    rows.append(r)
    return rows


def load_store():
    return GraphStore.load(GRAPH)
