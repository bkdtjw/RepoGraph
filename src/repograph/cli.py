"""RepoGraph 命令行入口（argparse，不依赖 typer）。对应技术设计文档 §10.2。

子命令：
    index      结构层 + git 层确定性抽取 → 落盘 graph.json / stats.json
    semantic   LLM 语义层：就地合并 Concept 节点与 DESCRIBES/IMPLEMENTS 边
    viz        渲染图谱可视化产出
    impact     影响面分析（确定性模板查询，分层打印）
    stats      打印图谱规模与质量指标
    all        一键全流程：index → semantic（可跳过）→ viz

所有跨模块调用严格遵循 models.py 顶部【API 约定】的函数签名。

工程约定：兄弟模块（extract/*, viz.render, retrieve.impact, build, stats）
在多 agent 并行开发中可能尚未就绪，因此本文件对它们一律**惰性导入**
（在各子命令处理函数内部 import），保证 `--help` 与参数解析在任何时刻可用。
"""
from __future__ import annotations

import argparse
import os
import sys

from repograph.config import settings

# 默认路径与 config.py 的 output_dir 联动（§10.1）
DEFAULT_OUT = settings.output_dir
DEFAULT_GRAPH = f"{settings.output_dir}/graph.json"


# ---------------------------------------------------------------------------
# 控制台编码：Windows 默认 GBK 会让中文/箭头字符输出报 UnicodeEncodeError
# ---------------------------------------------------------------------------


def _reconfigure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass


# ---------------------------------------------------------------------------
# 打印辅助
# ---------------------------------------------------------------------------


def _print_counts(counts: dict) -> None:
    """打印 GraphStore.counts() 的规模摘要。"""
    print("  ── 图谱规模 ──")
    print(f"    节点总数: {counts.get('total_nodes', 0)}")
    for label, n in sorted(counts.get("nodes", {}).items()):
        print(f"      {label:<10} {n}")
    print(f"    边总数:   {counts.get('total_edges', 0)}")
    for etype, n in sorted(counts.get("edges", {}).items()):
        print(f"      {etype:<10} {n}")


def _print_tree(obj, indent: int = 0) -> None:
    """通用分层缩进打印，用于 impact / semantic 摘要 / stats.json 等任意 dict/list。"""
    pad = "    " + "  " * indent
    if isinstance(obj, dict):
        for key, val in obj.items():
            if isinstance(val, dict):
                print(f"{pad}{key}:")
                _print_tree(val, indent + 1)
            elif isinstance(val, list):
                print(f"{pad}{key}: ({len(val)} 项)")
                _print_tree(val, indent + 1)
            else:
                print(f"{pad}{key}: {val}")
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                print(f"{pad}-")
                _print_tree(item, indent + 1)
            else:
                print(f"{pad}- {item}")
    else:
        print(f"{pad}{obj}")


def _load_store(path: str):
    """载入本地图谱；文件缺失时给出可操作的错误并以码 1 退出。"""
    from repograph.models import GraphStore

    if not os.path.exists(path):
        print(
            f"错误: 找不到图谱文件 {path}\n"
            f"      请先运行 `repograph index --repo <PATH> --name <NAME>` 生成。",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return GraphStore.load(path)


# ---------------------------------------------------------------------------
# 各阶段核心流程（供 index / all 复用）
# ---------------------------------------------------------------------------


def _run_index(repo: str, name: str, out: str):
    """extract_worktree → mark_endpoints → build_calls → extract_git
    → build_graph → save(graph.json) → write_stats(stats.json)。"""
    from repograph.models import PipelineStats
    from repograph.extract import ast_extractor, callgraph, endpoints, git_extractor
    from repograph import build
    from repograph import stats as stats_mod

    pstats = PipelineStats()  # 贯穿全流程，由各抽取模块就地写入计数
    print(f"[index] 仓库={repo}  名称={name}  输出目录={out}")

    print("  [1/5] AST 结构抽取 ...", flush=True)
    modules, parse_skips = ast_extractor.extract_worktree(repo, name, settings)
    pstats.parse_skips_worktree = parse_skips
    print(f"        模块 {len(modules)} 个 · 语法错误跳过 {parse_skips} 个")

    print("  [2/5] 端点识别 ...", flush=True)
    n_endpoints = endpoints.mark_endpoints(modules, settings)
    print(f"        端点函数 {n_endpoints} 个")

    print("  [3/5] 调用图解析（两遍扫描，仅落已解析边）...", flush=True)
    calls = callgraph.build_calls(modules, pstats)
    print(f"        CALLS 候选 {len(calls)} 条 · resolved={pstats.call_resolved} "
          f"unresolved={pstats.call_unresolved}")

    print("  [4/5] Git 历史抽取（diff→函数映射 + issue）...", flush=True)
    gitdata = git_extractor.extract_git(repo, name, modules, pstats)
    print(f"        提交 {len(gitdata.get('commits', []))} · issue {len(gitdata.get('issues', []))} "
          f"· MODIFIES {len(gitdata.get('modifies', []))} · FIXES {len(gitdata.get('fixes', []))}")

    print("  [5/5] 组装属性图 ...", flush=True)
    store = build.build_graph(modules, calls, gitdata, name)

    graph_path = f"{out}/graph.json"
    store.save(graph_path)
    print(f"        图谱已保存 → {graph_path}")

    stats_path = f"{out}/stats.json"
    stats_mod.write_stats(store, pstats, stats_path)
    print(f"        指标已保存 → {stats_path}")

    _print_counts(store.counts())
    return store, pstats, graph_path


def _run_semantic(graph_path: str, repo: str, name: str):
    """载入图谱 → run_semantic 就地合并概念层 → 回写原文件 → 打印摘要。"""
    from repograph.models import PipelineStats
    from repograph.extract import semantic

    store = _load_store(graph_path)
    pstats = PipelineStats()
    print(f"[semantic] 图谱={graph_path}  仓库={repo}  名称={name}")
    print("  运行 LLM 语义抽取（commit message / docstring / README / issue）...", flush=True)
    summary = semantic.run_semantic(store, repo, name, pstats, settings)
    store.save(graph_path)
    print(f"  已就地合并 Concept 节点与 DESCRIBES/IMPLEMENTS 边，回写 → {graph_path}")
    # 语义层单独运行时 stats.json 由 index 阶段写就，其结构指标不可被 fresh pstats
    # 覆盖为零。故只就地补丁语义相关字段并刷新 Concept 后的节点/边计数，保留结构指标。
    _patch_semantic_stats(store, pstats, graph_path)
    print("  ── 语义抽取摘要 ──")
    _print_tree(summary)
    return summary


def _patch_semantic_stats(store, pstats, graph_path: str) -> None:
    """把语义计数与最新图谱规模补丁进同目录 stats.json（保留 index 阶段的结构指标）。"""
    import json

    stats_path = os.path.join(os.path.dirname(graph_path) or ".", "stats.json")
    try:
        if os.path.exists(stats_path):
            with open(stats_path, encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {}
        counts = store.counts()
        data.update({
            "nodes": counts["nodes"],
            "edges": counts["edges"],
            "total_nodes": counts["total_nodes"],
            "total_edges": counts["total_edges"],
            "semantic_extracted": pstats.semantic_extracted,
            "extraction_reject_by_reason": dict(pstats.semantic_rejected),
        })
        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        print(f"  指标已更新（语义计数 + 图谱规模）→ {stats_path}")
    except (OSError, ValueError, KeyError) as exc:  # 补丁失败不应中断
        print(f"  （stats.json 语义补丁跳过：{type(exc).__name__}: {exc}）", file=sys.stderr)


def _run_viz(graph_path: str, out: str):
    """载入图谱 → render_all → 打印产出文件列表。"""
    from repograph.viz import render

    store = _load_store(graph_path)
    print(f"[viz] 图谱={graph_path}  输出目录={out}")
    outputs = render.render_all(store, out)
    print(f"  生成 {len(outputs)} 个可视化产出:")
    for path in outputs:
        print(f"    - {path}")
    return outputs


# ---------------------------------------------------------------------------
# 子命令处理函数
# ---------------------------------------------------------------------------


def cmd_index(args: argparse.Namespace) -> int:
    _run_index(args.repo, args.name, args.out)
    return 0


def cmd_semantic(args: argparse.Namespace) -> int:
    _run_semantic(args.graph, args.repo, args.name)
    return 0


def cmd_viz(args: argparse.Namespace) -> int:
    _run_viz(args.graph, args.out)
    return 0


def cmd_impact(args: argparse.Namespace) -> int:
    from repograph.retrieve import impact

    store = _load_store(args.graph)
    print(f"[impact] symbol={args.symbol}  depth={args.depth}  mode={args.mode}")
    result = impact.impact_analysis(store, args.symbol, args.depth, args.mode)
    print("  ── 影响面分析 ──")
    _print_tree(result)
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    import json

    store = _load_store(args.graph)
    print(f"[stats] 图谱={args.graph}")
    _print_counts(store.counts())

    stats_path = os.path.join(os.path.dirname(args.graph) or ".", "stats.json")
    if os.path.exists(stats_path):
        with open(stats_path, encoding="utf-8") as f:
            data = json.load(f)
        print(f"  ── 质量指标（{stats_path}）──")
        _print_tree(data)
    else:
        print(f"  （未找到 {stats_path}，运行 `repograph index ...` 生成质量指标）")
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    _, _, graph_path = _run_index(args.repo, args.name, args.out)
    if args.no_semantic:
        print("[all] 跳过语义层（--no-semantic）")
    else:
        # 语义层依赖本地 grok CLI，环境未就绪时不应阻断结构层产出，故降级为告警
        try:
            _run_semantic(graph_path, args.repo, args.name)
        except Exception as exc:  # noqa: BLE001 - 语义层可选，失败不影响后续
            print(f"[all] 语义层失败，已跳过（{type(exc).__name__}: {exc}）", file=sys.stderr)
    _run_viz(graph_path, args.out)
    print("[all] 全流程完成。")
    return 0


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """构造并返回顶层 ArgumentParser（纯参数定义，不触发任何兄弟模块导入）。"""
    parser = argparse.ArgumentParser(
        prog="repograph",
        description="RepoGraph — 代码知识图谱构建与检索（v0.1，本地 GraphStore 后端）",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_index = sub.add_parser("index", help="结构层 + git 层抽取并落盘 graph.json / stats.json")
    p_index.add_argument("--repo", required=True, help="目标仓库根目录")
    p_index.add_argument("--name", required=True, help="仓库名（用作 ID 前缀）")
    p_index.add_argument("--out", default=DEFAULT_OUT, help=f"输出目录（默认 {DEFAULT_OUT}）")
    p_index.set_defaults(func=cmd_index)

    p_sem = sub.add_parser("semantic", help="LLM 语义层：合并 Concept 与 DESCRIBES/IMPLEMENTS 边")
    p_sem.add_argument("--repo", required=True, help="目标仓库根目录")
    p_sem.add_argument("--name", required=True, help="仓库名（用作 ID 前缀）")
    p_sem.add_argument("--graph", default=DEFAULT_GRAPH, help=f"图谱文件（默认 {DEFAULT_GRAPH}）")
    p_sem.set_defaults(func=cmd_semantic)

    p_viz = sub.add_parser("viz", help="渲染图谱可视化产出")
    p_viz.add_argument("--graph", default=DEFAULT_GRAPH, help=f"图谱文件（默认 {DEFAULT_GRAPH}）")
    p_viz.add_argument("--out", default=DEFAULT_OUT, help=f"输出目录（默认 {DEFAULT_OUT}）")
    p_viz.set_defaults(func=cmd_viz)

    p_imp = sub.add_parser("impact", help="影响面分析（确定性模板查询）")
    p_imp.add_argument("--symbol", required=True,
                       help="限定名或其唯一后缀，如 ToolRunner.run")
    p_imp.add_argument("--graph", default=DEFAULT_GRAPH, help=f"图谱文件（默认 {DEFAULT_GRAPH}）")
    p_imp.add_argument("--depth", type=int, default=3, help="遍历深度（默认 3，建议 1-4）")
    p_imp.add_argument("--mode", choices=("calls", "imports"), default="calls",
                       help="calls=函数级调用链 | imports=模块级导入链（默认 calls）")
    p_imp.set_defaults(func=cmd_impact)

    p_stats = sub.add_parser("stats", help="打印图谱规模与已存质量指标")
    p_stats.add_argument("--graph", default=DEFAULT_GRAPH, help=f"图谱文件（默认 {DEFAULT_GRAPH}）")
    p_stats.set_defaults(func=cmd_stats)

    p_all = sub.add_parser("all", help="一键全流程：index → semantic → viz")
    p_all.add_argument("--repo", required=True, help="目标仓库根目录")
    p_all.add_argument("--name", required=True, help="仓库名（用作 ID 前缀）")
    p_all.add_argument("--out", default=DEFAULT_OUT, help=f"输出目录（默认 {DEFAULT_OUT}）")
    p_all.add_argument("--no-semantic", action="store_true", help="跳过语义层")
    p_all.set_defaults(func=cmd_all)

    return parser


def main(argv: list[str] | None = None) -> int:
    _reconfigure_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 2
    return func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
