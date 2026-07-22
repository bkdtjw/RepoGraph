"""主题词面召回（契约 A · L1）——确定性 BM25-lite，不碰网关。

现有 ``link_entities`` 只按**符号名词面**匹配；用户问"终止流程怎么处理的""怎么做
容错的"这类**不含具体符号名**的模糊问题时它一无所获。本模块补上"自然语言主题 →
真实图谱证据"的确定性桥梁：把图谱里 grok 语义层沉淀的 **Concept** 连同 **Commit**
message、**Module** docstring 建成 236 篇微型语料，用纯标准库 BM25-lite 召回，命中
的概念随后在 ``context.py`` 里沿 IMPLEMENTS/DESCRIBES 展开回真实函数与提交。

三个纯函数，全部确定性、可复用、零第三方依赖：

- ``zh_terms(text)``        中文无空格分词的替代：CJK 2/3/4-gram 滑窗 + 英文词（小写）。
- ``build_corpus_index``   对 236 篇文档建倒排索引 + 文档频次（供 IDF 现算）；可缓存复用。
- ``topic_recall``         BM25-lite（k1=1.5, b=0.75, IDF 现算）打分召回，返回命中节点。

只依赖 ``..models.GraphStore``；与 ``context.py`` / ``impact.py`` 不互相 import
（``context.py`` 单向 import 本模块）。
"""
from __future__ import annotations

import math
import re
from typing import Iterator, Optional

from ..models import GraphStore
from .lexicon import filter_stopwords

# ---------------------------------------------------------------------------
# BM25 参数与召回阈值
# ---------------------------------------------------------------------------

_K1 = 1.5
_B = 0.75

# 最小分阈值：BM25-lite 下，命中一个"有信息量"的词（idf≳1）经饱和后得分通常 >1；
# 只匹配到近乎无区分度的高频 gram（idf→0）会落在此线以下被滤除，避免噪声污染召回。
_MIN_SCORE = 1.0

# 语料文档单元的收录标签（v0.3 · C2 · D-11：Function/Class 双语卡片入档，仅带 zh_desc 者）。
# Function/Class 无 grok 语义描述，靠索引期网关生成的 zh_desc/zh_aliases 提供中文可召回文本；
# 未富化的函数（无 zh_desc）不入档，避免用英文 qualname 稀释语料（English 由 link_entities 覆盖）。
_DOC_LABELS = ("Concept", "Commit", "Module", "Function", "Class")

# CJK 统一表意文字（含扩展 A）——中文 term 的字符类
_CJK_RUN = re.compile(r"[一-鿿㐀-䶿]+")
# 英文 / 数字词（已小写化后匹配）
_EN_RUN = re.compile(r"[a-z0-9]+")


# ---------------------------------------------------------------------------
# zh_terms —— 中文 2/3/4-gram 滑窗 + 英文词
# ---------------------------------------------------------------------------

def zh_terms(text: str) -> list[str]:
    """把一段文本切成可用于倒排/打分的 term 列表（保留重复以承载词频）。

    中文没有空格分词，用**滑窗 n-gram**（n∈{2,3,4}）作为 term：一段连续 CJK 里
    每个长度 2/3/4 的子串各产出一个 term（长度 1 的孤立汉字单独保留，避免丢信息）。
    英文/数字按连续段切词、统一小写，长度<2 或纯数字的丢弃（纯数字无检索价值，
    且会与提交里的行号/计数噪声混淆）。查询与文档用**同一套** term 生成，保证可比。

    例：``zh_terms("终止流程")`` → ``["终止","止流","流程","终止流","止流程","终止流程"]``。
    """
    if not text:
        return []
    lowered = text.lower()
    terms: list[str] = []

    # 英文 / 数字词
    for w in _EN_RUN.findall(lowered):
        if len(w) >= 2 and not w.isdigit():
            terms.append(w)

    # 中文 CJK 段 → 2/3/4-gram 滑窗（原文大小写对中文无意义，用 lowered 亦可）
    for run in _CJK_RUN.findall(lowered):
        length = len(run)
        if length == 1:
            terms.append(run)
            continue
        for n in (2, 3, 4):
            if length < n:
                break
            for i in range(length - n + 1):
                terms.append(run[i:i + n])

    return terms


# ---------------------------------------------------------------------------
# 语料装配 —— 一个节点 = 一篇微型文档
# ---------------------------------------------------------------------------

def _concept_text(node: dict) -> str:
    """Concept 文档 = 名称 + 描述 + 别名 + zh_aliases + 首条证据引文（全部真实字段拼接）。

    ``aliases`` 为 grok 语义层原生别名（近乎全空）；``zh_aliases`` 是 C2 索引期回填的口语
    近义说法（``enrich`` 全权写入、可幂等替换），随语料入 BM25，是 FZ 口语指称召回的主要
    桥接来源（D-11 重议方向）。两者并读，原生别名与 C2 回填互不覆盖。
    """
    parts: list[str] = [node.get("name", ""), node.get("description", "")]
    parts.extend(node.get("aliases") or [])
    parts.extend(node.get("zh_aliases") or [])
    for ev in node.get("evidence") or []:
        quote = (ev or {}).get("quote")
        if quote:
            parts.append(quote)
    return " ".join(p for p in parts if p)


def _symbol_card_text(node: dict) -> Optional[str]:
    """Function/Class 文档 = zh_desc + zh_aliases（C2 索引期网关生成，``enrich`` 回填）。

    **仅带 ``zh_desc`` 者入档**（未富化的函数返回 None，不进语料）：zh_desc 是 ≤40 字中文
    功能描述、zh_aliases 是 3–5 条口语近义说法，二者共同承载跨语言（中文口语 → 英文符号）
    的词面桥接。英文 qualname 不入语料（由 ``link_entities`` 覆盖），避免稀释中文召回。
    """
    desc = node.get("zh_desc")
    if not (desc and desc.strip()):
        return None
    parts = [desc]
    parts.extend(node.get("zh_aliases") or [])
    return " ".join(p for p in parts if p)


def _doc_text(node: dict) -> Optional[str]:
    """节点 → 语料文本；不入选（Commit 无 message / Module docstring 为空 /
    Function·Class 无 zh_desc）返回 None。"""
    label = node["label"]
    if label == "Concept":
        return _concept_text(node)
    if label == "Commit":
        msg = node.get("message")
        return msg if (msg and msg.strip()) else None
    if label == "Module":
        doc = node.get("docstring")
        return doc if (doc and doc.strip()) else None
    if label in ("Function", "Class"):
        return _symbol_card_text(node)
    return None


def _doc_name(node: dict) -> str:
    """节点展示名：Function/Class 用 qualname，Concept/Module 用 name，Commit 用短 sha。"""
    label = node["label"]
    if label == "Commit":
        return (node.get("hash") or node["id"].rsplit("::", 1)[-1])[:8]
    if label in ("Function", "Class"):
        return node.get("qualname") or node.get("name") or node["id"].rsplit("::", 1)[-1]
    return node.get("name") or node["id"].rsplit("::", 1)[-1]


def _corpus_nodes(store: GraphStore) -> Iterator[tuple[dict, str]]:
    """遍历语料节点，产出 (node, text)；跳过无文本者。"""
    for label in _DOC_LABELS:
        for node in store.nodes(label):
            text = _doc_text(node)
            if text:
                yield node, text


# ---------------------------------------------------------------------------
# build_corpus_index —— 倒排索引 + 文档频次（DF），供 IDF 现算
# ---------------------------------------------------------------------------

class CorpusIndex:
    """236 篇微型文档的倒排索引。

    字段全部为查询期打分所需的确定性统计，可被后端缓存跨请求复用：

    - ``postings``  term → {doc_id: 词频tf}
    - ``df``        term → 文档频次（含该 term 的文档数）
    - ``doclen``    doc_id → 文档长度（term 总数）
    - ``meta``      doc_id → {label, name}
    - ``n_docs`` / ``avgdl``  文档数与平均长度（BM25 归一化用）
    """

    __slots__ = ("postings", "df", "doclen", "meta", "n_docs", "avgdl")

    def __init__(self) -> None:
        self.postings: dict[str, dict[str, int]] = {}
        self.df: dict[str, int] = {}
        self.doclen: dict[str, int] = {}
        self.meta: dict[str, dict] = {}
        self.n_docs: int = 0
        self.avgdl: float = 0.0


def build_corpus_index(store: GraphStore) -> CorpusIndex:
    """对 Concept/Commit/Module 三类节点建 BM25 倒排索引（确定性、可复用）。

    每个节点是一篇文档；文本经 ``zh_terms`` 切词后累计词频写入倒排表。返回的
    ``CorpusIndex`` 供 ``topic_recall`` 复用（``index=`` 传入即免重建）。
    """
    index = CorpusIndex()
    total_len = 0
    for node, text in _corpus_nodes(store):
        doc_id = node["id"]
        tf: dict[str, int] = {}
        for term in zh_terms(text):
            tf[term] = tf.get(term, 0) + 1
        if not tf:
            continue
        index.meta[doc_id] = {"label": node["label"], "name": _doc_name(node)}
        index.doclen[doc_id] = sum(tf.values())
        total_len += index.doclen[doc_id]
        for term, freq in tf.items():
            bucket = index.postings.get(term)
            if bucket is None:
                index.postings[term] = {doc_id: freq}
                index.df[term] = 1
            else:
                bucket[doc_id] = freq
                index.df[term] = len(bucket)

    index.n_docs = len(index.doclen)
    index.avgdl = (total_len / index.n_docs) if index.n_docs else 0.0
    return index


# ---------------------------------------------------------------------------
# topic_recall —— BM25-lite 打分召回
# ---------------------------------------------------------------------------

def _idf(n_docs: int, df: int) -> float:
    """BM25 概率 IDF（加 1 平滑，恒为正）：ln(1 + (N - df + 0.5)/(df + 0.5))。"""
    return math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))


def topic_recall(
    store: GraphStore,
    question: str,
    top_k: int = 8,
    index: Optional[CorpusIndex] = None,
    min_score: float = _MIN_SCORE,
) -> list[dict]:
    """对模糊问题做 BM25-lite 主题召回，返回命中的真实图谱节点。

    - ``index`` 传入则复用（后端缓存），否则现建；
    - 查询用 ``zh_terms`` 切词并去重（短查询里重叠 n-gram 不重复计权）；
    - 每个查询 term 的 IDF **现算**，按 BM25（k1=1.5, b=0.75）对含该 term 的文档累分；
    - 低于 ``min_score`` 的文档滤除，按分数降序（并列按 node_id）取前 ``top_k``。

    返回 ``[{node_id, label, name, score, matched_terms}]``；主要是 Concept，也含
    命中的 Commit / Module。无命中返回空列表。
    """
    if index is None:
        index = build_corpus_index(store)
    if index.n_docs == 0:
        return []

    # 查询侧停用词过滤（落地设计 §4.4 / calibration D-N1 修订）：滤除疑问/指代/功能词
    # n-gram（怎么/哪个/起来…），使高 IDF 的非内容词碎片不再主导打分（V0 校准根因）。
    # **仅作用于查询**——语料索引仍用原始 zh_terms（保 V1 自校验可复现）。
    q_terms = set(filter_stopwords(zh_terms(question)))
    if not q_terms:
        return []

    scores: dict[str, float] = {}
    matched: dict[str, set[str]] = {}

    for term in q_terms:
        bucket = index.postings.get(term)
        if not bucket:
            continue
        idf = _idf(index.n_docs, index.df[term])
        for doc_id, freq in bucket.items():
            dl = index.doclen[doc_id]
            denom = freq + _K1 * (1.0 - _B + _B * dl / index.avgdl)
            scores[doc_id] = scores.get(doc_id, 0.0) + idf * (freq * (_K1 + 1.0)) / denom
            matched.setdefault(doc_id, set()).add(term)

    ranked = sorted(
        (d for d, s in scores.items() if s >= min_score),
        key=lambda d: (-scores[d], d),
    )[:top_k]

    out: list[dict] = []
    for doc_id in ranked:
        meta = index.meta[doc_id]
        out.append({
            "node_id": doc_id,
            "label": meta["label"],
            "name": meta["name"],
            "score": round(scores[doc_id], 4),
            "matched_terms": sorted(matched[doc_id]),
        })
    return out
