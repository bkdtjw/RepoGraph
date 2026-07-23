"""端到端真测：repograph.mcp_server（MCP stdio 薄适配器，D-14/D-23/D-N8）。

**真实 stdio JSON-RPC**（非只测 import）：每个用例都把 ``python -m repograph.mcp_server``
拉起为**子进程**，通过其 stdin/stdout 走真正的换行分隔 JSON-RPC 2.0 会话——
``initialize`` → ``notifications/initialized`` → ``tools/list`` → ``tools/call``，对真实
``output/graph.json``（multi-agent-orch，510 节点 / 1698 边）产出的**真实检索值**断言。

三工具各 ≥2 真实用例（对齐任务契约）：
    ask_repo       ：「你知道我的代码库吗」→ route_label=meta + 真实 stats(259/139)；
                     实体问句「改动 _handle_terminate 会影响哪些调用方」→ symbol 档、上下文含调用方；
    impact_analysis：_handle_terminate → 3 直接调用方（含 _dispatch_group 等）；
                     歧义符号 invoke → error=ambiguous + 6 candidates；__init__ → 9 candidates；
                     imports 档、not_found、非法 depth（isError）；
    repo_overview  ：真实计数 139/259/22/15/75、顶层模块/热点/概念非空、幂等两调一致。

另测协议健壮性：未知方法 → -32601、未知工具 → isError、ping、graph 缺失（REPOGRAPH_GRAPH
指向不存在文件）→ 工具返回可读 graph_unavailable 错误而非崩溃。

运行（两种皆绿）：
    python tests/test_mcp_server.py           # 独立 runner（仓库既有约定）
    python -m pytest tests/test_mcp_server.py  # 无 fixture 依赖，pytest 亦可收集通过
"""
import atexit
import json
import os
import queue
import subprocess
import sys
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_SRC = os.path.join(_REPO, "src")
_GRAPH = os.path.join(_REPO, "output", "graph.json")

_TOOL_NAMES = {"ask_repo", "impact_analysis", "repo_overview"}


# ---------------------------------------------------------------------------
# 真实 stdio JSON-RPC 客户端（子进程 + 换行分隔消息，UTF-8，读走后台线程防阻塞）
# ---------------------------------------------------------------------------

class MCPStdioClient:
    """把 mcp_server 拉起为子进程，做真实 stdio JSON-RPC 会话。"""

    def __init__(self, env_overrides=None):
        env = dict(os.environ)
        # 让子进程能 import repograph（未安装亦可跑）；强制 UTF-8（Windows 默认 GBK 破坏中文）
        prev = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = _SRC + (os.pathsep + prev if prev else "")
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        if env_overrides:
            env.update(env_overrides)

        self.proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "repograph.mcp_server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, cwd=_REPO,
        )
        self._id = 0
        self._q: "queue.Queue[str | None]" = queue.Queue()
        self.stderr_lines: list[str] = []
        self._t_out = threading.Thread(target=self._pump_stdout, daemon=True)
        self._t_err = threading.Thread(target=self._pump_stderr, daemon=True)
        self._t_out.start()
        self._t_err.start()
        atexit.register(self.close)

    def _pump_stdout(self) -> None:
        for raw in self.proc.stdout:                       # 二进制逐行（服务器每消息单行 + \n）
            self._q.put(raw.decode("utf-8").strip())
        self._q.put(None)                                  # EOF 哨兵

    def _pump_stderr(self) -> None:
        for raw in self.proc.stderr:
            self.stderr_lines.append(raw.decode("utf-8", "replace").rstrip())

    def _write(self, obj: dict) -> None:
        self.proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def request(self, method: str, params=None, timeout: float = 30.0) -> dict:
        """发一条带 id 的请求，读回其唯一响应（服务器顺序处理，下一行即本请求响应）。"""
        self._id += 1
        rid = self._id
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self._write(msg)
        line = self._q.get(timeout=timeout)
        if line is None:
            raise RuntimeError(
                "服务器意外关闭；stderr:\n" + "\n".join(self.stderr_lines))
        resp = json.loads(line)
        assert resp.get("id") == rid, f"响应 id 不匹配: {resp.get('id')} != {rid}"
        assert resp.get("jsonrpc") == "2.0", f"缺少 jsonrpc 2.0: {resp}"
        return resp

    def notify(self, method: str, params=None) -> None:
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._write(msg)

    def call_tool(self, name: str, arguments: dict, timeout: float = 30.0) -> dict:
        """tools/call 便捷封装：返回 result（含 content / structuredContent / isError）。"""
        resp = self.request("tools/call", {"name": name, "arguments": arguments}, timeout)
        assert "result" in resp, f"tools/call 返回协议错误: {resp.get('error')}"
        return resp["result"]

    def structured(self, name: str, arguments: dict) -> tuple[dict, bool]:
        """返回 (structuredContent, isError)，并校验 content[text] 与 structuredContent 一致。"""
        res = self.call_tool(name, arguments)
        sc = res.get("structuredContent")
        assert isinstance(sc, dict), f"缺少 structuredContent: {res}"
        # 双通道一致性：content[0].text 必须是 sc 的合法 JSON 序列化
        text = res["content"][0]["text"]
        assert json.loads(text) == sc, "content[text] 与 structuredContent 不一致"
        return sc, bool(res.get("isError"))

    def close(self) -> None:
        try:
            if self.proc.poll() is None:
                try:
                    self.proc.stdin.close()
                except Exception:
                    pass
                self.proc.terminate()
                self.proc.wait(timeout=5)
        except Exception:
            pass


# 模块级共享会话（懒起，atexit 收尾）——供 pytest 无 fixture 收集 + 独立 runner 复用
_SESSION: MCPStdioClient | None = None


def _client() -> MCPStdioClient:
    global _SESSION
    if _SESSION is None:
        c = MCPStdioClient()
        # 完成一次 initialize 握手（真实协议时序）
        init = c.request("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "repograph-test", "version": "0"},
        })
        assert "result" in init, f"initialize 失败: {init}"
        c.notify("notifications/initialized")
        _SESSION = c
    return _SESSION


# ---------------------------------------------------------------------------
# 1) initialize —— 能力协商
# ---------------------------------------------------------------------------

def test_initialize():
    c = MCPStdioClient()
    try:
        resp = c.request("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"},
        })
        r = resp["result"]
        assert r["protocolVersion"] == "2025-06-18", "应回显客户端 protocolVersion"
        assert r["serverInfo"]["name"] == "repograph"
        assert "tools" in r["capabilities"], "须声明 tools 能力"
        # 启动诊断落 stderr、不污染 stdout JSON-RPC 通道
        c.notify("notifications/initialized")
    finally:
        c.close()
    print("test_initialize OK")


# ---------------------------------------------------------------------------
# 2) tools/list —— 断言 3 工具与 schema
# ---------------------------------------------------------------------------

def test_tools_list():
    c = _client()
    resp = c.request("tools/list")
    tools = resp["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == _TOOL_NAMES, f"工具集应为 {_TOOL_NAMES}，实得 {names}"
    by = {t["name"]: t for t in tools}

    # 每个工具都有 object 型 inputSchema + 非空描述
    for t in tools:
        assert t["inputSchema"]["type"] == "object"
        assert isinstance(t["description"], str) and len(t["description"]) > 20

    ask = by["ask_repo"]["inputSchema"]
    assert ask["required"] == ["question"]
    assert "question" in ask["properties"]

    imp = by["impact_analysis"]["inputSchema"]
    assert imp["required"] == ["symbol"]
    assert imp["properties"]["depth"]["default"] == 2, "depth 默认应为 2"
    assert imp["properties"]["depth"]["maximum"] == 4
    assert imp["properties"]["mode"]["enum"] == ["calls", "imports"]

    ov = by["repo_overview"]["inputSchema"]
    assert ov["properties"] == {}, "repo_overview 无入参"
    print("test_tools_list OK")


# ---------------------------------------------------------------------------
# 3) ask_repo —— 用例①元问题(route_label=meta+真实 stats) / 用例②实体问句(symbol 档)
# ---------------------------------------------------------------------------

_ASK_KEYS = {"mode", "route_label", "context_text", "linked", "resolved_query",
             "needs_disambiguation", "candidates", "premise_flags", "degraded",
             "suggestions", "stats"}


def test_ask_repo_meta():
    c = _client()
    sc, is_err = c.structured("ask_repo", {"question": "你知道我的代码库吗"})
    assert not is_err
    assert set(sc.keys()) == _ASK_KEYS, f"schema v2 应含固定 11 键，实得 {set(sc.keys())}"
    assert sc["route_label"] == "meta", f"元问题应路由 meta，实得 {sc['route_label']}"
    assert sc["mode"] == "overview"
    # 真实 stats（对真实图谱）
    assert sc["stats"]["functions"] == 259, "真实函数计数"
    assert sc["stats"]["concepts"] == 139, "真实概念计数"
    assert sc["context_text"], "meta 上下文非空（绝不裸拒）"
    assert sc["needs_disambiguation"] is False
    print("test_ask_repo_meta OK")


def test_ask_repo_entity():
    c = _client()
    sc, is_err = c.structured(
        "ask_repo", {"question": "改动 _handle_terminate 会影响哪些调用方"})
    assert not is_err
    assert sc["route_label"] == "entity_local"
    assert sc["mode"] == "symbol"
    # 上下文由真实 impact 闭包拼装：应含目标符号与其真实调用方
    assert "_handle_terminate" in sc["context_text"]
    assert "_dispatch_group" in sc["context_text"], "上下文应含真实调用方 _dispatch_group"
    assert any(r["entity_id"].endswith("_handle_terminate") for r in sc["linked"])
    print("test_ask_repo_entity OK")


# ---------------------------------------------------------------------------
# 4) impact_analysis —— _handle_terminate 调用方 / invoke·__init__ 歧义 / imports·not_found·非法 depth
# ---------------------------------------------------------------------------

def test_impact_handle_terminate():
    c = _client()
    sc, is_err = c.structured(
        "impact_analysis", {"symbol": "_handle_terminate", "depth": 2})
    assert not is_err
    assert sc["resolved_symbol"].endswith("::_handle_terminate")
    assert sc["mode"] == "calls" and sc["depth"] == 2
    direct_short = {cid.rsplit("::", 1)[-1] for cid in sc["direct_callers"]}
    assert {"_dispatch_group", "_dispatch_group_async",
            "_finish_interrupted_terminate"} <= direct_short, \
        f"直接调用方应含三真实调用方，实得 {direct_short}"
    assert len(sc["transitive_callers"]) == 2, "真实间接调用方 2 个（depth=2）"
    # depth=2 处闭包被截断（尚有 depth≥3 的上游调用方），truncated 如实为 True
    assert sc["truncated"] is True, "depth=2 处 _handle_terminate 闭包应被截断（有更上游调用方）"
    print("test_impact_handle_terminate OK")


def test_impact_ambiguous_invoke():
    c = _client()
    sc, is_err = c.structured("impact_analysis", {"symbol": "invoke"})
    # 歧义是 P3 设计内的**有效响应**（附 candidates 交调用方消歧），非工具失败 → isError=False
    assert not is_err, "歧义应作有效响应返回，不是 isError"
    assert sc.get("error") == "ambiguous"
    assert len(sc["candidates"]) == 6, f"invoke 应有 6 候选，实得 {len(sc['candidates'])}"
    assert all(cid.endswith(".invoke") for cid in sc["candidates"])
    print("test_impact_ambiguous_invoke OK")


def test_impact_ambiguous_init():
    c = _client()
    sc, is_err = c.structured("impact_analysis", {"symbol": "__init__"})
    assert not is_err
    assert sc.get("error") == "ambiguous"
    assert len(sc["candidates"]) == 9, f"__init__ 应有 9 候选，实得 {len(sc['candidates'])}"
    print("test_impact_ambiguous_init OK")


def test_impact_imports_mode():
    c = _client()
    sc, is_err = c.structured(
        "impact_analysis", {"symbol": "_handle_terminate", "mode": "imports", "depth": 2})
    assert not is_err
    assert sc["mode"] == "imports"
    assert len(sc["affected_modules"]) >= 1, "imports 档应给出受影响模块"
    print("test_impact_imports_mode OK")


def test_impact_not_found():
    c = _client()
    sc, is_err = c.structured(
        "impact_analysis", {"symbol": "zzz_nonexistent_symbol_xyz"})
    assert not is_err
    assert sc.get("error") == "not_found"
    print("test_impact_not_found OK")


def test_impact_invalid_depth():
    c = _client()
    sc, is_err = c.structured(
        "impact_analysis", {"symbol": "_handle_terminate", "depth": 9})
    assert is_err, "非白名单 depth 应 isError"
    assert sc.get("error") == "invalid_argument"
    print("test_impact_invalid_depth OK")


# ---------------------------------------------------------------------------
# 5) repo_overview —— 真实计数 139/259 + 顶层模块/热点/概念非空 + 幂等
# ---------------------------------------------------------------------------

def test_repo_overview():
    c = _client()
    sc, is_err = c.structured("repo_overview", {})
    assert not is_err
    st = sc["stats"]
    assert st["functions"] == 259 and st["concepts"] == 139, "真实计数 259/139"
    assert st["modules"] == 22 and st["classes"] == 15 and st["commits"] == 75
    assert sc["repo"] == "multi-agent-orch"
    assert len(sc["top_modules"]) >= 1 and len(sc["hot_functions"]) >= 1
    assert len(sc["core_concepts"]) >= 1
    assert sc["degraded"] is False
    assert sc["source"] == "output/repo_card.json"

    # 幂等：二次调用返回同一真实计数（用例②）
    sc2, _ = c.structured("repo_overview", {})
    assert sc2["stats"] == st, "repo_overview 幂等，两调计数一致"
    print("test_repo_overview OK")


# ---------------------------------------------------------------------------
# 6) 协议健壮性 —— ping / 未知方法 / 未知工具
# ---------------------------------------------------------------------------

def test_ping():
    c = _client()
    resp = c.request("ping")
    assert resp["result"] == {}, "ping 返回空结果"
    # 边界（E-Verify 审查补覆盖）：id=null 是**请求**（"id" 键存在，非通知），须回 id:null 的响应，
    # 不得被 "id" not in msg 的通知判定误吞——正是协议层 id 存在性边界。
    c._write({"jsonrpc": "2.0", "id": None, "method": "ping"})
    r2 = json.loads(c._q.get(timeout=30))
    assert r2.get("id") is None and r2.get("result") == {}, "id:null 请求应回 id:null 空结果"
    print("test_ping OK")


def test_unknown_method():
    c = _client()
    resp = c.request("no/such/method")
    assert "error" in resp and resp["error"]["code"] == -32601, "未知方法应 -32601"
    print("test_unknown_method OK")


def test_unknown_tool():
    c = _client()
    res = c.call_tool("does_not_exist", {})
    assert res["isError"] is True
    assert res["structuredContent"]["error"] == "unknown_tool"
    # 边界（E-Verify 审查补覆盖）：畸形 tools/call 一律归一为 isError（不崩传输、不冒泡协议层 -32603）
    #   ① arguments 非 dict     ② 必填 question 缺失     ③ symbol 非字符串
    r2 = c.request("tools/call", {"name": "ask_repo", "arguments": "bad"})["result"]
    assert r2["isError"] is True and r2["structuredContent"]["error"] == "invalid_arguments"
    r3 = c.call_tool("ask_repo", {})
    assert r3["isError"] is True and r3["structuredContent"]["error"] == "invalid_argument"
    r4 = c.call_tool("impact_analysis", {"symbol": 123})
    assert r4["isError"] is True and r4["structuredContent"]["error"] == "invalid_argument"
    print("test_unknown_tool OK")


# ---------------------------------------------------------------------------
# 7) 图谱路径覆盖 —— REPOGRAPH_GRAPH 指向不存在文件 → 可读 graph_unavailable，不崩传输
# ---------------------------------------------------------------------------

def test_graph_override_missing():
    bad = os.path.join(_REPO, "output", "__no_such_graph__.json")
    c = MCPStdioClient(env_overrides={"REPOGRAPH_GRAPH": bad})
    try:
        init = c.request("initialize", {"protocolVersion": "2025-06-18",
                                        "capabilities": {}, "clientInfo": {"name": "t"}})
        assert "result" in init, "缺图谱时 initialize 仍应成功（不崩传输）"
        c.notify("notifications/initialized")
        # tools/list 仍可用（静态 schema）
        assert len(c.request("tools/list")["result"]["tools"]) == 3
        # tools/call 返回可读错误
        res = c.call_tool("ask_repo", {"question": "你了解这个项目吗"})
        assert res["isError"] is True
        msg = res["structuredContent"]
        assert msg["error"] == "graph_unavailable"
        assert "__no_such_graph__.json" in msg["message"], "错误须含解析到的路径"
        assert "REPOGRAPH_GRAPH" in msg["message"], "错误须提示 env 覆盖"
        # 启动诊断落 stderr
        joined = "\n".join(c.stderr_lines)
        assert "repograph.mcp" in joined
    finally:
        c.close()
    print("test_graph_override_missing OK")


def test_graph_override_malformed():
    """图谱是合法 JSON 但结构错位（顶层 null → GraphStore.load 抛 TypeError）时，
    tools/call 须归一为可读 graph_unavailable 的 isError，**绝不**冒泡为协议层 -32603
    （opencode 审查 E1-R1 回归）。"""
    import tempfile
    fd, bad = tempfile.mkstemp(prefix="rg_bad_graph_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("null")                       # 合法 JSON，但 data["nodes"] → TypeError
        c = MCPStdioClient(env_overrides={"REPOGRAPH_GRAPH": bad})
        try:
            init = c.request("initialize", {"protocolVersion": "2025-06-18",
                                            "capabilities": {}, "clientInfo": {"name": "t"}})
            assert "result" in init
            c.notify("notifications/initialized")
            res = c.call_tool("repo_overview", {})
            assert res["isError"] is True, "结构错位应作可读 isError，而非协议 error"
            assert res["structuredContent"]["error"] == "graph_unavailable"
            assert "TypeError" in res["structuredContent"]["message"], "错误须点明真实异常类型"
        finally:
            c.close()
    finally:
        os.unlink(bad)
    print("test_graph_override_malformed OK")


# ---------------------------------------------------------------------------
# 独立 runner（仓库既有约定：python tests/test_mcp_server.py）
# ---------------------------------------------------------------------------

_ALL = [
    test_initialize,
    test_tools_list,
    test_ask_repo_meta,
    test_ask_repo_entity,
    test_impact_handle_terminate,
    test_impact_ambiguous_invoke,
    test_impact_ambiguous_init,
    test_impact_imports_mode,
    test_impact_not_found,
    test_impact_invalid_depth,
    test_repo_overview,
    test_ping,
    test_unknown_method,
    test_unknown_tool,
    test_graph_override_missing,
    test_graph_override_malformed,
]


if __name__ == "__main__":
    assert os.path.exists(_GRAPH), f"缺少真实图谱 {_GRAPH}"
    for fn in _ALL:
        fn()
    if _SESSION is not None:
        _SESSION.close()
    print(f"\nALL {len(_ALL)} MCP TESTS PASSED")
