"""RepoGraph MCP stdio 服务器 —— 纯 stdlib JSON-RPC 2.0 薄适配器（D-14 / D-23 / D-N8）。

把 ``retrieve`` 层的三个确定性/检索函数以 **MCP 工具**形态暴露给 Claude Code 及任意
MCP 客户端，复归执行计划 §4 Phase E「MCP 工具形态」（D-23 由砍除改判推迟至此，D-14 的
``repo_overview`` 能力从「meta 路由注入」复归为「按需工具」）。**薄适配器**：不改检索层、
不引第三方依赖。

选型（D-N8，如实记录）：官方 ``mcp`` python-sdk（FastMCP）本机可用且在 Python 3.14 上
可导入，但本服务器**主动选纯 stdlib** JSON-RPC 2.0 实现——理由是守住项目「运行时零第三方
依赖」的叙事主线（检索/服务路径全 stdlib，见 D-22/D-N3），使 ``python -m repograph.mcp_server``
无需任何 ``pip install`` 即可被 Claude Code 拉起；MCP stdio 传输所需的协议面（3 方法）小而
稳定，自实现成本远低于引入 anyio/httpx/starlette/uvicorn 依赖树的代价。详见 DECISIONS D-N8。

协议：MCP stdio 传输 = **换行分隔的 JSON-RPC 2.0**（每行一条完整 JSON 消息，消息体内不含
裸换行——``json.dumps`` 默认把 ``\\n`` 转义为 ``\\n`` 字面量，天然单行）。实现最小方法集：

    initialize                 → 能力协商（回显客户端 protocolVersion）
    notifications/initialized  → 通知（无 id，无响应）
    tools/list                 → 三工具的名称/描述/inputSchema
    tools/call                 → 分派到三工具，结果走 content[text] + structuredContent
    ping                       → 空结果（MCP 保活）

三工具（archive §8.2「刻意克制三件」；第四件 ``query_graph``/text2cypher 无载体，推迟
v0.4，见 DECISIONS D-N7）：

    ask_repo(question)                             —— 包装 build_repo_context（lexical 档）
    impact_analysis(symbol, depth=2, mode="calls") —— 包装 retrieve.impact.impact_analysis
    repo_overview()                                —— output/repo_card.json（缺失降级重建）

**答案边界**：``ask_repo`` 只返回检索上下文与结构化字段，**答案由调用方模型依据
``context_text`` 生成**（push→pull 语义不变，工具不生成事实、不掺概率数据）。

图谱路径：默认相对仓库 ``output/graph.json``，``REPOGRAPH_GRAPH`` 环境变量覆盖；载入失败
不崩传输——``initialize``/``tools/list`` 仍可用，``tools/call`` 返回 ``isError`` + 可读错误
（含解析到的绝对路径与 env 提示），并在启动时向 stderr 打印一行诊断。

运行：``python -m repograph.mcp_server``（见仓库根 ``.mcp.json.example``）。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional

# MCP 协议版本：initialize 时回显客户端所请求者（最大兼容）；客户端未给时用此默认。
_DEFAULT_PROTOCOL_VERSION = "2025-06-18"
_SERVER_NAME = "repograph"
_SERVER_VERSION = "0.3.0"

# JSON-RPC 2.0 标准错误码
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602    # 标准码，保留作完整参照；本适配器工具入参错一律走 tools/call isError（非协议层码）
_INTERNAL_ERROR = -32603


# ---------------------------------------------------------------------------
# 图谱路径解析与 GraphStore 缓存（懒加载，单进程单线程，无并发）
# ---------------------------------------------------------------------------

def default_graph_path() -> str:
    """``output/graph.json`` 绝对路径（相对仓库根，cwd 无关）。

    ``__file__`` = ``<repo>/src/repograph/mcp_server.py``；两级 ``dirname`` 到 ``<repo>/src``、
    再一级到 ``<repo>``。
    """
    here = os.path.dirname(os.path.abspath(__file__))          # <repo>/src/repograph
    repo_root = os.path.dirname(os.path.dirname(here))         # <repo>
    return os.path.join(repo_root, "output", "graph.json")


def resolve_graph_path() -> str:
    """图谱路径：``REPOGRAPH_GRAPH`` 环境变量优先，否则默认 ``output/graph.json``。"""
    env = os.environ.get("REPOGRAPH_GRAPH")
    if env and env.strip():
        return os.path.abspath(os.path.expanduser(env.strip()))
    return default_graph_path()


class _StoreHolder:
    """懒加载 GraphStore 的缓存持有者。载入失败不抛到传输层，记录可读错误供工具回传。"""

    def __init__(self, path: str) -> None:
        self.path = path
        self._store: Optional[Any] = None
        self._error: Optional[str] = None
        self._loaded = False

    def get(self) -> tuple[Optional[Any], Optional[str]]:
        """返回 ``(store, error)``。首次调用触发载入，之后走缓存。"""
        if self._loaded:
            return self._store, self._error
        self._loaded = True
        try:
            from .models import GraphStore

            if not os.path.exists(self.path):
                self._error = (
                    f"找不到图谱文件: {self.path}\n"
                    f"      请先运行 `repograph index --repo <PATH> --name <NAME>` 生成，"
                    f"或用 REPOGRAPH_GRAPH 环境变量指向已有 graph.json。"
                )
                return None, self._error
            self._store = GraphStore.load(self.path)
        except Exception as exc:  # noqa: BLE001 — 任何载入失败（含 `from .models` 的 ImportError、
            # 结构错位致的 TypeError/AttributeError：graph.json 是合法 JSON 但顶层为 null/list 等）
            # 都归一为可读 graph_unavailable 的 isError 通路，绝不冒泡为协议层 -32603；且 _error
            # 必被赋值——避免 lazy import 抛错后缓存态 (None, None) 致工具回包 message=null
            # （opencode 审查 E1-R1 补审于 E-Verify：import 段一并纳入 try）。
            self._error = f"图谱文件载入失败 ({type(exc).__name__}): {self.path} — {exc}"
            return None, self._error
        return self._store, None


# ---------------------------------------------------------------------------
# 工具定义（名称 / 描述 / inputSchema）—— 描述写明适用场景，供调用方 Agent 自行路由
# ---------------------------------------------------------------------------

_TOOLS: list[dict] = [
    {
        "name": "ask_repo",
        "description": (
            "仓库检索问答的**上下文供给**（不生成答案）。对自然语言问题跑 build_repo_context"
            "（五路路由 meta/global/entity_local/structural/out_of_scope + 四档瀑布，lexical 档），"
            "返回检索上下文 context_text 与结构化字段（mode/route_label/linked/resolved_query/"
            "needs_disambiguation/candidates/premise_flags/degraded/suggestions/stats）。"
            "**答案由调用方模型依据 context_text 生成**——本工具只供图谱检索证据，不编造事实、"
            "永不裸拒。适合：设计溯源『X 是怎么设计/演化来的』、符号/模块定位、元问题"
            "『你了解这个项目吗』。中文/英文/混排均可。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "自然语言问题，如「你了解这个项目吗」「_handle_terminate 改了会影响谁」",
                },
            },
            "required": ["question"],
            "additionalProperties": False,
        },
    },
    {
        "name": "impact_analysis",
        "description": (
            "分析修改某符号的**影响面**（确定性模板查询，非 LLM 生成，不掺概率数据）。"
            "沿 CALLS 反向 BFS 求调用方闭包（mode=calls）或沿 IMPORTS 求模块级影响（mode=imports）。"
            "返回 {resolved_symbol, direct_callers[], transitive_callers[], affected_endpoints[], "
            "affected_modules[], truncated, depth, mode}。**符号歧义时返回 {error:'ambiguous', "
            "candidates[]}**（P3：确定性工具不吃模糊输入，须先消歧到唯一 ID 再调）；未命中返回 "
            "{error:'not_found'}。重构/改动某函数前先调它评估波及面。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "限定名或其唯一后缀，如 'ChaosHarness.run' / '_handle_terminate'",
                },
                "depth": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 4,
                    "default": 2,
                    "description": "反向 BFS 深度，白名单 1–4，默认 2",
                },
                "mode": {
                    "type": "string",
                    "enum": ["calls", "imports"],
                    "default": "calls",
                    "description": "calls=函数级调用链 | imports=模块级导入链",
                },
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "repo_overview",
        "description": (
            "返回仓库 level-0 概览卡片 repo_card（规模统计 stats、顶层模块 top_modules、热点函数 "
            "hot_functions、核心概念 core_concepts、入口点 entrypoints、summary；另附 source/degraded "
            "两个溯源标记）。零检索、直接读 output/repo_card.json；缺失/损坏则现场确定性重建"
            "（degraded=true，source 标注结果来源，字段与 build_overview 同源、为其超集）。"
            "适合『这个项目是做什么的 / 整体结构 / 有哪些模块』类元问题。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
]

_TOOL_NAMES = {t["name"] for t in _TOOLS}


# ---------------------------------------------------------------------------
# 三工具实现（薄包装，不改检索层）
# ---------------------------------------------------------------------------

# ask_repo 结构化返回的固定键集（schema v2，纯增字段以默认补齐）
_ASK_REPO_KEYS = (
    "mode", "route_label", "context_text", "linked", "resolved_query",
    "needs_disambiguation", "candidates", "premise_flags", "degraded",
    "suggestions", "stats",
)


def _tool_ask_repo(store: Any, args: dict) -> dict:
    """包装 build_repo_context（lexical 档）→ 归一到固定 11 键（缺省字段补默认）。"""
    question = args.get("question")
    if not isinstance(question, str) or not question.strip():
        raise _ToolArgError("参数 question 必须为非空字符串")
    from .retrieve.context import build_repo_context

    ctx = build_repo_context(store, question)
    out = {
        "mode": ctx.get("mode"),
        "route_label": ctx.get("route_label"),
        "context_text": ctx.get("context_text", ""),
        "linked": ctx.get("linked", []),
        "resolved_query": ctx.get("resolved_query", question),
        "needs_disambiguation": ctx.get("needs_disambiguation", False),
        "candidates": ctx.get("candidates", []),
        "premise_flags": ctx.get("premise_flags", []),
        "degraded": ctx.get("degraded", False),
        "suggestions": ctx.get("suggestions", []),
        "stats": ctx.get("stats", {}),
    }
    return out


def _tool_impact_analysis(store: Any, args: dict) -> dict:
    """包装 retrieve.impact.impact_analysis；歧义/未命中如实透传 error+candidates（P3）。"""
    symbol = args.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        raise _ToolArgError("参数 symbol 必须为非空字符串")
    depth = args.get("depth", 2)
    mode = args.get("mode", "calls")
    # 显式排除 bool（True/False 是 int 子类，会绕过白名单）
    if isinstance(depth, bool) or not isinstance(depth, int) or depth not in (1, 2, 3, 4):
        raise _ToolArgError(f"参数 depth 必须是 1..4 的整数（白名单），收到 {depth!r}")
    if mode not in ("calls", "imports"):
        raise _ToolArgError(f"参数 mode 必须是 'calls' | 'imports'，收到 {mode!r}")

    from .retrieve.impact import impact_analysis

    # impact_analysis 对合法入参不抛错；歧义/未命中以 {error,...} 返回，直接透传（P3 契约）。
    return impact_analysis(store, symbol.strip(), depth=depth, mode=mode)


def _tool_repo_overview(store: Any, args: dict) -> dict:
    """返回 output/repo_card.json（缓存优先）；缺失/损坏现场确定性重建 + degraded=true。"""
    from .retrieve.repo_card import load_or_build_repo_card

    card, degraded = load_or_build_repo_card(store)
    out = dict(card)
    out["degraded"] = degraded
    out["source"] = (
        "build_repo_card(cache-miss,现场确定性重建)" if degraded else "output/repo_card.json"
    )
    return out


class _ToolArgError(ValueError):
    """工具入参非法——转为 tools/call 的 isError 结果（非协议层 error）。"""


_TOOL_IMPL = {
    "ask_repo": _tool_ask_repo,
    "impact_analysis": _tool_impact_analysis,
    "repo_overview": _tool_repo_overview,
}


# ---------------------------------------------------------------------------
# JSON-RPC 收发（换行分隔，UTF-8，逐条 flush）
# ---------------------------------------------------------------------------

def _write_message(out, msg: dict) -> None:
    """单行 JSON + 换行写出并 flush（json.dumps 默认转义换行 → 天然单行）。"""
    out.write(json.dumps(msg, ensure_ascii=False) + "\n")
    out.flush()


def _result(out, req_id: Any, result: Any) -> None:
    _write_message(out, {"jsonrpc": "2.0", "id": req_id, "result": result})


def _error(out, req_id: Any, code: int, message: str,
           data: Optional[Any] = None) -> None:
    err: dict = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    _write_message(out, {"jsonrpc": "2.0", "id": req_id, "error": err})


def _tool_result_content(payload: dict, is_error: bool = False) -> dict:
    """tools/call 结果封装：content[text=JSON] + structuredContent（双通道，最大兼容）。"""
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload,
        "isError": is_error,
    }


# ---------------------------------------------------------------------------
# 方法分派
# ---------------------------------------------------------------------------

def _handle_initialize(params: dict) -> dict:
    proto = params.get("protocolVersion") or _DEFAULT_PROTOCOL_VERSION
    return {
        "protocolVersion": proto,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
        "instructions": (
            "RepoGraph 代码知识图谱检索工具（stdlib MCP 薄适配器）。三工具：ask_repo（检索上下文，"
            "答案由你依 context_text 生成）、impact_analysis（确定性影响面，重构前先调）、"
            "repo_overview（仓库概览卡片）。"
        ),
    }


def _handle_tools_call(holder: _StoreHolder, params: dict, out) -> tuple[bool, Any]:
    """返回 (handled, result_payload)。工具执行错误走 isError 结果，不抛协议 error。"""
    name = params.get("name")
    args = params.get("arguments") or {}
    if name not in _TOOL_NAMES:
        return True, _tool_result_content(
            {"error": "unknown_tool", "message": f"未知工具: {name!r}；可用: {sorted(_TOOL_NAMES)}"},
            is_error=True,
        )
    if not isinstance(args, dict):
        return True, _tool_result_content(
            {"error": "invalid_arguments", "message": "arguments 必须是对象"}, is_error=True)

    store, load_err = holder.get()
    if store is None:
        return True, _tool_result_content(
            {"error": "graph_unavailable", "message": load_err}, is_error=True)

    try:
        payload = _TOOL_IMPL[name](store, args)
        # 成功结果的 JSON 序列化（_tool_result_content 内 json.dumps）亦纳入 try：若工具 payload
        # 含不可序列化值，在此抛错并归一为 isError，绝不穿透 _dispatch 冒泡为协议层 -32603
        # （E-Verify 审查：与 _StoreHolder 的 graph_unavailable 归一同一契约）。
        return True, _tool_result_content(payload, is_error=False)
    except _ToolArgError as exc:
        return True, _tool_result_content(
            {"error": "invalid_argument", "message": str(exc)}, is_error=True)
    except Exception as exc:  # noqa: BLE001 — 工具内部异常/结果序列化异常兜底为 isError，绝不崩传输
        return True, _tool_result_content(
            {"error": "tool_error", "message": f"{type(exc).__name__}: {exc}"}, is_error=True)


def _dispatch(holder: _StoreHolder, msg: dict, out) -> None:
    """分派单条 JSON-RPC 消息。通知（无 id）不回响应。"""
    req_id = msg.get("id")
    is_notification = "id" not in msg
    method = msg.get("method")

    if not isinstance(method, str):
        if not is_notification:
            _error(out, req_id, _INVALID_REQUEST, "缺少或非法的 method 字段")
        return

    # 通知：initialized / cancelled 等一律吞掉不回（JSON-RPC 通知无响应）
    if is_notification:
        return

    if method == "initialize":
        _result(out, req_id, _handle_initialize(msg.get("params") or {}))
    elif method == "ping":
        _result(out, req_id, {})
    elif method == "tools/list":
        _result(out, req_id, {"tools": _TOOLS})
    elif method == "tools/call":
        _handled, payload = _handle_tools_call(holder, msg.get("params") or {}, out)
        _result(out, req_id, payload)
    else:
        _error(out, req_id, _METHOD_NOT_FOUND, f"未实现的方法: {method}")


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------

def _reconfigure_streams() -> tuple[Any, Any]:
    """把 stdin/stdout 切到 UTF-8（Windows 默认 GBK 会破坏中文与 JSON）。返回 (stdin, stdout)。"""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", newline="\n")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass
    return sys.stdin, sys.stdout


def serve(stdin=None, stdout=None) -> int:
    """MCP stdio 主循环：逐行读 JSON-RPC，分派，写响应。EOF（stdin 关闭）时正常退出。"""
    rin, rout = _reconfigure_streams()
    stdin = stdin or rin
    stdout = stdout or rout

    graph_path = resolve_graph_path()
    holder = _StoreHolder(graph_path)
    # 启动诊断（stderr，不污染 stdout 的 JSON-RPC 通道）
    exists = os.path.exists(graph_path)
    print(f"[repograph.mcp] 启动 · 图谱={graph_path} · 存在={exists}", file=sys.stderr, flush=True)
    if not exists:
        print("[repograph.mcp] 警告: 图谱文件缺失，工具调用将返回可读错误（设 REPOGRAPH_GRAPH 覆盖路径）",
              file=sys.stderr, flush=True)

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            _error(stdout, None, _PARSE_ERROR, f"JSON 解析失败: {exc}")
            continue
        if not isinstance(msg, dict):
            _error(stdout, None, _INVALID_REQUEST, "顶层消息必须是 JSON 对象")
            continue
        try:
            _dispatch(holder, msg, stdout)
        except Exception as exc:  # noqa: BLE001 — 分派兜底，单条异常不终止循环
            _error(stdout, msg.get("id"), _INTERNAL_ERROR, f"内部错误: {type(exc).__name__}: {exc}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    return serve()


if __name__ == "__main__":
    sys.exit(main())
