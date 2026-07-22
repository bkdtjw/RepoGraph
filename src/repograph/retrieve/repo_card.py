"""level-0 仓库卡片（v0.3 · Phase C1 · 裁定 D-01）——meta 路由的答案素材。

落地设计 §4.1 / P1「答案来源前置」：「你了解这个项目吗」类元问题的答案素材必须在
**索引期显式制造**，不指望检索碰巧覆盖。本模块把 v0.1 惰性拼装的 ``build_overview``
升格为**结构化卡片**：

- **确定性字段**（纯图谱统计，零网络）：``stats``（22/15/259/75/139 五类规模）、
  ``top_modules``（按 loc）、``hot_functions``（按 MODIFIES 计数）、``core_concepts``
  （按 IMPLEMENTS 落点）、``entrypoints``（聚合 ``Function.is_endpoint``，本图为 0 则如实空）。
- **summary**（唯一一次索引期真实网关调用，qwen3.8-max-preview，≤300 字）：由
  ``generate_card_summary`` 产出并经**专名白名单校验**（summary 中英文标识符必须出现在输入中，
  违规重试 1 次后降级弃 summary，反幻觉，风险 F2）。失败**如实降级为纯确定性卡片**，不伪造。

产出 ``output/repo_card.json`` 缓存。查询期 meta 路由 ``build_meta_context`` **读取优先缓存**、
缺失/损坏则现场 ``build_repo_card``（纯确定性）+ ``degraded=True``，**绝不因缺文件裸拒**（P4）。

只依赖 ``..models.GraphStore``（确定性部分）与 ``..extract.llm_client``（summary 生成，
仅索引期）。**不 import ``context``**（避免与 meta 路由回接的循环依赖）。
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

from ..models import GraphStore, dotted_from_relpath

# 卡片确定性字段的展示上限（与 build_overview 对齐，保证 L0 事实齐备）
_TOP_MODULES = 8
_TOP_HOT = 5
_TOP_CONCEPTS = 8

_SUMMARY_MAX_CHARS = 300


# ---------------------------------------------------------------------------
# 路径与小工具（本地实现，不 import context 以免循环）
# ---------------------------------------------------------------------------

def default_cache_path() -> str:
    """``output/repo_card.json`` 绝对路径（相对仓库根）。"""
    here = os.path.dirname(os.path.abspath(__file__))            # src/repograph/retrieve
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    return os.path.join(repo_root, "output", "repo_card.json")


def _first_line(text: Optional[str]) -> str:
    if not text:
        return ""
    for line in text.splitlines():
        s = line.strip()
        if s:
            return s
    return ""


def _qn(store: GraphStore, node_id: str) -> str:
    node = store.get_node(node_id)
    if node is None:
        return node_id.rsplit("::", 1)[-1]
    return node.get("qualname") or node.get("name") or node_id.rsplit("::", 1)[-1]


def _repo_name(store: GraphStore) -> str:
    for node in store.nodes():
        nid = node.get("id", "")
        if "::" in nid and not nid.startswith("concept::"):
            return nid.split("::", 1)[0]
    return "(unknown)"


# ---------------------------------------------------------------------------
# 确定性卡片装配（纯图谱统计，零网络）
# ---------------------------------------------------------------------------

def build_repo_card(store: GraphStore, summary: Optional[str] = None) -> dict:
    """装配 level-0 卡片的**确定性**字段（+ 可选 summary）。纯函数、零网络。

    与 ``context.build_overview`` 口径一致：顶层模块按 ``loc`` 降序、热点函数按 MODIFIES
    入度、核心概念按 IMPLEMENTS 落点数；``entrypoints`` 聚合 ``is_endpoint`` 为真的函数
    （本图为 0，则如实产空）。返回可 JSON 序列化的卡片 dict。
    """
    counts = store.counts().get("nodes", {})
    stats = {
        "modules": counts.get("Module", 0),
        "classes": counts.get("Class", 0),
        "functions": counts.get("Function", 0),
        "commits": counts.get("Commit", 0),
        "concepts": counts.get("Concept", 0),
    }

    # 顶层模块：按 loc 降序 top N
    mods = sorted(store.nodes("Module"),
                  key=lambda m: (-(m.get("loc") or 0), m.get("path", "")))
    top_modules = [
        {"path": m.get("path", "?"), "loc": m.get("loc", 0),
         "doc": _first_line(m.get("docstring"))}
        for m in mods[:_TOP_MODULES]
    ]

    # 热点函数：被 MODIFIES 指向最多的 top N
    mod_ct: dict[str, int] = {}
    for _s, _t, dst, _p in store.edges("MODIFIES"):
        mod_ct[dst] = mod_ct.get(dst, 0) + 1
    hot = sorted(mod_ct.items(), key=lambda kv: (-kv[1], kv[0]))
    hot_functions = [
        {"qualname": _qn(store, fid), "modifies": c}
        for fid, c in hot if store.get_node(fid) is not None
    ][:_TOP_HOT]

    # 核心概念：IMPLEMENTS 落点最多的 top N
    impl_ct: dict[str, int] = {}
    for _s, _t, dst, _p in store.edges("IMPLEMENTS"):
        impl_ct[dst] = impl_ct.get(dst, 0) + 1
    core = sorted(impl_ct.items(), key=lambda kv: (-kv[1], kv[0]))
    core_concepts = []
    for cid, c in core:
        node = store.get_node(cid)
        if node is None:
            continue
        core_concepts.append({"name": node.get("name", ""), "impl": c,
                              "desc": _first_line(node.get("description"))})
        if len(core_concepts) >= _TOP_CONCEPTS:
            break

    # 入口点：聚合 is_endpoint 为真的 Function（本图为 0 → 如实空）
    entrypoints = []
    for fn in store.nodes("Function"):
        if fn.get("is_endpoint"):
            entrypoints.append({
                "qualname": fn.get("qualname", ""),
                "http_method": fn.get("http_method"),
                "route_path": fn.get("route_path"),
            })

    return {
        "repo": _repo_name(store),
        "stats": stats,
        "top_modules": top_modules,
        "hot_functions": hot_functions,
        "core_concepts": core_concepts,
        "entrypoints": entrypoints,
        "summary": summary or None,
    }


def render_card_text(card: dict) -> str:
    """把卡片渲染为中文注入文本（含全部确定性事实 + 可选 summary）。

    **恒含规模事实行**（模块/类/函数/提交/概念计数），使 meta 路由无论是否有 summary
    都满足 L0 事实达标（≥3），与 ``build_overview`` 的事实口径一致。
    """
    s = card.get("stats", {})
    lines: list[str] = []
    lines.append("【仓库卡片】（来源: 图谱统计 · level-0 repo_card）")
    lines.append(f"仓库: {card.get('repo', '(unknown)')}")
    lines.append(
        f"规模: 模块 {s.get('modules', 0)} · 类 {s.get('classes', 0)} · "
        f"函数 {s.get('functions', 0)} · 提交 {s.get('commits', 0)} · "
        f"概念 {s.get('concepts', 0)}"
    )

    summary = card.get("summary")
    if summary:
        lines.append("")
        lines.append("【概述】（来源: 索引期网关生成 · 已过专名白名单校验）")
        lines.append(summary)

    mods = card.get("top_modules") or []
    if mods:
        lines.append("")
        lines.append("【顶层模块】（来源: Module 按 loc 降序）")
        for m in mods:
            lines.append(f"- {m.get('path', '?')} (loc={m.get('loc', '?')}): "
                         f"{m.get('doc') or '（无 docstring）'}")

    hot = card.get("hot_functions") or []
    if hot:
        lines.append("")
        lines.append("【热点函数】（来源: MODIFIES 计数，改动最频繁）")
        for h in hot:
            lines.append(f"- {h.get('qualname', '')} (被 {h.get('modifies', 0)} 次提交修改)")

    core = card.get("core_concepts") or []
    if core:
        lines.append("")
        lines.append("【核心概念】（来源: IMPLEMENTS 落点最多）")
        for c in core:
            lines.append(f"- {c.get('name', '')} (落 {c.get('impl', 0)} 处实现): "
                         f"{c.get('desc') or ''}")

    eps = card.get("entrypoints") or []
    lines.append("")
    if eps:
        lines.append("【入口点】（来源: Function.is_endpoint 聚合）")
        for e in eps:
            hm = e.get("http_method") or "?"
            rp = e.get("route_path") or "?"
            lines.append(f"- {e.get('qualname', '')} [{hm} {rp}]")
    else:
        lines.append("【入口点】（来源: Function.is_endpoint 聚合）: （本图无 HTTP 端点）")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# summary 生成（唯一一次索引期真实网关调用 + 专名白名单校验）
# ---------------------------------------------------------------------------

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_./]*")


def _summary_input(card: dict, readme_head: str = "") -> tuple[str, set[str]]:
    """构造 summary 生成的输入文本 + 允许出现的专名白名单集合（英文标识符/路径）。"""
    parts: list[str] = [render_card_text({**card, "summary": None})]
    if readme_head:
        parts.append("README 首段：" + readme_head)
    input_text = "\n".join(parts)
    allowed = {m.group(0) for m in _IDENT_RE.finditer(input_text)}
    return input_text, allowed


def _summary_violates_whitelist(summary: str, input_text: str) -> list[str]:
    """返回 summary 里**不在输入中**的英文标识符（专名白名单违规项，反幻觉 F2）。

    只校验英文标识符类专名（发明 Redis/FastAPI 之类的主要幻觉风险面）；短词（≤1 字符）
    与在输入文本中出现（子串）者放行。中文由生成侧 prompt 约束 + 人工抽检兜底。
    """
    low_input = input_text.lower()
    bad: list[str] = []
    for m in _IDENT_RE.finditer(summary):
        tok = m.group(0)
        if len(tok) <= 1:
            continue
        if tok.lower() not in low_input:
            bad.append(tok)
    return bad


def generate_card_summary(store: GraphStore, card: Optional[dict] = None,
                          readme_head: str = "", model: Optional[str] = None,
                          config_path: Optional[str] = None,
                          whitelist_retries: int = 4) -> Optional[str]:
    """唯一一次索引期真实网关调用生成 ≤300 字 summary；白名单违规重试后降级弃之。

    真实数据铁律：网络/网关**真实发生**；任何失败（配置缺失 / HTTP 错误 / 白名单违规
    达 ``whitelist_retries`` 次）→ 返回 ``None``（弃 summary），**绝不伪造**。绝不回显 token
    （由 llm_client 保证）。白名单违规靠重试而非放宽校验消化（模型输出非确定性，反幻觉防线不降）。
    """
    from ..extract.llm_client import ask_gateway, INDEX_MODEL, GatewayConfigError, GatewayCallError

    if card is None:
        card = build_repo_card(store)
    input_text, _allowed = _summary_input(card, readme_head)
    system = (
        "你是代码库概述撰写器。根据下面提供的【确定性事实】（规模统计、顶层模块、热点函数、"
        "核心概念）为该代码库写一段 ≤300 字的中文概述，供他人快速了解这个项目是做什么的、"
        "整体结构与重点。严格约束：(1) 只能使用事实中原样出现过的专名（模块路径、函数名、概念名、"
        "数字），严禁发明事实里没有的技术栈名词、库名或英文缩写；(2) 尽量用中文表述，不自造英文词；"
        "(3) 不做评价性夸张；(4) 只输出概述正文，不要标题、不要 JSON、不要解释。"
    )
    user = "【确定性事实】\n" + input_text + "\n\n请据此写 ≤300 字中文概述："

    for _attempt in range(max(1, whitelist_retries)):
        try:
            # qwen3.8-max-preview 为大模型、时延波动大，给足 timeout（90s）；ask_gateway
            # 内部再重试 1 次消化瞬时超时，外层 whitelist_retries 消化非确定性白名单违规。
            text = ask_gateway(system, user, model=model or INDEX_MODEL,
                               max_tokens=600, config_path=config_path,
                               timeout=90, retries=1)
        except (GatewayConfigError, GatewayCallError):
            return None                      # 如实降级，不伪造
        summary = " ".join(text.split()).strip()
        if len(summary) > _SUMMARY_MAX_CHARS:
            summary = summary[:_SUMMARY_MAX_CHARS]
        bad = _summary_violates_whitelist(summary, input_text)
        if not bad:
            return summary
        # 白名单违规（模型输出非确定性）：重试；耗尽仍违规则弃 summary（降级）
    return None


def generate_and_save(store: GraphStore, cache_path: Optional[str] = None,
                      readme_head: str = "", model: Optional[str] = None,
                      config_path: Optional[str] = None) -> dict:
    """索引期一次性：确定性卡片 + 真实 summary → 写 ``output/repo_card.json``。

    summary 生成失败时只落确定性字段（``summary=null``），meta 路由仍可作答（与
    ``cli.py`` 既有「语义层未就绪降级为告警」一致）。返回落盘的卡片 dict。
    """
    path = cache_path or default_cache_path()
    card = build_repo_card(store)
    summary = generate_card_summary(store, card, readme_head=readme_head,
                                    model=model, config_path=config_path)
    card["summary"] = summary
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(card, f, ensure_ascii=False, indent=1)
    return card


# ---------------------------------------------------------------------------
# 查询期 meta 路由入口（读取优先缓存，缺失/损坏降级现场确定性卡片）
# ---------------------------------------------------------------------------

def load_or_build_repo_card(store: GraphStore,
                            cache_path: Optional[str] = None) -> tuple[dict, bool]:
    """读取 ``output/repo_card.json`` 缓存；缺失/损坏 → 现场 ``build_repo_card`` + ``degraded=True``。

    返回 ``(card, degraded)``。缓存损坏/结构不符时不抛错（P4 绝不因缺文件裸拒），只降级。
    """
    path = cache_path or default_cache_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                card = json.load(f)
            if isinstance(card, dict) and isinstance(card.get("stats"), dict):
                return card, False
        except Exception:  # noqa: BLE001  损坏缓存 → 现场重建
            pass
    return build_repo_card(store), True


def build_meta_context(store: GraphStore, cache_path: Optional[str] = None) -> dict:
    """meta 路由：注入 repo_card（缓存优先），返回 ``mode='meta'`` 的上下文 dict。

    返回统一 ``{mode, linked, context_text, stats, degraded}``；``degraded`` 标识本次是否
    走了现场降级（缓存缺失/损坏）。绝不裸拒（context_text 恒含规模事实）。
    """
    card, degraded = load_or_build_repo_card(store, cache_path)
    s = card.get("stats", {})
    stats = {
        "symbols": 0, "topics": 0, "impact_callers": 0, "commits": 0,
        "concepts": s.get("concepts", 0),
        "modules": s.get("modules", 0), "functions": s.get("functions", 0),
        "classes": s.get("classes", 0),
    }
    # mode 取 overview 展示态（spec §5.1：build_repo_context 侧恒返回 overview 类展示 mode，
    # meta/global 的事件 mode 改写在网关侧据 route_label 完成；此处以 route_label='meta' 承载
    # 精确五分类，mode='overview' 保证 server._RG_VALID_MODES 兼容、零 server 改动）。
    return {
        "mode": "overview",
        "linked": [],
        "context_text": render_card_text(card),
        "stats": stats,
        "degraded": degraded,
    }
