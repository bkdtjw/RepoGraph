"""§4.2 调用图解析：两遍扫描，仅落**可静态判定**的 `CALLS` 边，不猜测。

Pass 1（全局符号表）：遍历全部模块，建立 dotted→ModuleFacts 索引；逐模块建立
    - 函数 qualname 集合；
    - "顶层符号 → Function qualname" 映射（模块级函数名 → 其 qualname）；
    - 类 qualname 集合与 类→bases。

Pass 2（逐函数解析调用点）：对每个 FunctionFacts 的每个 CallSite，按 §4.2 顺序
解析 callee 并产出 (caller_id, callee_id, {'count', 'call_sites'})，同一对合并计数。
resolved / unresolved 计入 PipelineStats.call_resolved / call_unresolved。

调用点归一化形态（models.CallSite.shape，由 ast_extractor 产出）：
    ("name", x)          裸名调用 f(...)
    ("attr", m, f)       属性调用 m.f(...)（m 为 Name）
    ("self", f)          方法内自调用 self.f(...) / cls.f(...)
    ("other", repr)      其余一律 unresolved
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models import (
    ModuleFacts,
    FunctionFacts,
    PipelineStats,
    symbol_id,
    build_module_index,
)


# ---------------------------------------------------------------------------
# Pass 1：每模块的局部符号索引
# ---------------------------------------------------------------------------


@dataclass
class _ModIndex:
    module: ModuleFacts
    func_qualnames: set          # 全部函数 qualname（含方法，如 "Worker.run"）
    toplevel_funcs: set          # 模块级函数 qualname（无 '.'，可裸名/属性直调）
    class_qualnames: set         # 全部类 qualname
    class_bases: dict            # 类 qualname → bases（按源码书写的点分名）


def _build_indexes(modules: list[ModuleFacts]) -> dict[str, _ModIndex]:
    idx: dict[str, _ModIndex] = {}
    for m in modules:
        func_qn = {f.qualname for f in m.functions}
        toplevel = {q for q in func_qn if "." not in q}
        class_qn = {c.qualname for c in m.classes}
        bases = {c.qualname: list(c.bases) for c in m.classes}
        idx[m.dotted] = _ModIndex(m, func_qn, toplevel, class_qn, bases)
    return idx


def _owning_class(func_qualname: str, class_qualnames: set) -> str | None:
    """所属类 = caller qualname 去掉末段后，沿 '.' 前缀命中的最长类 qualname。

    "Worker.run" → "Worker"；"Outer.Inner.m" → "Outer.Inner"（若为类）。
    非方法（无类前缀）返回 None。
    """
    prefix = func_qualname.rpartition(".")[0]
    while prefix:
        if prefix in class_qualnames:
            return prefix
        prefix = prefix.rpartition(".")[0]
    return None


def _resolve_base_class(
    base: str,
    cur_idx: _ModIndex,
    indexes: dict[str, _ModIndex],
) -> tuple[_ModIndex, str] | None:
    """把一个 bases 点分名解析到仓库内的某个类：返回 (目标模块索引, 类 qualname)。

    先在本模块类集合找；再经 from_import_map 解析到其他模块的类。单层，不递归。
    """
    # 1) 本模块类集合（含嵌套类的点分 qualname）
    if base in cur_idx.class_qualnames:
        return cur_idx, base
    # 2) from_import_map：from a.b import Base → {"Base": ("a.b", "Base")}
    fim = cur_idx.module.imports.from_import_map
    head = base.split(".", 1)[0]
    if head in fim:
        mod, sym = fim[head]
        rest = base[len(head):]              # "" 或 ".Nested"
        target_cls = sym + rest
        tidx = indexes.get(mod)
        if tidx is not None and target_cls in tidx.class_qualnames:
            return tidx, target_cls
    return None


# ---------------------------------------------------------------------------
# Pass 2：单个调用点解析 → (目标模块索引, 目标函数 qualname) 或 None
# ---------------------------------------------------------------------------


def _resolve_call(
    shape: tuple,
    module: ModuleFacts,
    func: FunctionFacts,
    cur_idx: _ModIndex,
    indexes: dict[str, _ModIndex],
) -> tuple[_ModIndex, str] | None:
    if not shape:
        return None
    kind = shape[0]

    # 1) 裸名调用 f(...)
    if kind == "name":
        x = shape[1]
        # a) 本模块顶层函数
        if x in cur_idx.toplevel_funcs:
            return cur_idx, x
        # b) from-import 到仓库内模块的顶层函数
        fim = module.imports.from_import_map
        if x in fim:
            mod, sym = fim[x]
            tidx = indexes.get(mod)
            if tidx is not None and sym in tidx.toplevel_funcs:
                return tidx, sym
        return None

    # 2) 属性调用 m.f(...)
    if kind == "attr":
        m, f = shape[1], shape[2]
        # a) m 为导入的模块别名 → 目标模块的顶层函数 f（只匹配顶层函数，Class.f 不算）
        im = module.imports.import_map
        if m in im:
            tidx = indexes.get(im[m])
            if tidx is not None and f in tidx.toplevel_funcs:
                return tidx, f
            return None
        # b) m 为本模块类名 → 连 "类.f"（f 须为其方法）
        if m in cur_idx.class_qualnames:
            method_qn = f"{m}.{f}"
            if method_qn in cur_idx.func_qualnames:
                return cur_idx, method_qn
        return None

    # 3) 方法内自调用 self.f(...) / cls.f(...)
    if kind == "self":
        f = shape[1]
        cls_qn = _owning_class(func.qualname, cur_idx.class_qualnames)
        if cls_qn is None:
            return None
        # a) 本类方法
        method_qn = f"{cls_qn}.{f}"
        if method_qn in cur_idx.func_qualnames:
            return cur_idx, method_qn
        # b) 沿 bases 在仓库内单层查找
        for base in cur_idx.class_bases.get(cls_qn, ()):
            resolved = _resolve_base_class(base, cur_idx, indexes)
            if resolved is None:
                continue
            base_idx, base_cls = resolved
            base_method_qn = f"{base_cls}.{f}"
            if base_method_qn in base_idx.func_qualnames:
                return base_idx, base_method_qn
        return None

    # 4) 其余（("other", ...) 或未知形态）→ unresolved
    return None


# ---------------------------------------------------------------------------
# 入口：build_calls
# ---------------------------------------------------------------------------


def build_calls(
    modules: list[ModuleFacts],
    stats: PipelineStats,
) -> list[tuple[str, str, dict]]:
    """构建 CALLS 边。

    返回 [(src_function_id, dst_function_id, {'count': n, 'call_sites': [行号,...]}), ...]。
    同一 (caller, callee) 的多次调用合并计数并累计调用点行号；自调用（递归）保留。
    """
    # Pass 1
    build_module_index(modules)          # 履行契约：dotted→ModuleFacts（存在性由 indexes 兜底）
    indexes = _build_indexes(modules)

    # Pass 2
    edges: dict[tuple[str, str], dict] = {}

    def _add_edge(caller_id: str, callee_id: str, lineno: int) -> None:
        e = edges.get((caller_id, callee_id))
        if e is None:
            e = {"count": 0, "call_sites": []}
            edges[(caller_id, callee_id)] = e
        e["count"] += 1
        e["call_sites"].append(lineno)

    for m in modules:
        cur_idx = indexes[m.dotted]
        for func in m.functions:
            caller_id = symbol_id(m.repo, m.relpath, func.qualname)
            for cs in func.call_sites:
                target = _resolve_call(cs.shape, m, func, cur_idx, indexes)
                if target is None:
                    stats.call_unresolved += 1
                    continue
                tidx, tqn = target
                callee_id = symbol_id(tidx.module.repo, tidx.module.relpath, tqn)
                _add_edge(caller_id, callee_id, cs.lineno)
                stats.call_resolved += 1

    return [(src, dst, props) for (src, dst), props in edges.items()]
