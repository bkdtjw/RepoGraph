"""影响面分析（§7.2）——确定性模板查询，非 LLM 生成。

本地后端（models.GraphStore）上的 BFS 实现，语义与文档 §7.2 的 Cypher 模板
等价：沿 CALLS 反向求调用方闭包（分层），或沿 IMPORTS 反向求模块级影响面。
AGE 部署形态下由 store/age.py 的参数化模板承担同一职责（depth 白名单拼入，
$fid 走参数），返回结构保持一致。
"""
from __future__ import annotations

from typing import Optional

from ..models import GraphStore

_DEPTH_WHITELIST = (1, 2, 3, 4)
_MODES = ("calls", "imports")


# ---------------------------------------------------------------------------
# 符号解析（§7.1 的精简确定性版）：精确 qualname → 唯一后缀 → 歧义/未命中
# ---------------------------------------------------------------------------

def _resolve_symbol(store: GraphStore, symbol: str) -> tuple[Optional[str], Optional[dict]]:
    """返回 (function_id, None) 或 (None, error_dict)。

    1) 精确匹配：Function.qualname == symbol；恰好 1 个 → 命中，>1 → 歧义。
    2) 否则唯一后缀匹配：qualname 以 '.'+symbol 结尾（段边界）；恰好 1 → 命中，
       >1 → 歧义，0 → 未命中。
    """
    exact: list[str] = []
    suffix: list[str] = []
    suffix_tail = "." + symbol
    for node in store.nodes("Function"):
        qn = node.get("qualname", "")
        if qn == symbol:
            exact.append(node["id"])
        elif qn.endswith(suffix_tail):
            suffix.append(node["id"])

    if len(exact) == 1:
        return exact[0], None
    if len(exact) > 1:
        return None, {"error": "ambiguous", "candidates": sorted(exact)}

    if len(suffix) == 1:
        return suffix[0], None
    if len(suffix) > 1:
        return None, {"error": "ambiguous", "candidates": sorted(suffix)}

    return None, {"error": "not_found"}


# ---------------------------------------------------------------------------
# 反向邻接与 CONTAINS 反查
# ---------------------------------------------------------------------------

def _reverse_adjacency(store: GraphStore, etype: str) -> dict[str, set[str]]:
    """etype 边的反向邻接：dst → {src}。用于反向 BFS。"""
    rev: dict[str, set[str]] = {}
    for src, _t, dst, _props in store.edges(etype):
        rev.setdefault(dst, set()).add(src)
    return rev


def _contains_parents(store: GraphStore) -> dict[str, list[str]]:
    """CONTAINS 反向：child_id → [parent_id]（parent 可能是 Module 或 Class）。"""
    parents: dict[str, list[str]] = {}
    for src, _t, dst, _props in store.edges("CONTAINS"):
        parents.setdefault(dst, []).append(src)
    return parents


def _module_of(store: GraphStore, func_id: str, parents: dict[str, list[str]]) -> set[str]:
    """经 CONTAINS 反查函数所属模块：直接父为 Module 取之；父为 Class 时再上溯一层到 Module。"""
    mods: set[str] = set()
    for parent in parents.get(func_id, ()):
        node = store.get_node(parent)
        if node is None:
            continue
        if node["label"] == "Module":
            mods.add(parent)
        elif node["label"] == "Class":
            for gp in parents.get(parent, ()):
                gpn = store.get_node(gp)
                if gpn is not None and gpn["label"] == "Module":
                    mods.add(gp)
    return mods


def _bfs_levels(
    seeds: list[str], rev: dict[str, set[str]], depth: int
) -> tuple[dict[str, int], bool]:
    """从 seeds（level 0）沿反向邻接逐层 BFS 到 depth。

    返回 (levels: node→首达层号, truncated)。truncated=True 表示在 depth 层仍有
    未纳入的上游节点（闭包被深度截断）。
    """
    levels: dict[str, int] = {s: 0 for s in seeds}
    frontier = list(seeds)
    for d in range(1, depth + 1):
        nxt: list[str] = []
        for node in frontier:
            for pred in rev.get(node, ()):
                if pred not in levels:
                    levels[pred] = d
                    nxt.append(pred)
        frontier = nxt
        if not frontier:
            break

    # 截断判定：最外层节点是否还有未纳入的上游
    truncated = False
    for node in frontier:                       # frontier = 最后一层新增节点
        for pred in rev.get(node, ()):
            if pred not in levels:
                truncated = True
                break
        if truncated:
            break
    return levels, truncated


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

def impact_analysis(
    store: GraphStore, symbol: str, depth: int = 3, mode: str = "calls"
) -> dict:
    """影响面分析。

    mode='calls'：沿 CALLS 反向 BFS 求调用方闭包，分层给出
        direct_callers(1 跳) / transitive_callers(2..depth 跳)，并汇聚
        affected_endpoints（闭包内 is_endpoint 函数）与 affected_modules
        （闭包内函数所属模块，经 CONTAINS 反查）。闭包含目标函数自身。
    mode='imports'：定位符号所在 Module，沿 IMPORTS 反向 BFS 求受影响模块列表；
        direct_callers/transitive_callers 为模块级分层，affected_endpoints 为
        受影响模块内的端点函数。

    出错返回 {'error': ...}；歧义附 candidates。depth 限定白名单整数 1–4。
    """
    if isinstance(depth, bool) or not isinstance(depth, int) or depth not in _DEPTH_WHITELIST:
        raise ValueError(f"depth 必须是 1..4 的整数（白名单），收到 {depth!r}")
    if mode not in _MODES:
        raise ValueError(f"mode 必须是 {_MODES} 之一，收到 {mode!r}")

    resolved, err = _resolve_symbol(store, symbol)
    if err is not None:
        return err
    assert resolved is not None

    if mode == "calls":
        return _impact_calls(store, resolved, depth)
    return _impact_imports(store, resolved, depth)


def _impact_calls(store: GraphStore, target: str, depth: int) -> dict:
    rev = _reverse_adjacency(store, "CALLS")
    levels, truncated = _bfs_levels([target], rev, depth)

    direct = sorted(nid for nid, lvl in levels.items() if lvl == 1)
    transitive = sorted(nid for nid, lvl in levels.items() if 2 <= lvl <= depth)

    closure = set(levels)               # 含目标（level 0）+ 全部调用方
    parents = _contains_parents(store)

    endpoints: list[str] = []
    modules: set[str] = set()
    for nid in closure:
        node = store.get_node(nid)
        if node is None:
            continue
        if node.get("is_endpoint"):
            endpoints.append(nid)
        modules |= _module_of(store, nid, parents)

    return {
        "resolved_symbol": target,
        "mode": "calls",
        "depth": depth,
        "direct_callers": direct,
        "transitive_callers": transitive,
        "affected_endpoints": sorted(endpoints),
        "affected_modules": sorted(modules),
        "truncated": truncated,
    }


def _impact_imports(store: GraphStore, target: str, depth: int) -> dict:
    parents = _contains_parents(store)
    seed_modules = sorted(_module_of(store, target, parents))

    rev = _reverse_adjacency(store, "IMPORTS")
    levels, truncated = _bfs_levels(seed_modules, rev, depth)

    direct = sorted(mid for mid, lvl in levels.items() if lvl == 1)
    transitive = sorted(mid for mid, lvl in levels.items() if 2 <= lvl <= depth)
    affected_modules = sorted(levels)          # 含源模块（level 0）

    # 受影响模块内的端点函数（经 CONTAINS 正向：闭包模块所属的所有函数中筛端点）
    affected_set = set(affected_modules)
    endpoints: list[str] = []
    for node in store.nodes("Function"):
        if not node.get("is_endpoint"):
            continue
        if _module_of(store, node["id"], parents) & affected_set:
            endpoints.append(node["id"])

    return {
        "resolved_symbol": target,
        "mode": "imports",
        "depth": depth,
        "direct_callers": direct,
        "transitive_callers": transitive,
        "affected_endpoints": sorted(endpoints),
        "affected_modules": affected_modules,
        "truncated": truncated,
    }
