"""RepoGraph 共享数据契约（v0.1）。

本文件是全部抽取/存储/检索/可视化模块之间的唯一接口来源，
对应技术设计文档 §3（图谱 Schema）与 §4（确定性抽取层）。
所有并行开发的模块只依赖本文件与 config.py，不互相 import。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Literal, Optional

# ---------------------------------------------------------------------------
# 节点 Label 与边类型（封闭枚举，§3.2 / §3.3）
# ---------------------------------------------------------------------------

NODE_LABELS = ("Module", "Class", "Function", "Commit", "Issue", "Concept")

EDGE_TYPES = (
    "CONTAINS",    # Module→Class, Module→Function, Class→Function
    "IMPORTS",     # Module→Module（仅仓库内）
    "CALLS",       # Function→Function（仅可静态判定）
    "MODIFIES",    # Commit→Function
    "TOUCHES",     # Commit→Module（函数级映射失败的兜底）
    "FIXES",       # Commit→Issue
    "PROPOSES",    # Issue→Concept
    "DESCRIBES",   # Commit→Concept
    "IMPLEMENTS",  # Function|Module→Concept
)

ConceptType = Literal["design_decision", "domain_concept", "constraint"]

# ---------------------------------------------------------------------------
# 规范 ID 生成（§3.4）—— ID 即 MERGE 合并键，保证幂等
# ---------------------------------------------------------------------------


def module_id(repo: str, relpath: str) -> str:
    """Module ID：{repo}::{relpath}，relpath 必须为 POSIX 分隔符。"""
    return f"{repo}::{_posix(relpath)}"


def symbol_id(repo: str, relpath: str, qualname: str) -> str:
    """Class / Function ID：{repo}::{relpath}::{qualname}。"""
    return f"{repo}::{_posix(relpath)}::{qualname}"


def commit_id(repo: str, sha: str) -> str:
    return f"{repo}::commit::{sha}"


def issue_id(repo: str, number: int) -> str:
    return f"{repo}::issue::{number}"


def concept_id(slug: str) -> str:
    return f"concept::{slug}"


def _posix(p: str) -> str:
    return p.replace("\\", "/")


# ---------------------------------------------------------------------------
# AST 抽取产物（§4.1）—— ast_extractor 产出，callgraph / store 消费
# ---------------------------------------------------------------------------

# 归一化调用点形态（§4.2 Pass 2 的输入）：
#   ("name", callee)          f(...)              —— 裸名调用
#   ("attr", base, attr)      m.f(...)            —— base 为 Name 的属性调用
#   ("self", attr)            self.f(...)/cls.f(...) —— 方法内自调用
#   ("other", repr)           其余一律 unresolved，不建边
CallShape = tuple


@dataclass
class CallSite:
    lineno: int
    shape: CallShape


@dataclass
class FunctionFacts:
    qualname: str                    # ast 语义限定名，嵌套用 '.' 连接（如 AgentLoop.run）
    span_start: int                  # 含装饰器行（§4.1）
    span_end: int
    signature: str                   # 由 node.args 重建，不做类型求值
    is_async: bool
    is_method: bool                  # 直接父作用域为 ClassDef
    docstring: Optional[str]         # 完整 docstring 原文
    decorators: list[str] = field(default_factory=list)       # 装饰器点分名，如 "app.get"
    decorator_first_arg: list[Optional[str]] = field(default_factory=list)
    # 与 decorators 一一对应：装饰器调用的首个位置参数若为字符串字面量则记录，否则 None
    call_sites: list[CallSite] = field(default_factory=list)  # 函数体内全部调用点
    # 端点标记（endpoints.py 回填，§4.3）
    is_endpoint: bool = False
    http_method: Optional[str] = None
    route_path: Optional[str] = None


@dataclass
class ClassFacts:
    qualname: str
    span_start: int
    span_end: int
    docstring: Optional[str]
    bases: list[str] = field(default_factory=list)   # 按源码书写的点分名（后续在仓库内解析）


@dataclass
class ImportFacts:
    """单个模块的导入映射（§4.2 Pass 1 的输入）。"""
    import_map: dict[str, str] = field(default_factory=dict)
    # import a.b as c        → {"c": "a.b"}；import a.b → {"a": "a"}（顶层名绑定）
    from_import_map: dict[str, tuple[str, str]] = field(default_factory=dict)
    # from a.b import f as g → {"g": ("a.b", "f")}；相对导入需已解析为绝对点分名
    external_imports: list[str] = field(default_factory=list)
    # 解析后不在仓库内的目标（点分名），只记属性不建边


@dataclass
class ModuleFacts:
    repo: str
    relpath: str                     # POSIX
    name: str                        # 文件名去扩展名
    package: str                     # 点分包路径（不含模块名），顶层为 ""
    dotted: str                      # 完整点分模块名（如 orch.scheduler.core）
    loc: int
    docstring: Optional[str]
    classes: list[ClassFacts] = field(default_factory=list)
    functions: list[FunctionFacts] = field(default_factory=list)  # 含方法（is_method=True）
    imports: ImportFacts = field(default_factory=ImportFacts)

    @property
    def id(self) -> str:
        return module_id(self.repo, self.relpath)


# ---------------------------------------------------------------------------
# 点分模块名 ↔ 仓库相对路径 的解析索引
# ---------------------------------------------------------------------------


def dotted_from_relpath(relpath: str, src_roots: tuple[str, ...] = ("src",)) -> str:
    """'src/orch/scheduler/core.py' → 'orch.scheduler.core'；
    包 __init__.py 映射为包名本身；src_roots 中的前缀目录被剥除。"""
    p = _posix(relpath)
    for root in src_roots:
        if p.startswith(root + "/"):
            p = p[len(root) + 1:]
            break
    if p.endswith("/__init__.py"):
        p = p[: -len("/__init__.py")]
    elif p.endswith(".py"):
        p = p[:-3]
    return p.replace("/", ".")


def build_module_index(modules: list[ModuleFacts]) -> dict[str, ModuleFacts]:
    """dotted → ModuleFacts。callgraph Pass 1 的全局符号表基础。"""
    return {m.dotted: m for m in modules}


# ---------------------------------------------------------------------------
# GraphStore 抽象（§6 存储层的本地实现契约）
#
# 说明：设计文档以 PostgreSQL + Apache AGE 为目标存储；本仓库 v0.1 提供
# 同一抽象下的本地后端（内存 + JSON 持久化），store/age.py 保留 AGE 接口
# 与 DDL 作为部署形态切换点（文档 §2.3 / §6.3 的切换预案反向应用）。
# ---------------------------------------------------------------------------


class GraphStore:
    """本地属性图存储。merge 语义以 ID 为合并键，重跑幂等（§3.4）。"""

    def __init__(self) -> None:
        self._nodes: dict[str, dict] = {}                     # id → {id,label,**props}
        self._edges: dict[tuple[str, str, str], dict] = {}    # (src,type,dst) → props

    def merge_node(self, node_id: str, label: str, properties: dict | None = None) -> None:
        assert label in NODE_LABELS, f"unknown label: {label}"
        cur = self._nodes.setdefault(node_id, {"id": node_id, "label": label})
        if properties:
            cur.update(properties)

    def merge_edge(self, src: str, etype: str, dst: str, properties: dict | None = None) -> None:
        assert etype in EDGE_TYPES, f"unknown edge type: {etype}"
        cur = self._edges.setdefault((src, etype, dst), {})
        if properties:
            cur.update(properties)

    def nodes(self, label: str | None = None) -> Iterator[dict]:
        for n in self._nodes.values():
            if label is None or n["label"] == label:
                yield n

    def edges(self, etype: str | None = None) -> Iterator[tuple[str, str, str, dict]]:
        for (src, t, dst), props in self._edges.items():
            if etype is None or t == etype:
                yield src, t, dst, props

    def get_node(self, node_id: str) -> Optional[dict]:
        return self._nodes.get(node_id)

    def counts(self) -> dict:
        by_label: dict[str, int] = {}
        for n in self._nodes.values():
            by_label[n["label"]] = by_label.get(n["label"], 0) + 1
        by_type: dict[str, int] = {}
        for (_, t, _) in self._edges:
            by_type[t] = by_type.get(t, 0) + 1
        return {"nodes": by_label, "edges": by_type,
                "total_nodes": len(self._nodes), "total_edges": len(self._edges)}

    # 持久化：save/load 为 JSON（output/graph.json），格式如下——
    # {"nodes": [{...}], "edges": [{"src":..,"type":..,"dst":..,"properties":{..}}]}
    def save(self, path: str) -> None:
        import json, os
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        data = {
            "nodes": list(self._nodes.values()),
            "edges": [
                {"src": s, "type": t, "dst": d, "properties": p}
                for (s, t, d), p in self._edges.items()
            ],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)

    @classmethod
    def load(cls, path: str) -> "GraphStore":
        import json
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        st = cls()
        for n in data["nodes"]:
            props = {k: v for k, v in n.items() if k not in ("id", "label")}
            st.merge_node(n["id"], n["label"], props)
        for e in data["edges"]:
            st.merge_edge(e["src"], e["type"], e["dst"], e.get("properties") or {})
        return st


# ---------------------------------------------------------------------------
# 质量指标载体（§4.6）—— 各抽取模块把计数写进来，stats.py 汇总输出
# ---------------------------------------------------------------------------


@dataclass
class PipelineStats:
    call_resolved: int = 0
    call_unresolved: int = 0
    modifies_commits: int = 0        # 产生 ≥1 条 MODIFIES 的提交数
    code_commits: int = 0            # 变更过 .py 的提交总数
    dangling_modifies: int = 0
    parse_skips_worktree: int = 0
    parse_skips_blob: int = 0
    blob_cache_hits: int = 0
    blob_cache_misses: int = 0
    semantic_extracted: int = 0
    semantic_rejected: dict = field(default_factory=dict)  # reason → count

    def as_dict(self) -> dict:
        total_calls = self.call_resolved + self.call_unresolved
        blob_total = self.blob_cache_hits + self.blob_cache_misses
        return {
            "call_resolved_rate": round(self.call_resolved / total_calls, 4) if total_calls else None,
            "call_resolved": self.call_resolved,
            "call_unresolved": self.call_unresolved,
            "modifies_coverage": round(self.modifies_commits / self.code_commits, 4) if self.code_commits else None,
            "dangling_modifies": self.dangling_modifies,
            "parse_skips": {"worktree": self.parse_skips_worktree, "blob": self.parse_skips_blob},
            "blob_cache_hit_rate": round(self.blob_cache_hits / blob_total, 4) if blob_total else None,
            "semantic_extracted": self.semantic_extracted,
            "extraction_reject_by_reason": self.semantic_rejected,
        }
