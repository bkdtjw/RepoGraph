"""图谱组装（§3.2 / §3.3）。

把确定性抽取层的产物（ModuleFacts 列表、CALLS 边、Git 数据）组装成
一个 `models.GraphStore`：落全部节点（Module / Class / Function / Commit /
Issue）与结构/历史边（CONTAINS / IMPORTS / CALLS / MODIFIES / TOUCHES /
FIXES）。语义层节点与边（Concept / DESCRIBES / IMPLEMENTS 等）由
semantic.run_semantic 就地 merge，不在此处。

本模块只消费共享契约 models.py，不 import 其他抽取模块。
"""
from __future__ import annotations

from .models import (
    GraphStore,
    ModuleFacts,
    PipelineStats,  # noqa: F401  (仅用于类型提示可读性)
    build_module_index,
    symbol_id,
)


def _first_line(text: str | None) -> str | None:
    """docstring 首行（去空白）；空或 None → None。完整原文进向量层，节点只留首行。"""
    if not text:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    return stripped.splitlines()[0].strip()


def _add_imports(store: GraphStore, m: ModuleFacts, index: dict[str, ModuleFacts]) -> None:
    """由 ImportFacts 生成仓库内 Module→Module 的 IMPORTS 边（§3.3）。

    names 语义（按契约）：
      - from a.b import f as g  → 记符号名 'f'
      - import a.b as c         → 整体导入，记 '*module*'
      - from pkg import submod  → 若 pkg.submod 是仓库内模块，视为整体导入，记 '*module*'
    仅落目标在仓库内（经 dotted→模块索引解析成功）的边；外部依赖不建边
    （已由 Module.external_imports 属性承载）。
    """
    mid = m.id
    names_by_target: dict[str, list[str]] = {}
    order: list[str] = []

    def add(target_id: str, name: str) -> None:
        if target_id == mid:          # 不给模块自身建 IMPORTS 自环
            return
        bucket = names_by_target.get(target_id)
        if bucket is None:
            bucket = []
            names_by_target[target_id] = bucket
            order.append(target_id)
        if name not in bucket:
            bucket.append(name)

    imp = m.imports

    # 整体模块导入：优先用 module_imports 的完整点分名。import a.b（无 as）时
    # import_map 只保留顶层名 a，会把子模块依赖误并到包根 __init__；module_imports 保留完整
    # a.b。对每个完整名按最长前缀在仓库内解析（import a.b.c 且仓库只有 a.b → 连 a.b）。
    whole_module_targets = getattr(imp, "module_imports", None) or list(imp.import_map.values())
    for dotted in whole_module_targets:
        parts = dotted.split(".")
        for i in range(len(parts), 0, -1):
            tgt = index.get(".".join(parts[:i]))
            if tgt is not None:
                add(tgt.id, "*module*")
                break

    # from-import：契约形态为 {local: (module_dotted, orig_name)}；
    # 对 §4.2 文本中出现的 {local: "a.b.f"} 字符串形态做防御性兼容。
    for _local, val in imp.from_import_map.items():
        if isinstance(val, (tuple, list)) and len(val) == 2:
            module_dotted, orig = str(val[0]), str(val[1])
        elif isinstance(val, str):
            if "." in val:
                module_dotted, orig = val.rsplit(".", 1)
            else:
                module_dotted, orig = "", val
        else:
            continue
        full = f"{module_dotted}.{orig}" if module_dotted else orig
        if full in index:                     # from pkg import submodule（导入子模块）
            add(index[full].id, "*module*")
        elif module_dotted in index:          # from module import symbol（导入符号）
            add(index[module_dotted].id, orig)
        # 否则目标在仓库外，跳过

    for tid in order:
        store.merge_edge(mid, "IMPORTS", tid, {"names": names_by_target[tid]})


def build_graph(
    modules: list[ModuleFacts],
    calls: list[tuple[str, str, dict]],
    gitdata: dict,
    repo_name: str,
) -> GraphStore:
    """组装完整属性图并返回 GraphStore。

    参数：
      modules  — ast_extractor 产物（已含 endpoints 回填、call_sites）。
      calls    — callgraph.build_calls 产物：(src_function_id, dst_function_id,
                 {'count', 'call_sites'})。
      gitdata  — git_extractor.extract_git 产物；keys: commits/issues
                 (list[dict 含 id]) 与 modifies/touches/fixes
                 (list[tuple[src_id, dst_id, props]])。
      repo_name — 仓库名（节点 ID 前缀，已内嵌于各 facts 的 repo/id，此处仅备用）。
    """
    store = GraphStore()
    index = build_module_index(modules)   # dotted → ModuleFacts（仅用于 IMPORTS 解析）

    for m in modules:
        mid = m.id
        store.merge_node(mid, "Module", {
            "repo": m.repo,
            "path": m.relpath,
            "name": m.name,
            "package": m.package,
            "loc": m.loc,
            "docstring": _first_line(m.docstring),
            "external_imports": list(m.imports.external_imports),
        })

        class_qualnames = {c.qualname for c in m.classes}

        for c in m.classes:
            cid = symbol_id(m.repo, m.relpath, c.qualname)
            store.merge_node(cid, "Class", {
                "repo": m.repo,
                "qualname": c.qualname,
                "file": m.relpath,
                "span_start": c.span_start,
                "span_end": c.span_end,
                "docstring": _first_line(c.docstring),
                "bases": list(c.bases),
            })
            store.merge_edge(mid, "CONTAINS", cid)

        for fn in m.functions:
            fid = symbol_id(m.repo, m.relpath, fn.qualname)
            store.merge_node(fid, "Function", {
                "repo": m.repo,
                "qualname": fn.qualname,
                "file": m.relpath,
                "span_start": fn.span_start,
                "span_end": fn.span_end,
                "signature": fn.signature,
                "is_async": fn.is_async,
                "is_method": fn.is_method,
                "is_endpoint": fn.is_endpoint,
                "http_method": fn.http_method,
                "route_path": fn.route_path,
                "docstring": _first_line(fn.docstring),
            })
            # CONTAINS 归属：qualname 含 '.' 且其前缀是本模块某个类 → 挂 Class，否则挂 Module。
            # （嵌套函数的前缀是函数名，不在 class_qualnames 中，故正确落到 Module。）
            container_id = mid
            if "." in fn.qualname:
                prefix = fn.qualname.rsplit(".", 1)[0]
                if prefix in class_qualnames:
                    container_id = symbol_id(m.repo, m.relpath, prefix)
            store.merge_edge(container_id, "CONTAINS", fid)

        _add_imports(store, m, index)

    # CALLS（Function→Function），props 直接透传
    for src, dst, props in calls:
        store.merge_edge(src, "CALLS", dst, dict(props) if props else None)

    # Git 层节点与边，全部来自参数
    for c in gitdata.get("commits", []) or []:
        cid = c["id"]
        store.merge_node(cid, "Commit", {k: v for k, v in c.items() if k not in ("id", "label")})
    for issue in gitdata.get("issues", []) or []:
        iid = issue["id"]
        store.merge_node(iid, "Issue", {k: v for k, v in issue.items() if k not in ("id", "label")})
    for src, dst, props in gitdata.get("modifies", []) or []:
        store.merge_edge(src, "MODIFIES", dst, dict(props) if props else None)
    for src, dst, props in gitdata.get("touches", []) or []:
        store.merge_edge(src, "TOUCHES", dst, dict(props) if props else None)
    for src, dst, props in gitdata.get("fixes", []) or []:
        store.merge_edge(src, "FIXES", dst, dict(props) if props else None)

    return store
