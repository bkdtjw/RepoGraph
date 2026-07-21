"""cli.py 自测：只覆盖参数解析与 --help，不做端到端（兄弟模块可能尚未就绪）。

运行：cd <项目根> && python tests/test_cli.py
"""
import contextlib
import io
import os
import sys

sys.path.insert(0, "src")

from repograph import cli  # noqa: E402


def _parse(argv):
    """用一个全新 parser 解析，返回 Namespace（不触发任何子命令处理函数）。"""
    return cli.build_parser().parse_args(argv)


def _expect_parse_exit(argv, code):
    """parse_args 应抛 SystemExit(code)；吞掉 argparse 的 help/error 输出。"""
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cli.build_parser().parse_args(argv)
    except SystemExit as exc:
        assert exc.code == code, f"argv={argv} 期望退出码 {code}，实得 {exc.code}"
        return
    raise AssertionError(f"argv={argv} 期望 SystemExit({code})，但未抛出")


def _expect_main_exit(argv, code):
    """cli.main 透传 argparse 的 SystemExit（--help / 参数错误）。"""
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cli.main(argv)
    except SystemExit as exc:
        assert exc.code == code, f"main({argv}) 期望退出码 {code}，实得 {exc.code}"
        return
    raise AssertionError(f"main({argv}) 期望 SystemExit({code})，但未抛出")


def test_defaults_bound_to_config():
    # 默认路径必须与 config.settings.output_dir 联动
    assert cli.DEFAULT_OUT == "output", cli.DEFAULT_OUT
    assert cli.DEFAULT_GRAPH == "output/graph.json", cli.DEFAULT_GRAPH


def test_parser_builds():
    p = cli.build_parser()
    import argparse
    assert isinstance(p, argparse.ArgumentParser)


def test_all_six_subcommands_registered():
    expected = {"index", "semantic", "viz", "impact", "stats", "all"}
    p = cli.build_parser()
    # 从 subparsers action 里取已注册的子命令名
    choices = set()
    for action in p._actions:
        if isinstance(action, __import__("argparse")._SubParsersAction):
            choices = set(action.choices.keys())
    assert choices == expected, choices


def test_index_parse():
    ns = _parse(["index", "--repo", "/tmp/r", "--name", "demo"])
    assert ns.command == "index"
    assert ns.repo == "/tmp/r"
    assert ns.name == "demo"
    assert ns.out == "output"              # 默认
    assert ns.func is cli.cmd_index


def test_index_out_override():
    ns = _parse(["index", "--repo", "/tmp/r", "--name", "demo", "--out", "custom_dir"])
    assert ns.out == "custom_dir"


def test_index_missing_required():
    _expect_parse_exit(["index", "--repo", "/tmp/r"], 2)   # 缺 --name
    _expect_parse_exit(["index", "--name", "demo"], 2)     # 缺 --repo


def test_semantic_parse():
    ns = _parse(["semantic", "--repo", "/tmp/r", "--name", "demo"])
    assert ns.command == "semantic"
    assert ns.graph == "output/graph.json"  # 默认
    assert ns.func is cli.cmd_semantic
    ns2 = _parse(["semantic", "--repo", "/tmp/r", "--name", "demo", "--graph", "g.json"])
    assert ns2.graph == "g.json"


def test_semantic_missing_required():
    _expect_parse_exit(["semantic", "--repo", "/tmp/r"], 2)  # 缺 --name


def test_viz_parse():
    ns = _parse(["viz"])
    assert ns.command == "viz"
    assert ns.graph == "output/graph.json"
    assert ns.out == "output"
    assert ns.func is cli.cmd_viz


def test_impact_parse_defaults():
    ns = _parse(["impact", "--symbol", "ToolRunner.run"])
    assert ns.command == "impact"
    assert ns.symbol == "ToolRunner.run"
    assert ns.depth == 3                    # 默认
    assert isinstance(ns.depth, int)        # type=int 生效
    assert ns.mode == "calls"               # 默认
    assert ns.func is cli.cmd_impact


def test_impact_parse_overrides():
    ns = _parse(["impact", "--symbol", "X", "--depth", "2", "--mode", "imports"])
    assert ns.depth == 2
    assert ns.mode == "imports"


def test_impact_bad_mode():
    _expect_parse_exit(["impact", "--symbol", "X", "--mode", "bogus"], 2)  # choices 限制


def test_impact_missing_symbol():
    _expect_parse_exit(["impact"], 2)


def test_stats_parse():
    ns = _parse(["stats"])
    assert ns.command == "stats"
    assert ns.graph == "output/graph.json"
    assert ns.func is cli.cmd_stats


def test_all_parse():
    ns = _parse(["all", "--repo", "/tmp/r", "--name", "demo"])
    assert ns.command == "all"
    assert ns.no_semantic is False          # 默认不跳过
    assert ns.func is cli.cmd_all
    ns2 = _parse(["all", "--repo", "/tmp/r", "--name", "demo", "--no-semantic"])
    assert ns2.no_semantic is True


def test_all_missing_required():
    _expect_parse_exit(["all", "--repo", "/tmp/r"], 2)


def test_help_exits_zero():
    _expect_main_exit(["--help"], 0)
    _expect_parse_exit(["-h"], 0)


def test_subcommand_help_exits_zero():
    for cmd in ("index", "semantic", "viz", "impact", "stats", "all"):
        _expect_main_exit([cmd, "--help"], 0)


def test_no_subcommand_returns_2():
    # 无子命令：打印帮助并返回 2（不抛异常）
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        rc = cli.main([])
    assert rc == 2, rc


def test_unknown_subcommand():
    _expect_parse_exit(["frobnicate"], 2)


def test_load_store_missing_file_exits_1():
    # _load_store 对缺失文件应以码 1 退出（可操作的错误提示）
    missing = os.path.join(os.path.dirname(__file__), "__does_not_exist__.json")
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            cli._load_store(missing)
    except SystemExit as exc:
        assert exc.code == 1, exc.code
    else:
        raise AssertionError("缺失文件应触发 SystemExit(1)")


def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        passed += 1
        print(f"  ok  {t.__name__}")
    print(f"\n全部 {passed} 项自测通过。")


if __name__ == "__main__":
    _run_all()
