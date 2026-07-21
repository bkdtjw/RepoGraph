"""ast_extractor（§4.1）与 endpoints（§4.3）自测。

真实运行、纯 assert、不依赖 pytest：
    cd <项目根> && python tests/test_ast_extractor.py
"""
import os
import sys
import tempfile

sys.path.insert(0, "src")

from repograph.config import Settings
from repograph.extract import ast_extractor, endpoints
from repograph.models import ClassFacts, FunctionFacts


# 一个覆盖嵌套类 / 异步函数 / 装饰器 / 相对导入 / 各类调用点的源码
RICH_SOURCE = '''\
"""Module docstring line one."""
from __future__ import annotations
import os
import a.b as ab
import x.y
from .sibling import helper
from ..other import thing as t
from collections import OrderedDict

app = object()


@app.get("/items")
async def list_items(limit: int = 10, *args, flag: bool = False, **kw) -> list:
    """List items."""
    helper()
    result = os.getpid()

    def inner():
        ab.compute()

    inner()
    return result


class Service:
    """Service class."""

    def __init__(self, name):
        self.name = name

    @app.post("/run")
    async def run(self, x: int) -> int:
        self.helper(x)
        other.method()
        return x

    class Inner:
        def deep(self):
            weird[0].call()
'''


def _by_qual(items):
    return {i.qualname: i for i in items}


def test_extract_module_fields():
    mf = ast_extractor.extract_module(
        "testrepo", "src/pkg/sub/mod.py", RICH_SOURCE, ("src",)
    )
    assert mf is not None
    # 模块级属性
    assert mf.repo == "testrepo"
    assert mf.relpath == "src/pkg/sub/mod.py"
    assert mf.name == "mod"
    assert mf.dotted == "pkg.sub.mod"
    assert mf.package == "pkg.sub"
    assert mf.docstring == "Module docstring line one."
    assert mf.loc == len(RICH_SOURCE.splitlines())

    # ---- 导入映射 ----
    im = mf.imports
    assert im.import_map["ab"] == "a.b"          # import a.b as ab
    assert im.import_map["x"] == "x"             # import x.y（无 as → 顶层名）
    assert im.import_map["os"] == "os"           # import os
    assert im.from_import_map["OrderedDict"] == ("collections", "OrderedDict")
    assert im.from_import_map["annotations"] == ("__future__", "annotations")
    # 相对导入解析为绝对点分名
    assert im.from_import_map["helper"] == ("pkg.sub.sibling", "helper")   # from .sibling
    assert im.from_import_map["t"] == ("pkg.other", "thing")               # from ..other
    print("[ok] module-level attrs + imports")

    # ---- 函数 ----
    funcs = _by_qual(mf.functions)
    assert set(funcs) == {
        "list_items",
        "list_items.inner",
        "Service.__init__",
        "Service.run",
        "Service.Inner.deep",
    }, sorted(funcs)

    li = funcs["list_items"]
    assert li.is_async is True
    assert li.is_method is False
    assert li.docstring == "List items."
    assert li.decorators == ["app.get"]
    assert li.decorator_first_arg == ["/items"]
    assert li.signature == (
        "async list_items(limit: int=10, *args, flag: bool=False, **kw) -> list"
    ), li.signature
    # span_start 计入装饰器行：该行应是 @app.get 装饰器
    src_lines = RICH_SOURCE.splitlines()
    assert src_lines[li.span_start - 1].strip().startswith("@app.get"), li.span_start
    assert li.span_end > li.span_start
    # 调用点：helper() / os.getpid() / inner()；ab.compute() 属于内层 inner
    shapes = [cs.shape for cs in li.call_sites]
    assert ("name", "helper") in shapes
    assert ("attr", "os", "getpid") in shapes
    assert ("name", "inner") in shapes
    assert ("attr", "ab", "compute") not in shapes
    assert len(li.call_sites) == 3, shapes
    print("[ok] list_items function facts + call sites")

    inner = funcs["list_items.inner"]
    assert inner.is_method is False
    assert [cs.shape for cs in inner.call_sites] == [("attr", "ab", "compute")]
    print("[ok] nested function ownership of call site")

    run = funcs["Service.run"]
    assert run.is_async is True
    assert run.is_method is True
    assert run.signature == "async run(self, x: int) -> int", run.signature
    run_shapes = [cs.shape for cs in run.call_sites]
    assert ("self", "helper") in run_shapes            # self.helper(x)
    assert ("attr", "other", "method") in run_shapes   # other.method()
    print("[ok] method facts + self-call shape")

    init = funcs["Service.__init__"]
    assert init.is_method is True
    assert init.call_sites == []  # 仅赋值，无调用

    deep = funcs["Service.Inner.deep"]
    assert deep.is_method is True                       # 直接父作用域是 Inner 类
    assert [cs.shape for cs in deep.call_sites][0][0] == "other"  # weird[0].call()
    print("[ok] deeply nested method + 'other' call shape")

    # ---- 类 ----
    classes = _by_qual(mf.classes)
    assert set(classes) == {"Service", "Service.Inner"}, sorted(classes)
    assert classes["Service"].docstring == "Service class."
    assert classes["Service"].bases == []
    print("[ok] class facts (nested class qualname)")


def test_syntax_error_returns_none():
    assert ast_extractor.extract_module("r", "src/bad.py", "def (:\n", ("src",)) is None
    print("[ok] SyntaxError → None")


def test_bases_and_relative_from_package_init():
    src = "from . import submod\nfrom .deep import fn\n"
    mf = ast_extractor.extract_module("r", "src/pkg/__init__.py", src, ("src",))
    assert mf.dotted == "pkg"
    # __init__.py 的“当前包”是它自身 → 相对导入锚定到 pkg
    assert mf.imports.from_import_map["submod"] == ("pkg", "submod")
    assert mf.imports.from_import_map["fn"] == ("pkg.deep", "fn")

    src2 = "class Child(Base, mixins.Loud):\n    pass\n"
    mf2 = ast_extractor.extract_module("r", "src/c.py", src2, ("src",))
    assert mf2.classes[0].bases == ["Base", "mixins.Loud"]
    print("[ok] __init__ relative import anchor + class bases")


def _write(root, relpath, content):
    full = os.path.join(root, relpath.replace("/", os.sep))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


def test_extract_worktree_and_external():
    settings = Settings()
    with tempfile.TemporaryDirectory() as root:
        # 仓库内两个模块 + 一个跨模块导入 + 一个外部导入
        _write(root, "src/pkg/a.py",
               "import os\nfrom pkg import b\nfrom pkg.b import go\n")
        _write(root, "src/pkg/b.py", "def go():\n    pass\n")
        # 应被排除：tests/ 目录 + __pycache__
        _write(root, "tests/test_x.py", "import totally_broken syntax(((\n")
        _write(root, "src/__pycache__/junk.py", "x = 1\n")
        # 一个语法错误文件（计入 parse_skips）
        _write(root, "src/pkg/broken.py", "def oops(:\n")
        # 非 .py 不收
        _write(root, "src/readme.txt", "not python\n")

        modules, skips = ast_extractor.extract_worktree(root, "myrepo", settings)

        dotted = {m.dotted for m in modules}
        assert dotted == {"pkg.a", "pkg.b"}, dotted   # tests/ 与 __pycache__ 被排除
        assert skips == 1, skips                       # 仅 src/pkg/broken.py

        # relpath 为 POSIX
        for m in modules:
            assert "\\" not in m.relpath

        a = next(m for m in modules if m.dotted == "pkg.a")
        # os 外部；pkg / pkg.b 仓库内（不进 external）
        assert "os" in a.imports.external_imports
        assert "pkg" not in a.imports.external_imports
        assert "pkg.b" not in a.imports.external_imports
        assert a.imports.external_imports == ["os"], a.imports.external_imports
        print("[ok] extract_worktree: exclude dirs / parse_skips / POSIX / external classify")


def test_mark_endpoints():
    settings = Settings()
    mf = ast_extractor.extract_module("r", "src/api.py", RICH_SOURCE, ("src",))
    n = endpoints.mark_endpoints([mf], settings)
    assert n == 2, n  # list_items (@app.get) + Service.run (@app.post)

    funcs = _by_qual(mf.functions)
    li = funcs["list_items"]
    assert li.is_endpoint is True
    assert li.http_method == "GET"
    assert li.route_path == "/items"

    run = funcs["Service.run"]
    assert run.is_endpoint is True
    assert run.http_method == "POST"
    assert run.route_path == "/run"

    # 非端点函数不受影响
    assert funcs["Service.__init__"].is_endpoint is False
    assert funcs["Service.__init__"].http_method is None

    # 幂等：再跑一次结果一致
    n2 = endpoints.mark_endpoints([mf], settings)
    assert n2 == 2
    print("[ok] mark_endpoints: method/path + non-endpoint untouched + idempotent")


if __name__ == "__main__":
    test_extract_module_fields()
    test_syntax_error_returns_none()
    test_bases_and_relative_from_package_init()
    test_extract_worktree_and_external()
    test_mark_endpoints()
    print("\nALL TESTS PASSED")
