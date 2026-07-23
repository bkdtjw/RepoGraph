"""E2 验收动作实测探针（archive §8.3 三动作的**本机等价真实调用**，D-23 复归 · Phase E）。

把 ``python -m repograph.mcp_server`` 拉起为子进程，走真实换行分隔 JSON-RPC 2.0 stdio，
对归档 §8.3 定义的三个 Claude Code 验收动作做**等价真实调用**并打印输入/输出摘要：

    动作 A（重构前先调 impact_analysis）：归档设想「重构 ToolRunner.run」——本仓无 ToolRunner，
        取真实热点函数 ``_handle_terminate``（被 6 次提交改、3 直接调用方）作重构目标；
        另附 ``ChaosHarness.run``（本仓唯一 ``.run`` 方法，ToolRunner.run 的字面同形）作对照。
    动作 B（设计溯源问 ask_repo）：问「终止清单这套设计是怎么演化来的」，观察返回的检索上下文
        （概念 + DESCRIBES 提交 = 演化史）。
    动作 C（关 MCP 对比）：同一符号 ``_handle_terminate`` 下，对比「MCP 关（grep 词面）」与
        「MCP 开（impact_analysis 确定性闭包）」能回答的问题差异。

产物摘要供 ``design_work/e2_acceptance.md`` 引用。纯 stdlib、真实子进程、不碰网关。

运行：python design_work/e2_acceptance_probe.py
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_SRC = os.path.join(_REPO, "src")


class Client:
    """极简真实 stdio JSON-RPC 客户端（子进程 + 后台读线程）。"""

    def __init__(self) -> None:
        env = dict(os.environ)
        prev = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = _SRC + (os.pathsep + prev if prev else "")
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        self.p = subprocess.Popen(
            [sys.executable, "-u", "-m", "repograph.mcp_server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, cwd=_REPO,
        )
        self._id = 0
        self._q: "queue.Queue[str | None]" = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        for raw in self.p.stdout:
            self._q.put(raw.decode("utf-8").strip())
        self._q.put(None)

    def _send(self, obj: dict) -> None:
        self.p.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        self.p.stdin.flush()

    def req(self, method: str, params=None) -> dict:
        self._id += 1
        rid = self._id
        m = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            m["params"] = params
        self._send(m)
        line = self._q.get(timeout=30)
        if line is None:
            raise RuntimeError("server closed")
        return json.loads(line)

    def note(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def call(self, name: str, arguments: dict) -> tuple[dict, bool]:
        r = self.req("tools/call", {"name": name, "arguments": arguments})["result"]
        return r["structuredContent"], bool(r.get("isError"))

    def close(self) -> None:
        try:
            self.p.stdin.close()
            self.p.terminate()
            self.p.wait(timeout=5)
        except Exception:
            pass


def _short(node_id: str) -> str:
    return node_id.rsplit("::", 1)[-1]


def main() -> int:
    c = Client()
    init = c.req("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "e2-probe", "version": "0"}})
    c.note("notifications/initialized")
    tools = [t["name"] for t in c.req("tools/list")["result"]["tools"]]
    print("=" * 78)
    print("initialize server=%s proto=%s" % (
        init["result"]["serverInfo"], init["result"]["protocolVersion"]))
    print("tools/list =", tools)

    # ---- 动作 A：重构前先调 impact_analysis ----
    print("\n" + "=" * 78)
    print("动作 A — 重构前先调 impact_analysis（等价『重构 ToolRunner.run』）")
    for sym, depth in [("_handle_terminate", 2), ("ChaosHarness.run", 2)]:
        sc, err = c.call("impact_analysis", {"symbol": sym, "depth": depth, "mode": "calls"})
        print("\n  输入: impact_analysis(symbol=%r, depth=%d, mode='calls')  isError=%s" % (sym, depth, err))
        print("  resolved_symbol =", sc.get("resolved_symbol", sc.get("error")))
        print("  direct_callers  =", [_short(x) for x in sc.get("direct_callers", [])])
        print("  transitive      =", [_short(x) for x in sc.get("transitive_callers", [])])
        print("  affected_modules=", [_short(x) for x in sc.get("affected_modules", [])])
        print("  truncated       =", sc.get("truncated"))

    # ---- 动作 B：设计溯源问 ask_repo ----
    print("\n" + "=" * 78)
    print("动作 B — 设计溯源问 ask_repo")
    qb = "终止清单这套设计是怎么演化来的"
    sc, err = c.call("ask_repo", {"question": qb})
    print("  输入: ask_repo(question=%r)  isError=%s" % (qb, err))
    print("  route_label=%s  mode=%s  degraded=%s" % (sc["route_label"], sc["mode"], sc["degraded"]))
    print("  linked(%d)=%s" % (len(sc["linked"]), [x.get("name") for x in sc["linked"][:5]]))
    commits = re.findall(r"·\s*([0-9a-f]{8})\s", sc["context_text"])
    concepts = re.findall(r"\[Concept\]\s*([^\n（(]+)", sc["context_text"])
    print("  context_text 长度=%d  含提交(DESCRIBES)=%s  样例sha=%s" % (
        len(sc["context_text"]), bool(commits), commits[:4]))
    print("  上下文命中概念样例=", [x.strip() for x in concepts[:5]])
    print("  premise_flags=%s  suggestions=%s" % (sc["premise_flags"], sc["suggestions"][:2]))

    # ---- 动作 C：关 MCP 对比（grep 词面 vs impact 闭包）----
    print("\n" + "=" * 78)
    print("动作 C — 关 MCP 对比（同符号 _handle_terminate）")
    # MCP 关：编码 Agent 只能 grep 词面
    grep = subprocess.run(
        ["git", "grep", "-n", "_handle_terminate", "--", "*.py"],
        cwd=_find_indexed_repo(), capture_output=True, text=True, encoding="utf-8", errors="replace",
    ) if _find_indexed_repo() else None
    if grep and grep.returncode == 0:
        lines = [l for l in grep.stdout.splitlines() if l.strip()]
        print("  [MCP 关] git grep '_handle_terminate' → %d 处词面匹配（只知『在哪』，不知调用闭包/受影响模块/是否截断）" % len(lines))
        for l in lines[:3]:
            print("     ", l[:100])
    else:
        print("  [MCP 关] （被索引仓库不在本机；示意：grep 只回词面匹配，无结构化调用闭包）")
    sc, _ = c.call("impact_analysis", {"symbol": "_handle_terminate", "depth": 2})
    print("  [MCP 开] impact_analysis → 确定性回答『改它会波及谁』：")
    print("     direct=%s trans=%s modules=%s truncated=%s" % (
        [_short(x) for x in sc["direct_callers"]],
        [_short(x) for x in sc["transitive_callers"]],
        [_short(x) for x in sc["affected_modules"]], sc["truncated"]))

    c.close()
    print("\n" + "=" * 78)
    print("E2 三动作实测完成（真实子进程 stdio JSON-RPC）")
    return 0


def _find_indexed_repo() -> str | None:
    """尝试定位被索引仓库 multi-agent-orch（动作 C 的 MCP-off grep 基线）；找不到返回 None。"""
    candidates = [
        os.path.join(os.path.dirname(_REPO), "multi-agent-orch"),
        os.path.join(_REPO, "..", "multi-agent-orch"),
    ]
    for p in candidates:
        if os.path.isdir(os.path.join(p, ".git")):
            return os.path.abspath(p)
    return None


if __name__ == "__main__":
    sys.exit(main())
