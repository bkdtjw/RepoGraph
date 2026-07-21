"""callgraph.build_calls 自测（§4.2）。

手工构造 3 个 ModuleFacts，覆盖：
  - 跨模块 from-import 裸名调用       (rule 1b)
  - 本模块顶层函数裸名调用            (rule 1a)
  - 导入模块别名的属性调用            (rule 2a)
  - 本模块类名的属性方法调用          (rule 2b)
  - self 本类方法调用（含递归/合并计数）(rule 3a)
  - self 基类方法调用（跨模块单层）    (rule 3b)
  - 无法解析的变量属性调用 / 未知裸名  (rule 4 / unresolved)

不依赖 pytest：纯 assert，运行 `python tests/test_callgraph.py`。
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from repograph.models import (  # noqa: E402
    ModuleFacts,
    ClassFacts,
    FunctionFacts,
    CallSite,
    ImportFacts,
    PipelineStats,
    symbol_id,
)
from repograph.extract import callgraph  # noqa: E402

REPO = "repo"


def _fn(qualname, call_sites, is_method=False):
    return FunctionFacts(
        qualname=qualname,
        span_start=1,
        span_end=2,
        signature="()",
        is_async=False,
        is_method=is_method,
        docstring=None,
        call_sites=call_sites,
    )


def _cls(qualname, bases=()):
    return ClassFacts(
        qualname=qualname,
        span_start=1,
        span_end=2,
        docstring=None,
        bases=list(bases),
    )


def _build_modules():
    # -- Module util (pkg.util) ------------------------------------------
    util = ModuleFacts(
        repo=REPO,
        relpath="src/pkg/util.py",
        name="util",
        package="pkg",
        dotted="pkg.util",
        loc=30,
        docstring=None,
        classes=[_cls("Base")],
        functions=[
            _fn("helper", []),
            # compute() 裸名调用本模块顶层函数 helper （rule 1a）
            _fn("compute", [CallSite(5, ("name", "helper"))]),
            _fn("Base.shared", [], is_method=True),
        ],
        imports=ImportFacts(),
    )

    # -- Module core (pkg.core) ------------------------------------------
    core = ModuleFacts(
        repo=REPO,
        relpath="src/pkg/core.py",
        name="core",
        package="pkg",
        dotted="pkg.core",
        loc=40,
        docstring=None,
        classes=[_cls("Worker", bases=["Base"])],
        functions=[
            _fn(
                "Worker.run",
                [
                    CallSite(10, ("name", "helper")),        # rule 1b 跨模块 from-import
                    CallSite(11, ("self", "step")),          # rule 3a 本类方法
                    CallSite(12, ("self", "shared")),        # rule 3b 基类方法（跨模块）
                    CallSite(13, ("attr", "util", "compute")),  # rule 2a 导入模块别名
                    CallSite(14, ("other", "queue.pop()")),  # rule 4 unresolved
                    CallSite(15, ("name", "unknown")),       # unresolved（本地/导入均无）
                ],
                is_method=True,
            ),
            _fn(
                "Worker.step",
                [
                    CallSite(20, ("self", "run")),           # 递归/自调用保留
                    CallSite(21, ("self", "run")),           # 同一对合并计数
                ],
                is_method=True,
            ),
        ],
        imports=ImportFacts(
            import_map={"util": "pkg.util"},
            from_import_map={
                "helper": ("pkg.util", "helper"),
                "Base": ("pkg.util", "Base"),
            },
        ),
    )

    # -- Module app (pkg.app) --------------------------------------------
    app = ModuleFacts(
        repo=REPO,
        relpath="src/pkg/app.py",
        name="app",
        package="pkg",
        dotted="pkg.app",
        loc=20,
        docstring=None,
        classes=[_cls("Helpers")],
        functions=[
            _fn("main", [CallSite(30, ("attr", "Helpers", "do"))]),  # rule 2b 本模块类名
            _fn("Helpers.do", [], is_method=True),
        ],
        imports=ImportFacts(),
    )

    return [util, core, app]


def _sid(relpath, qualname):
    return symbol_id(REPO, relpath, qualname)


def main():
    modules = _build_modules()
    stats = PipelineStats()

    edges = callgraph.build_calls(modules, stats)

    # 归一化为 dict 便于断言
    got = {(s, d): props for (s, d, props) in edges}

    U = "src/pkg/util.py"
    C = "src/pkg/core.py"
    A = "src/pkg/app.py"

    expected = {
        # rule 1a：本模块顶层函数
        (_sid(U, "compute"), _sid(U, "helper")): (1, [5]),
        # rule 1b：跨模块 from-import
        (_sid(C, "Worker.run"), _sid(U, "helper")): (1, [10]),
        # rule 3a：本类方法
        (_sid(C, "Worker.run"), _sid(C, "Worker.step")): (1, [11]),
        # rule 3b：基类方法（Worker(Base) → pkg.util.Base.shared）
        (_sid(C, "Worker.run"), _sid(U, "Base.shared")): (1, [12]),
        # rule 2a：导入模块别名 util.compute
        (_sid(C, "Worker.run"), _sid(U, "compute")): (1, [13]),
        # 递归 + 合并计数：step → run 两次
        (_sid(C, "Worker.step"), _sid(C, "Worker.run")): (2, [20, 21]),
        # rule 2b：本模块类名 Helpers.do
        (_sid(A, "main"), _sid(A, "Helpers.do")): (1, [30]),
    }

    # 1) 边集合完全一致（无多余、无遗漏）
    assert set(got.keys()) == set(expected.keys()), (
        "边集合不一致\n"
        f"  多出: {set(got) - set(expected)}\n"
        f"  缺失: {set(expected) - set(got)}"
    )

    # 2) 每条边的 count 与 call_sites 精确匹配
    for key, (exp_count, exp_sites) in expected.items():
        props = got[key]
        assert props["count"] == exp_count, f"{key} count={props['count']} != {exp_count}"
        assert props["call_sites"] == exp_sites, (
            f"{key} call_sites={props['call_sites']} != {exp_sites}"
        )

    # 3) resolved / unresolved 统计
    # 已解析调用点：1+1+1+1+1+2+1 = 8；未解析：line14 (other) + line15 (unknown) = 2
    assert stats.call_resolved == 8, f"call_resolved={stats.call_resolved} != 8"
    assert stats.call_unresolved == 2, f"call_unresolved={stats.call_unresolved} != 2"

    # 4) 自调用（递归）确实保留为独立边
    assert (_sid(C, "Worker.step"), _sid(C, "Worker.run")) in got

    # 5) 未解析调用点不产生任何边（无幽灵 callee）
    all_callees = {d for (_, d) in got}
    assert _sid(C, "Worker.run::unknown") not in all_callees  # sanity

    # 6) call_resolved_rate 由 stats 汇总（8 / 10 = 0.8）
    summary = stats.as_dict()
    assert summary["call_resolved_rate"] == 0.8, summary["call_resolved_rate"]

    print("OK: callgraph.build_calls 全部断言通过")
    print(f"  edges={len(got)} resolved={stats.call_resolved} "
          f"unresolved={stats.call_unresolved} rate={summary['call_resolved_rate']}")


if __name__ == "__main__":
    main()
