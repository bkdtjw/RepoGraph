"""本地 grok CLI 的 headless 封装（文档 §5，llm_backend='grok-cli'）。

对外只暴露 :func:`ask_grok` 与 :class:`GrokError`。调用形如::

    ask_grok("从下面文本抽取概念...", json_schema=CONCEPT_SCHEMA)

--- 实测输出格式（据此写解析器）---------------------------------------------
以命令
    grok -p 'return {"x":1} as json' \
         --json-schema '{"type":"object","properties":{"x":{"type":"number"}},
                         "required":["x"]}' --max-turns 1
实测 stdout（grok 0.2.x，Windows x86_64）为一个 **信封 JSON**：

    {
      "text": "{\\"x\\":1}",
      "stopReason": "EndTurn",
      "sessionId": "019f8419-...",
      "requestId": "8b14f4e1-...",
      "thought": "The user wants me to return ...",
      "structuredOutput": { "x": 1 }
    }

即：`--json-schema` 隐含 `--output-format json`，真正符合 schema 的对象在
`structuredOutput` 字段里，`text` 字段是同一对象的 JSON 字符串副本。
因此本解析器优先取 `structuredOutput`，退而取 `text`，再退而取其它常见包裹字段
（result/content/output/data 等），最后才把整段 stdout 当成裸 JSON。

无 `--json-schema` 时 `--output-format` 为默认 plain，stdout 即模型回复正文，
本函数直接 strip 后原样返回。
"""
from __future__ import annotations

import json
import subprocess
from typing import Any, Optional

__all__ = ["ask_grok", "GrokError"]


class GrokError(RuntimeError):
    """grok 调用失败或输出无法解析为 JSON 时抛出（携带 stdout 前 500 字符）。"""


# grok 信封字段中「内层才是目标 schema」的常见包裹键，按优先级排列。
_WRAPPER_KEYS = (
    "structuredOutput", "structured_output",
    "result", "content", "output", "data", "response", "json",
)


def ask_grok(
    prompt: str,
    json_schema: Optional[dict] = None,
    timeout: int = 300,
    exe: Optional[str] = None,
) -> "str | dict":
    """单轮调用本地 grok CLI。

    参数
    ----
    prompt      : 提示词（作为 ``-p`` 位置内容，禁 shell 拼接）。
    json_schema : 非空时追加 ``--json-schema <dumps>``，CLI 强制输出符合该 schema
                  的 JSON；本函数解析后返回 ``dict``。为空时返回 stdout 文本。
    timeout     : 秒；超时 raise :class:`subprocess.TimeoutExpired`（调用方自行处理）。
    exe         : grok 可执行文件路径；缺省取 ``config.settings.grok_exe``。

    返回
    ----
    ``json_schema`` 为空  -> ``str``（stdout.strip()）
    ``json_schema`` 非空  -> ``dict``（解析出的目标对象）

    异常
    ----
    :class:`GrokError` : 进程失败且无可用输出，或有 schema 时输出无法解析为目标 JSON。
    """
    if exe is None:
        from repograph.config import settings as _settings
        exe = _settings.grok_exe

    # 列表 argv，绝不 shell=True。
    argv: list[str] = [exe, "-p", prompt, "--max-turns", "1"]
    if json_schema:
        argv += ["--json-schema", json.dumps(json_schema, ensure_ascii=False)]

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as exc:  # exe 路径不存在
        raise GrokError(f"grok executable not found: {exe}") from exc

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    if not json_schema:
        if proc.returncode != 0 and not stdout:
            raise GrokError(f"grok exited {proc.returncode}: {stderr[:500]}")
        return stdout

    parsed = _parse_structured(stdout, json_schema)
    if parsed is None:
        detail = stdout or stderr
        raise GrokError(detail[:500] if detail else f"grok exited {proc.returncode}: empty output")
    return parsed


# ---------------------------------------------------------------------------
# 解析器
# ---------------------------------------------------------------------------


def _parse_structured(stdout: str, schema: dict) -> Optional[dict]:
    """把 grok stdout 解析为目标 schema 对象；失败返回 None。"""
    if not stdout:
        return None

    # 1) 整体当 JSON。
    obj = _try_load(stdout)

    # 2) 提取最外层 {...}。
    if obj is None:
        snippet = _extract_outermost_object(stdout)
        if snippet is not None:
            obj = _try_load(snippet)

    # 3) 逐行找第一段可解析的 JSON 对象（CLI 可能带包裹/日志行）。
    if obj is None:
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                cand = _try_load(line)
                if isinstance(cand, dict):
                    obj = cand
                    break

    if not isinstance(obj, dict):
        return None

    return _unwrap(obj, schema)


def _unwrap(obj: dict, schema: dict) -> dict:
    """从信封对象中取出真正符合 schema 的内层对象。"""
    prop_keys = set((schema or {}).get("properties", {}).keys())
    obj_keys = set(obj.keys())

    # A) obj 本身就是目标：含 schema 顶层属性，且不含任何包裹字段。
    if prop_keys and (prop_keys & obj_keys) and not (obj_keys & set(_WRAPPER_KEYS)):
        return obj

    # B) 逐个尝试已知包裹字段（structuredOutput 优先，见模块 docstring 实测）。
    for key in _WRAPPER_KEYS:
        if key not in obj:
            continue
        inner: Any = obj[key]
        if isinstance(inner, str):
            inner = _try_load(inner.strip())
        # 合法的结构化对象允许为空 {}（如 schema 目标本就无必填内容时），
        # 不能因 falsy 而漏掉；仅当解析失败（inner 非 dict）时才试下一个包裹键。
        if isinstance(inner, dict):
            return inner

    # C) text 字段是目标对象的 JSON 字符串副本（结构化字段缺失时的兜底）。
    text = obj.get("text")
    if isinstance(text, str):
        inner = _try_load(text.strip())
        if isinstance(inner, dict):
            return inner

    # D) 实在无法拆包，把 obj 原样返回（可能已经是目标，或缺 schema 提示）。
    return obj


def _try_load(s: str) -> Optional[Any]:
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _extract_outermost_object(s: str) -> Optional[str]:
    """返回第一个 '{' 到其配对 '}' 的子串（忽略字符串字面量内的花括号）。"""
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return s[start:i + 1]
    return None
