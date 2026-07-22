"""索引期语义通道——阿里网关 HTTP 直连客户端（v0.3 · Phase C · 裁定 D-N4）。

**通道变更背景**：2026-07-22 grok CLI 订阅到期断供（402 Payment Required，实测确认）。
本模块以标准库 ``urllib`` 直连与 Anthropic Messages 兼容的阿里网关（POST ``{base}/v1/messages``，
非流式），承接原 ``grok_client.ask_grok`` 的索引期结构化生成职责（repo_card summary 及
后续中文卡片/概念对齐等），与 ``claude-ui/server.py`` 的 ``_rg_llm_select_concepts`` **同款传输**。

安全铁律（与运行时网关一致）：
- 鉴权令牌 ``anthropic_auth_token`` **只读入内存放请求头**，绝不打印 / 写文件 / 记日志；
  异常信息一律不含 token（提及一律 sk-****）。
- 配置读 ``claude-ui/config.json``（``anthropic_base_url`` + ``anthropic_auth_token`` +
  ``model_*``），找不到配置文件或缺关键字段时**抛明确异常**，绝不静默伪造输出（真实数据铁律）。

原 grok ``--json-schema`` 的结构化强制改由 **prompt 内嵌 schema + 调用方程序校验**承担
（``semantic.py`` 既有候选白名单 + quote-substring 校验体系不变——本就不信任模型输出，换模型不降防线）。

只依赖标准库（json / os / time / urllib）。``grok_client.py`` 保留为可选后端（已断供），不删。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

ANTHROPIC_VERSION = "2023-06-01"

# 索引期默认模型（落地设计 §4.7 / D-N4）：卡片 / summary 用 qwen3.8-max-preview。
INDEX_MODEL = "qwen3.8-max-preview"


class GatewayConfigError(RuntimeError):
    """网关配置不可用（找不到 config.json 或缺 base_url / auth_token）。"""


class GatewayCallError(RuntimeError):
    """网关调用失败（HTTP 错误 / 超时 / 响应无文本）。消息不含 token。"""


# ---------------------------------------------------------------------------
# 配置定位与读取（claude-ui/config.json；绝不回显 token）
# ---------------------------------------------------------------------------

def _candidate_config_paths(explicit: str | None) -> list[str]:
    """按优先级枚举 config.json 候选路径（显式 > 环境变量 > 同级 claude-ui > 绝对默认）。"""
    cands: list[str] = []
    if explicit:
        cands.append(explicit)
    env = os.environ.get("REPOGRAPH_GATEWAY_CONFIG")
    if env:
        cands.append(env)
    # 仓库根 = .../代码库知识图谱；其父目录（Desktop）下的同级 claude-ui/config.json
    here = os.path.dirname(os.path.abspath(__file__))            # src/repograph/extract
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    parent = os.path.dirname(repo_root)
    cands.append(os.path.join(parent, "claude-ui", "config.json"))
    # 绝对默认（本机 as-built 位置）
    cands.append(r"C:\Users\nirvana\Desktop\claude-ui\config.json")
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for c in cands:
        cn = os.path.normpath(c)
        if cn not in seen:
            seen.add(cn)
            out.append(cn)
    return out


def load_gateway_config(config_path: str | None = None) -> dict:
    """读取网关配置 dict。找不到文件抛 ``GatewayConfigError``（含所有已查路径，不含 token）。"""
    tried: list[str] = []
    for p in _candidate_config_paths(config_path):
        tried.append(p)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    raise GatewayConfigError(
        "找不到网关配置 config.json；已尝试：" + " | ".join(tried)
    )


def _resolve_endpoint(cfg: dict) -> tuple[str, str]:
    """从配置取 (base, token)；缺失则抛 ``GatewayConfigError``。token 绝不出现在异常消息里。"""
    base = (cfg.get("anthropic_base_url") or "").rstrip("/")
    token = cfg.get("anthropic_auth_token") or ""
    if not base:
        raise GatewayConfigError("config.json 缺 anthropic_base_url")
    if not token:
        raise GatewayConfigError("config.json 缺 anthropic_auth_token（sk-****）")
    return base, token


def _extract_text(raw: bytes) -> str:
    """从非流式 messages 响应体提取拼接文本块（与 server._extract_text 同义）。"""
    try:
        obj = json.loads(raw.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return ""
    if isinstance(obj, dict):
        content = obj.get("content")
        if isinstance(content, list):
            parts = [(b.get("text") or "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            if parts:
                return "".join(parts)
    return ""


# ---------------------------------------------------------------------------
# ask_gateway —— 单轮非流式生成
# ---------------------------------------------------------------------------

def ask_gateway(system: str, user: str, model: str | None = None,
                max_tokens: int = 512, timeout: int = 30,
                config_path: str | None = None,
                retries: int = 2, sleep_s: float = 0.5) -> str:
    """向网关发一次**非流式** messages 请求，返回首个文本块（拼接）。

    - ``model`` 缺省用 ``INDEX_MODEL``（qwen3.8-max-preview）；
    - 鉴权令牌只入 ``Authorization`` 头，**绝不回显**；异常消息不含 token；
    - 限速（``sleep_s``）+ 重试（``retries`` 次）应对瞬时失败；
    - 全部重试失败 → 抛 ``GatewayCallError``（调用方按需降级，**不伪造输出**）。

    配置缺失（无文件 / 无 base / 无 token）→ 抛 ``GatewayConfigError``。
    """
    cfg = load_gateway_config(config_path)
    base, token = _resolve_endpoint(cfg)
    mdl = model or INDEX_MODEL
    url = base + "/v1/messages"
    payload = {
        "model": mdl,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": "Bearer " + token,        # 仅入请求头，绝不打印/记录
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }

    last_err = "unknown"
    attempts = max(1, retries + 1)
    for i in range(attempts):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            text = _extract_text(raw)
            if text.strip():
                return text
            last_err = "响应无文本块"
        except urllib.error.HTTPError as e:      # 不读 e 的鉴权相关字段，不外泄
            last_err = f"HTTP {e.code}"
        except Exception as e:                   # noqa: BLE001  超时 / 网络 / 解析
            last_err = type(e).__name__
        if i < attempts - 1:
            time.sleep(sleep_s)
    raise GatewayCallError(f"网关调用失败（{mdl}）：{last_err}")
