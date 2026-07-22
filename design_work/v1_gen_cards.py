# -*- coding: utf-8 -*-
"""V1 步骤2：真实调用阿里网关为目标实体生成 ≤40 字中文功能描述卡片。

安全红线：从 claude-ui/config.json 读 anthropic_base_url + anthropic_auth_token，
token 只入内存与请求头，**绝不打印/落盘/写日志**。传输精确复刻 server.py：
POST {base}/v1/messages，头 Authorization: Bearer <token>（不打印）、
anthropic-version: 2023-06-01、content-type: application/json，非流式 urllib。

反幻觉：输出里的英文标识符必须是输入（qualname+doc）的子串（忽略大小写），
违规重试一次，仍违规则降级弃用该卡（discard）。限速每请求 sleep 0.5s，
网络/HTTP 失败重试 2 次；单卡失败不阻塞全局。

先做网关健康探针：探针失败 → 整体判 blocked，如实记录并中止（绝不伪造卡片）。

产物：design_work/v1_cards.json —— {meta, cards:[{id,label,name,input_summary,
card_raw,card_text,char_count,accepted,reason,attempts}], blocked:bool}
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = "C:/Users/nirvana/Desktop/claude-ui/config.json"
TARGETS = os.path.join(ROOT, "design_work", "v1_targets.json")
OUT = os.path.join(ROOT, "design_work", "v1_cards.json")

MODEL = "qwen3.8-max-preview"          # 任务指定
ANTHROPIC_VERSION = "2023-06-01"
SLEEP_BETWEEN = 0.5                     # 限速
NET_RETRIES = 2                         # 网络/HTTP 失败重试次数
MAX_CARD_CHARS = 40
TIMEOUT = 40

# 英文标识符 run（含点分/下划线/数字），len>=2 且非纯数字才校验
_EN_RUN = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")

SYSTEM = (
    "你是代码库实体的中文功能描述生成器。给你一个函数/类/概念的【名称】与【原始说明】，"
    "请输出一句不超过 40 字的中文功能描述，说清它「做什么、解决什么问题」，供中文语义检索使用。"
    "硬规则："
    "1) 只依据给定输入，严禁引入输入中不存在的技术名词或英文标识符（防幻觉）；"
    "2) 输入里已有的英文标识符可原样保留，但不得杜撰输入中没有的英文名或第三方技术名；"
    "3) 用自然、口语化但准确的中文，多用近义表达帮助检索；"
    "4) 只输出这一句描述本身，不要引号、不要前缀标签、不要解释、不要换行。"
)


def load_cfg():
    cfg = json.load(open(CONFIG, encoding="utf-8"))
    base = (cfg.get("anthropic_base_url") or "").rstrip("/")
    token = cfg.get("anthropic_auth_token") or ""
    return base, token


def headers(token):
    # token 仅进请求头，绝不打印
    return {
        "Authorization": "Bearer " + token,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }


def extract_text(raw):
    try:
        obj = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return ""
    if isinstance(obj, dict):
        content = obj.get("content")
        if isinstance(content, list):
            parts = [(b.get("text") or "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            return "".join(parts)
    return ""


def call_gateway(base, token, user, max_tokens=200):
    """单次网关调用（含 NET_RETRIES 次重试）。返回 (text|None, err|None)。"""
    payload = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": user}],
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_err = None
    for attempt in range(NET_RETRIES + 1):
        try:
            req = urllib.request.Request(
                base + "/v1/messages", data=data,
                headers=headers(token), method="POST")
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
            text = extract_text(raw)
            if text and text.strip():
                return text.strip(), None
            last_err = "empty_text"
        except urllib.error.HTTPError as e:
            # 只记状态码，绝不记 body/header（防泄漏）
            last_err = "HTTP_%s" % e.code
        except Exception as e:  # noqa: BLE001
            last_err = type(e).__name__
        if attempt < NET_RETRIES:
            time.sleep(1.0 + attempt)
    return None, last_err


def clean_card(text):
    """去引号/前缀/换行，压成一行。"""
    s = (text or "").strip()
    s = s.replace("\n", " ").replace("\r", " ")
    s = re.sub(r"^[\"'“”『「]+", "", s).strip()
    s = re.sub(r"[\"'“”』」]+$", "", s).strip()
    for pre in ("描述：", "描述:", "功能：", "功能:", "答：", "答:"):
        if s.startswith(pre):
            s = s[len(pre):].strip()
    return s


def whitelist_ok(card, input_text):
    """卡片里的英文标识符必须都是输入的子串（忽略大小写）。返回 (ok, offenders)。"""
    low_in = input_text.lower()
    offenders = []
    for run in _EN_RUN.findall(card):
        tok = run.strip("._")
        if len(tok) < 2 or tok.isdigit():
            continue
        if tok.lower() not in low_in:
            offenders.append(tok)
    return (len(offenders) == 0), offenders


def gen_one(base, token, t):
    """为单个目标实体生成卡片。返回结果 dict。"""
    qn = t["input_qualname"] or t["name"]
    doc = t["input_doc"] or ""
    input_text = (qn + " " + doc).strip()
    user = "【名称】%s\n【原始说明】%s" % (qn, doc or "（无说明）")

    attempts = []
    for tno in range(2):   # 首次 + 违规重试一次
        text, err = call_gateway(base, token, user)
        if err is not None:
            attempts.append({"try": tno + 1, "net_err": err})
            # 网络错误也算一次尝试；若首次网络失败，直接返回（call_gateway 内已重试）
            return {"card_raw": None, "card_text": None, "accepted": False,
                    "reason": "net_fail:%s" % err, "attempts": attempts,
                    "input_text": input_text}
        card = clean_card(text)
        ok, offenders = whitelist_ok(card, input_text)
        attempts.append({"try": tno + 1, "raw": text, "cleaned": card,
                         "whitelist_ok": ok, "offenders": offenders})
        if ok:
            used = card if len(card) <= MAX_CARD_CHARS else card[:MAX_CARD_CHARS]
            return {"card_raw": card, "card_text": used, "accepted": True,
                    "reason": "ok" if len(card) <= MAX_CARD_CHARS else "ok_truncated",
                    "attempts": attempts, "input_text": input_text,
                    "char_count": len(card)}
        time.sleep(SLEEP_BETWEEN)
    # 两次都违规 → 降级弃用
    return {"card_raw": attempts[-1].get("cleaned"), "card_text": None,
            "accepted": False, "reason": "whitelist_violation",
            "attempts": attempts, "input_text": input_text}


def probe(base, token):
    text, err = call_gateway(base, token, "请只回复两个字：正常", max_tokens=20)
    return (text is not None), (err or ""), (text or "")


def main():
    tgt = json.load(open(TARGETS, encoding="utf-8"))
    base, token = load_cfg()
    print("网关 base:", base, " model:", MODEL, " token: sk-****（不打印）")

    ok, err, sample = probe(base, token)
    if not ok:
        print("[BLOCKED] 网关健康探针失败：", err)
        out = {"meta": {"model": MODEL, "base": base, "blocked": True,
                        "probe_error": err},
               "blocked": True, "cards": []}
        json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("已写 blocked 记录:", os.path.relpath(OUT, ROOT))
        return 3
    print("探针 OK，网关可用（样本回复长度 %d）。开始生成 %d 张卡片..." % (
        len(sample), len(tgt["targets"])))

    cards = []
    n_ok = n_discard = n_netfail = 0
    for i, t in enumerate(tgt["targets"], 1):
        r = gen_one(base, token, t)
        rec = {
            "id": t["id"], "label": t["label"], "name": t["name"],
            "tier": t["tier"],
            "input_summary": {"qualname": t["input_qualname"],
                              "doc": t["input_doc"]},
            "card_text": r["card_text"], "card_raw": r["card_raw"],
            "accepted": r["accepted"], "reason": r["reason"],
            "char_count": r.get("char_count"),
            "attempts": r["attempts"],
        }
        cards.append(rec)
        if r["accepted"]:
            n_ok += 1
            tag = "OK "
        elif r["reason"].startswith("net_fail"):
            n_netfail += 1
            tag = "NET"
        else:
            n_discard += 1
            tag = "DIS"
        ct = r.get("char_count")
        print("  [%2d/%d] %s %-8s %-38s %s" % (
            i, len(tgt["targets"]), tag, t["label"], t["name"][:38],
            (r["card_text"] or ("<%s>" % r["reason"]))[:36]))
        time.sleep(SLEEP_BETWEEN)

    out = {
        "meta": {"model": MODEL, "base": base, "blocked": False,
                 "n_targets": len(tgt["targets"]),
                 "n_accepted": n_ok, "n_discarded": n_discard,
                 "n_netfail": n_netfail,
                 "whitelist_rule": "输出英文标识符须为输入子串(忽略大小写)，违规重试1次后弃用",
                 "max_card_chars": MAX_CARD_CHARS},
        "blocked": False,
        "cards": cards,
    }
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\n生成完成：接受 %d / 弃用(whitelist) %d / 网络失败 %d，共 %d" % (
        n_ok, n_discard, n_netfail, len(cards)))
    print("写出:", os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
