"""AST 结构抽取（技术设计文档 §4.1）。

对工作树内所有 `.py` 文件做确定性解析，产出 `ModuleFacts`（含类、函数、
导入映射、逐函数调用点）。结构关系抽取错误率为零是本层的硬指标，因此本
模块不做任何猜测：调用点只做形态归一化（shape），真正的调用边解析留给
`callgraph.py`（§4.2 Pass 2）。

关键实现约定（§4.1）：
- 限定名（qualname）由自维护的作用域栈递归生成——`ast.walk` 不保序，
  无法正确产出嵌套限定名，故用显式递归下降。
- 行号跨度 `span_start` 计入装饰器行，否则纯改装饰器的提交会漏掉映射。
- 调用点归属**最内层**函数（嵌套函数拥有自己的调用点集合）。
- `external_imports` 的仓库内/外判定需要全量模块集合，故在
  `extract_worktree` 收齐所有模块后统一回填。
"""
from __future__ import annotations

import ast
import os

from repograph.models import (
    CallSite,
    ClassFacts,
    FunctionFacts,
    ImportFacts,
    ModuleFacts,
    dotted_from_relpath,
)


def _posix(p: str) -> str:
    return p.replace("\\", "/")


# ---------------------------------------------------------------------------
# 圈复杂度（§4.3 / 裁定 D-03）——纯语法量，遍历函数体计决策点
#
# 白名单（一次写死，避免实现分叉，保守近似 McCabe；assert / with 按惯例不计）：
#   If / For / AsyncFor / While / ExceptHandler / IfExp（三元）/ match_case（每 case）
#   / BoolOp（and·or 每个**额外**操作数 +1）/ 推导式的每个 if 过滤子。
# 复杂度 = 1 + 决策点数。**嵌套函数/类各自归属**：遍历不下沉进嵌套 def/class 作用域。
# ---------------------------------------------------------------------------

_SCOPE_BOUNDARY = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_BRANCH_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp)
_MATCH_CASE = getattr(ast, "match_case", ())   # Python 3.10+；旧版无 match 语句


def _iter_scope_nodes(func_node):
    """产出 func_node 函数体内全部 AST 节点，**不下沉**进嵌套 def/class（各自归属）。

    嵌套作用域节点本身会被产出（用于判定它不是决策点），但其子树不再展开。
    """
    stack = list(getattr(func_node, "body", ()))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, _SCOPE_BOUNDARY):
            continue                                    # 嵌套作用域：产出自身、不展开子树
        stack.extend(ast.iter_child_nodes(node))


def count_cyclomatic(func_node) -> int:
    """函数体圈复杂度 = 1 + 决策点数（白名单见上；嵌套 def/class 不计入本函数）。"""
    count = 1
    for node in _iter_scope_nodes(func_node):
        if isinstance(node, _BRANCH_NODES):
            count += 1
        elif isinstance(node, ast.BoolOp):
            count += len(node.values) - 1               # a and b and c → +2
        elif isinstance(node, ast.comprehension):
            count += len(node.ifs)                       # 推导式每个 if 过滤子
        elif _MATCH_CASE and isinstance(node, _MATCH_CASE):
            count += 1                                   # match 每个 case
    return count


# ---------------------------------------------------------------------------
# 单模块递归下降访问器
# ---------------------------------------------------------------------------


class _ScopeVisitor:
    """递归下降遍历，维护作用域栈生成 qualname，并把调用点归属最内层函数。

    scope: list[(name, is_class)] —— 用于拼 qualname 与判定 is_method。
    func_stack: list[FunctionFacts] —— 栈顶即当前收集调用点的函数。
    """

    def __init__(self, mf: ModuleFacts) -> None:
        self.mf = mf
        self.scope: list[tuple[str, bool]] = []
        self.func_stack: list[FunctionFacts] = []

    # -- qualname / span 工具 --------------------------------------------
    def _qual(self, name: str) -> str:
        if self.scope:
            return ".".join(n for n, _ in self.scope) + "." + name
        return name

    @staticmethod
    def _span_start(node: ast.AST) -> int:
        # 装饰器行计入跨度：min(node.lineno, *装饰器行)
        lines = [node.lineno]
        for dec in getattr(node, "decorator_list", ()):
            lines.append(dec.lineno)
        return min(lines)

    # -- 遍历分派 --------------------------------------------------------
    def walk(self, node: ast.AST) -> None:
        if isinstance(node, ast.ClassDef):
            self._enter_class(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._enter_function(node)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            self._collect_import(node)
        else:
            if isinstance(node, ast.Call) and self.func_stack:
                self._collect_call(node)
            for child in ast.iter_child_nodes(node):
                self.walk(child)

    # -- 类 --------------------------------------------------------------
    def _enter_class(self, node: ast.ClassDef) -> None:
        cf = ClassFacts(
            qualname=self._qual(node.name),
            span_start=self._span_start(node),
            span_end=node.end_lineno,
            docstring=ast.get_docstring(node),
            bases=[ast.unparse(b) for b in node.bases],
        )
        self.mf.classes.append(cf)
        self.scope.append((node.name, True))
        for child in node.body:
            self.walk(child)
        self.scope.pop()

    # -- 函数 ------------------------------------------------------------
    def _enter_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        is_method = bool(self.scope) and self.scope[-1][1]
        ff = FunctionFacts(
            qualname=self._qual(node.name),
            span_start=self._span_start(node),
            span_end=node.end_lineno,
            signature=self._signature(node),
            is_async=isinstance(node, ast.AsyncFunctionDef),
            is_method=is_method,
            docstring=ast.get_docstring(node),
            cyclomatic=count_cyclomatic(node),          # §4.3 / D-03：圈复杂度顺带计数
        )
        for dec in node.decorator_list:
            name, first_arg = self._decorator(dec)
            ff.decorators.append(name)
            ff.decorator_first_arg.append(first_arg)
        self.mf.functions.append(ff)

        self.scope.append((node.name, False))
        self.func_stack.append(ff)
        # 只遍历函数体：装饰器/参数默认值/注解不计入调用点（定义期求值）。
        for child in node.body:
            self.walk(child)
        self.func_stack.pop()
        self.scope.pop()

    @staticmethod
    def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        # 由 node.args 重建：ast.unparse 已正确处理 posonly / kwonly /
        # *args / **kwargs / 默认值 / 注解。前缀 async，含返回注解，不带 def。
        params = ast.unparse(node.args)
        sig = f"{node.name}({params})"
        if node.returns is not None:
            sig += " -> " + ast.unparse(node.returns)
        if isinstance(node, ast.AsyncFunctionDef):
            sig = "async " + sig
        return sig

    @staticmethod
    def _decorator(dec: ast.expr) -> tuple[str, str | None]:
        # 点分名：Call 取其 func 部分，否则取整个表达式。
        # decorator_first_arg：Call 首个位置参数为字符串字面量时记录，否则 None。
        if isinstance(dec, ast.Call):
            name = ast.unparse(dec.func)
            first_arg = None
            if dec.args:
                first = dec.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    first_arg = first.value
            return name, first_arg
        return ast.unparse(dec), None

    # -- 调用点 ----------------------------------------------------------
    def _collect_call(self, node: ast.Call) -> None:
        self.func_stack[-1].call_sites.append(
            CallSite(lineno=node.lineno, shape=self._call_shape(node))
        )

    @staticmethod
    def _call_shape(node: ast.Call) -> tuple:
        func = node.func
        if isinstance(func, ast.Name):
            return ("name", func.id)
        if isinstance(func, ast.Attribute):
            base = func.value
            if isinstance(base, ast.Name):
                if base.id in ("self", "cls"):
                    return ("self", func.attr)
                return ("attr", base.id, func.attr)
        return ("other", ast.unparse(func)[:80])

    # -- 导入 ------------------------------------------------------------
    def _collect_import(self, node: ast.Import | ast.ImportFrom) -> None:
        im = self.mf.imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                # 完整点分名始终记录，供 build 建 IMPORTS 边解析真实子模块依赖
                # （import a.b 无 as 时 import_map 只留顶层名 a，会丢失 a.b 这一子模块目标）。
                im.module_imports.append(alias.name)
                if alias.asname:
                    # import a.b as c → {"c": "a.b"}
                    im.import_map[alias.asname] = alias.name
                else:
                    # import a.b → 绑定顶层名 a → {"a": "a"}
                    top = alias.name.split(".")[0]
                    im.import_map[top] = top
        else:  # ast.ImportFrom
            if node.level and node.level > 0:
                base = self._resolve_relative(node.level, node.module)
            else:
                base = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    continue  # 星号导入无法映射具体符号
                bound = alias.asname or alias.name
                # from a.b import f as g → {"g": ("a.b", "f")}
                im.from_import_map[bound] = (base, alias.name)

    def _resolve_relative(self, level: int, module: str | None) -> str:
        # 相对导入按当前模块所在包解析为绝对点分名。
        # 包 __init__.py 的“当前包”是它自身；普通模块是其父包。
        if self.mf.relpath.endswith("__init__.py"):
            pkg = self.mf.dotted
        else:
            pkg = self.mf.package
        parts = pkg.split(".") if pkg else []
        strip = level - 1  # level=1 → 当前包；level=n → 上溯 n-1 层
        if strip > 0:
            parts = parts[: len(parts) - strip] if strip <= len(parts) else []
        anchor = ".".join(parts)
        if module:
            return anchor + "." + module if anchor else module
        return anchor


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------


def extract_module(
    repo: str, relpath: str, source: str, src_roots: tuple[str, ...] = ("src",)
) -> ModuleFacts | None:
    """解析单个 .py 源码为 ModuleFacts。SyntaxError → 返回 None（记入 skip）。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    relpath = _posix(relpath)
    dotted = dotted_from_relpath(relpath, tuple(src_roots))
    base = relpath.rsplit("/", 1)[-1]
    name = base[:-3] if base.endswith(".py") else base
    package = dotted.rsplit(".", 1)[0] if "." in dotted else ""
    loc = len(source.splitlines())

    mf = ModuleFacts(
        repo=repo,
        relpath=relpath,
        name=name,
        package=package,
        dotted=dotted,
        loc=loc,
        docstring=ast.get_docstring(tree),
        imports=ImportFacts(),
    )
    visitor = _ScopeVisitor(mf)
    for stmt in tree.body:
        visitor.walk(stmt)
    return mf


def extract_worktree(
    repo_root: str, repo_name: str, settings
) -> tuple[list[ModuleFacts], int]:
    """遍历工作树抽取全部模块，返回 (模块列表, parse_skips 数)。

    - 排除 settings.exclude_dirs（目录名精确匹配），只收 .py。
    - relpath 用 POSIX 分隔符；dotted 剥除 settings.src_roots 前缀。
    - 收齐全部模块后统一回填 external_imports（仓库内/外判定）。
    """
    exclude = set(settings.exclude_dirs)
    src_roots = tuple(settings.src_roots)
    modules: list[ModuleFacts] = []
    parse_skips = 0

    for dirpath, dirnames, filenames in os.walk(repo_root):
        # 就地裁剪，阻止 os.walk 递归进被排除目录
        dirnames[:] = [d for d in dirnames if d not in exclude]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            full = os.path.join(dirpath, fn)
            relpath = _posix(os.path.relpath(full, repo_root))
            try:
                with open(full, "r", encoding="utf-8") as f:
                    source = f.read()
            except (OSError, UnicodeDecodeError):
                parse_skips += 1
                continue
            mf = extract_module(repo_name, relpath, source, src_roots)
            if mf is None:
                parse_skips += 1
                continue
            modules.append(mf)

    _backfill_external_imports(modules)
    return modules, parse_skips


def _backfill_external_imports(modules: list[ModuleFacts]) -> None:
    """收齐全部模块后，逐模块判定导入目标是否在仓库内，回填 external_imports。"""
    repo_dotted = {m.dotted for m in modules}
    # 仓库内包（含中间层）的点分名集合，用于判定 `import pkg` 类前缀命中
    repo_pkgs: set[str] = set()
    for d in repo_dotted:
        parts = d.split(".")
        for i in range(1, len(parts)):
            repo_pkgs.add(".".join(parts[:i]))

    def is_internal(dotted: str) -> bool:
        return bool(dotted) and (dotted in repo_dotted or dotted in repo_pkgs)

    for m in modules:
        ext: list[str] = []
        seen: set[str] = set()

        def add(target: str) -> None:
            if target and target not in seen:
                seen.add(target)
                ext.append(target)

        for target in m.imports.import_map.values():
            if not is_internal(target):
                add(target)
        for base, sym in m.imports.from_import_map.values():
            full = (base + "." + sym) if base else sym
            if is_internal(base) or is_internal(full):
                continue
            add(base if base else full)

        m.imports.external_imports = ext
