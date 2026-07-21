"""语义抽取层（文档 §5）：从 commit message / module docstring 抽取 Concept 实体，
经校验、对齐后就地 merge 进 GraphStore。

对外唯一入口 :func:`run_semantic`（契约见 §5.2）::

    run_semantic(store, repo_root, repo_name, stats, settings) -> dict

流程（§5.1→§5.4）：
  1. 输入单元：store 全部 Commit.message 按 ``settings.semantic_batch_size`` 分批；
     外加各 Module 非空 docstring 合成单批。
  2. 每批构造中文 prompt（附候选符号白名单），调 grok CLI 强制 JSON 输出。
  3. 校验流水线（§5.3）：quote 子串校验（不成立 confidence 减半并标记 quote_unverified）
     → implements 目标存在性（不在候选列表剔除并计 bad_target）
     → confidence < 阈值丢弃（计 low_confidence）。批次失败计 batch_error 跳过不中断。
  4. 对齐（§5.4 简化版）：规范化名称精确相等即合并（aliases 并集 / confidence 取高 /
     evidence 并集），审计写 output/align_audit.jsonl。
  5. 落图：Concept 节点 + DESCRIBES(Commit→Concept) / IMPLEMENTS(Module|Function→Concept)。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Optional

from repograph import models
from repograph.config import settings as _default_settings
from repograph.extract.grok_client import GrokError, ask_grok

__all__ = ["run_semantic", "CONCEPT_SCHEMA"]

# grok --json-schema 强制输出契约（§5.2）。
CONCEPT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "concepts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "ctype": {
                        "type": "string",
                        "enum": ["design_decision", "domain_concept", "constraint"],
                    },
                    "description": {"type": "string"},
                    "source_ref": {"type": "string"},
                    "quote": {"type": "string"},
                    "confidence": {"type": "number"},
                    "implements_candidates": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "name", "ctype", "description", "source_ref",
                    "quote", "confidence", "implements_candidates",
                ],
            },
        }
    },
    "required": ["concepts"],
}

_VALID_CTYPES = {"design_decision", "domain_concept", "constraint"}


# ---------------------------------------------------------------------------
# 内部数据结构
# ---------------------------------------------------------------------------


@dataclass
class _Batch:
    """一批待抽取单元：source_ref -> 原文，以及本批候选符号白名单。"""
    texts: dict[str, str] = field(default_factory=dict)
    candidates: set[str] = field(default_factory=set)


@dataclass
class _Extraction:
    """通过全部校验的一条概念（对齐前）。"""
    name: str
    ctype: str
    description: str
    source_ref: str
    quote: str
    confidence: float
    implements: list[str]          # 已通过候选校验的函数 id
    quote_unverified: bool


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def run_semantic(store, repo_root: str, repo_name: str, stats, settings=None) -> dict:
    settings = settings or _default_settings

    # ---- 1. 组装批次 --------------------------------------------------------
    batches = _build_batches(store, settings)

    # ---- 2+3. 逐批抽取并校验 ------------------------------------------------
    validated: list[_Extraction] = []
    for batch in batches:
        try:
            raw = _call_llm(batch, settings)
        except (GrokError, subprocess.TimeoutExpired, OSError) as exc:  # §5.3 批次失败
            _bump(stats, "batch_error")
            continue
        except Exception:  # 兜底：任何未预期异常也不得中断整条流水线
            _bump(stats, "batch_error")
            continue
        validated.extend(_validate(raw, batch, stats, settings))

    stats.semantic_extracted += len(validated)

    # ---- 4. 对齐 ------------------------------------------------------------
    groups = _align(validated, settings)

    # ---- 5. 落图 ------------------------------------------------------------
    summary = _materialize(store, groups)
    summary["batches"] = len(batches)
    summary["rejected"] = dict(stats.semantic_rejected)
    return summary


# ---------------------------------------------------------------------------
# 1. 批次组装
# ---------------------------------------------------------------------------


def _build_batches(store, settings) -> list[_Batch]:
    batches: list[_Batch] = []

    # commit MODIFIES 目标：commit_id -> {function id}
    commit_modifies: dict[str, set[str]] = {}
    for src, _t, dst, _p in store.edges("MODIFIES"):
        commit_modifies.setdefault(src, set()).add(dst)

    # commit message 分批
    commits = sorted(
        (n for n in store.nodes("Commit") if (n.get("message") or "").strip()),
        key=lambda n: n["id"],
    )
    size = max(1, int(getattr(settings, "semantic_batch_size", 20)))
    for i in range(0, len(commits), size):
        chunk = commits[i:i + size]
        b = _Batch()
        for n in chunk:
            b.texts[n["id"]] = n["message"]
            b.candidates |= commit_modifies.get(n["id"], set())
        batches.append(b)

    # module docstring 单批（候选=该模块内全部函数 id）
    func_ids = [n["id"] for n in store.nodes("Function")]
    modules = sorted(
        (n for n in store.nodes("Module") if (n.get("docstring") or "").strip()),
        key=lambda n: n["id"],
    )
    if modules:
        b = _Batch()
        for m in modules:
            mid = m["id"]
            b.texts[mid] = m["docstring"]
            prefix = mid + "::"
            b.candidates |= {fid for fid in func_ids if fid.startswith(prefix)}
        batches.append(b)

    return batches


# ---------------------------------------------------------------------------
# 2. LLM 调用
# ---------------------------------------------------------------------------


def _call_llm(batch: _Batch, settings) -> dict:
    prompt = _build_prompt(batch)
    timeout = int(getattr(settings, "grok_timeout_s", 300))
    result = ask_grok(prompt, json_schema=CONCEPT_SCHEMA, timeout=timeout,
                      exe=getattr(settings, "grok_exe", None))
    if not isinstance(result, dict):
        raise GrokError(f"unexpected ask_grok return type: {type(result).__name__}")
    return result


def _build_prompt(batch: _Batch) -> str:
    lines: list[str] = []
    lines.append("你是一个从代码仓库文本（提交信息、模块文档字符串）中抽取“概念实体”的信息抽取器。")
    lines.append("概念分三类 ctype：design_decision（设计决策）、domain_concept（领域概念）、constraint（约束）。")
    lines.append("")
    lines.append("必须遵守以下规则：")
    lines.append("1. 只抽取文本中明确表述的概念，禁止推断、脑补或补全文本没有写出的内容。")
    lines.append("2. quote 必须逐字复制自对应来源的原文，是原文中的一段连续子串，不得改写、翻译或拼接。")
    lines.append("3. implements_candidates 只能从下方“候选符号列表”中原样选取；列表之外的任何 id 一律不得输出。")
    lines.append("4. 没有可抽取的概念时，concepts 返回空数组 []，禁止为凑数而编造。")
    lines.append("5. confidence 为 0~1 的校准置信度：文本明确直接给高分（≥0.8），含糊或需一定推断给低分（<0.6）。")
    lines.append("6. source_ref 必须是下方输入中给出的 [来源ID]，不得改写。")
    lines.append("")

    if batch.candidates:
        lines.append("候选符号列表（implements_candidates 只能取自其中）：")
        for cid in sorted(batch.candidates):
            lines.append(f"- {cid}")
    else:
        lines.append("候选符号列表：（空）——本批不得输出任何 implements_candidates。")
    lines.append("")

    lines.append("输入文本（每段以 [来源ID] 开头，其后为该来源的原文）：")
    for ref, text in batch.texts.items():
        lines.append(f"[{ref}] {text}")
    lines.append("")
    lines.append('请严格按给定 JSON schema 输出 {"concepts": [...]}；无可抽取时输出 {"concepts": []}。')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 3. 校验流水线（§5.3）
# ---------------------------------------------------------------------------


def _validate(raw: dict, batch: _Batch, stats, settings) -> list[_Extraction]:
    concepts = raw.get("concepts")
    if not isinstance(concepts, list):
        return []

    conf_min = float(getattr(settings, "semantic_confidence_min", 0.6))
    out: list[_Extraction] = []

    for c in concepts:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        source_ref = str(c.get("source_ref") or "").strip()
        if not name or not source_ref:
            continue

        ctype = c.get("ctype")
        if ctype not in _VALID_CTYPES:
            ctype = "domain_concept"

        try:
            confidence = float(c.get("confidence"))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        quote = str(c.get("quote") or "")

        # (a) quote 子串校验：不成立则 confidence 减半并标记 quote_unverified。
        source_text = batch.texts.get(source_ref, "")
        quote_unverified = False
        if not quote or quote not in source_text:
            quote_unverified = True
            confidence = confidence / 2.0

        # (b) implements 目标存在性校验：不在候选列表则剔除并计 bad_target。
        raw_impl = c.get("implements_candidates") or []
        if not isinstance(raw_impl, list):
            raw_impl = []
        impl_ok: list[str] = []
        for cand in raw_impl:
            cand = str(cand)
            if cand in batch.candidates:
                if cand not in impl_ok:
                    impl_ok.append(cand)
            else:
                _bump(stats, "bad_target")

        # (c) confidence 阈值过滤。
        if confidence < conf_min:
            _bump(stats, "low_confidence")
            continue

        out.append(_Extraction(
            name=name,
            ctype=ctype,
            description=str(c.get("description") or ""),
            source_ref=source_ref,
            quote=quote,
            confidence=confidence,
            implements=impl_ok,
            quote_unverified=quote_unverified,
        ))

    return out


# ---------------------------------------------------------------------------
# 4. 对齐（§5.4 简化版：规范化名称精确相等即合并）
# ---------------------------------------------------------------------------


@dataclass
class _Group:
    key: str
    name: str                                  # 规范名（最高置信度者）
    ctype: str
    description: str
    confidence: float
    aliases: set[str] = field(default_factory=set)
    evidence: list[dict] = field(default_factory=list)   # [{source_ref, quote}]
    members: list[_Extraction] = field(default_factory=list)


def _align(validated: list[_Extraction], settings) -> list[_Group]:
    groups: dict[str, _Group] = {}

    for ex in validated:
        key = _norm_key(ex.name)
        g = groups.get(key)
        if g is None:
            g = _Group(key=key, name=ex.name, ctype=ex.ctype,
                       description=ex.description, confidence=ex.confidence)
            groups[key] = g
        else:
            # 保留高置信度者为规范名。
            if ex.confidence > g.confidence:
                if g.name != ex.name:
                    g.aliases.add(g.name)
                g.name = ex.name
                # 规范名不得同时留在别名集：多成员乱序合并时旧规范名可能等于新规范名，
                # 否则落图/审计的 aliases 会包含概念自身的名字（违反互斥语义）。
                g.aliases.discard(ex.name)
                g.ctype = ex.ctype
                g.description = ex.description
                g.confidence = max(g.confidence, ex.confidence)
            elif ex.name != g.name:
                g.aliases.add(ex.name)
        g.members.append(ex)
        ev = {"source_ref": ex.source_ref, "quote": ex.quote}
        if ev not in g.evidence:
            g.evidence.append(ev)

    result = list(groups.values())
    _write_audit(result, settings)
    return result


def _norm_key(name: str) -> str:
    """规范化名称作为合并键：小写、去空格与连字符。"""
    return re.sub(r"[\s\-]+", "", name.strip().lower())


def _slug(name: str) -> str:
    """由名称生成 slug：小写、非字母数字（含中文以外符号）替换为 '-'，中文保留。"""
    s = name.strip().lower()
    s = re.sub("[^0-9a-z一-鿿]+", "-", s)
    s = s.strip("-")
    return s or "concept"


def _concept_key(g: "_Group") -> str:
    """Concept 节点 ID 的判别串。

    直接用对齐阶段的规范化键 g.key（每个组唯一），而非 _slug(g.name)。
    _slug 会把 '_'、'.' 等 g.key 保留的标点一并归一为 '-'，可能把两个规范化键
    不同（§5.4 有意保留为独立概念）的组碰撞到同一 concept_id，导致 merge_node
    互相覆盖、边与属性串台。以 g.key 为准可保证 组↔concept_id 一一对应。"""
    return g.key or _slug(g.name)


def _write_audit(groups: list[_Group], settings) -> None:
    outdir = getattr(settings, "output_dir", "output")
    path = os.path.join(outdir, "align_audit.jsonl")
    try:
        os.makedirs(outdir or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for g in groups:
                row = {
                    "canonical_name": g.name,
                    "norm_key": g.key,
                    "concept_id": models.concept_id(_concept_key(g)),
                    "aliases": sorted(g.aliases),
                    "members": [m.name for m in g.members],
                    "merged": len(g.members) > 1,
                    "confidence": g.confidence,
                    "evidence_count": len(g.evidence),
                }
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        # 审计失败不应中断落图。
        pass


# ---------------------------------------------------------------------------
# 5. 落图
# ---------------------------------------------------------------------------


def _materialize(store, groups: list[_Group]) -> dict:
    describes: set[tuple[str, str]] = set()
    implements: set[tuple[str, str]] = set()

    for g in groups:
        cid = models.concept_id(_concept_key(g))
        store.merge_node(cid, "Concept", {
            "name": g.name,
            "ctype": g.ctype,
            "description": g.description,
            "aliases": sorted(g.aliases),
            "confidence": g.confidence,
            "evidence": list(g.evidence),
        })

        for ex in g.members:
            props = {
                "confidence": ex.confidence,
                "evidence": ex.quote,
                "quote_unverified": ex.quote_unverified,
            }
            src_node = store.get_node(ex.source_ref)
            src_label = src_node.get("label") if src_node else None

            if src_label == "Commit":
                store.merge_edge(ex.source_ref, "DESCRIBES", cid, props)
                describes.add((ex.source_ref, cid))
            elif src_label == "Module":
                store.merge_edge(ex.source_ref, "IMPLEMENTS", cid, props)
                implements.add((ex.source_ref, cid))
            # source_ref 非 Commit/Module（理论上不出现）时不建来源边，仅保留概念节点。

            for fid in ex.implements:
                store.merge_edge(fid, "IMPLEMENTS", cid, props)
                implements.add((fid, cid))

    return {
        "concepts": len(groups),
        "describes": len(describes),
        "implements": len(implements),
    }


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _bump(stats, reason: str, n: int = 1) -> None:
    stats.semantic_rejected[reason] = stats.semantic_rejected.get(reason, 0) + n
