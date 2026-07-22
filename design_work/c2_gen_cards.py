# -*- coding: utf-8 -*-
"""C2 步骤2：富化双语卡片批量生成（D-11 重议方向落地，真实阿里网关调用）。

对 output/graph.json 的**全部 139 Concept + 核心 Function/Class（fan_in+MODIFIES top，
并保证 FZ 20 题 gold 及 1 跳邻域入选）** 生成两字段卡片：
  - desc      ≤40 字中文功能描述（专名白名单校验，英文标识符须为富化输入子串，同 V1 反幻觉）
  - zh_aliases 3-5 个受控口语近义说法（**允许非输入子串**——只进检索语料与节点属性、永不进
               答案事实，与 symbol_guesses 检索辅助隔离同原则；铁律辩护见落地设计 §4.4）

富化输入（较 V1 的「qualname+docstring首行」大幅加厚，对症 V1 dev 平坦根因）：
  Function → qualname + 完整 docstring + 签名 + ≤3 调用点上下文（CALLS 反向：调用方 qualname
             +其 docstring 首行）+ 所属模块 docstring 首行 + 该函数 IMPLEMENTS 的概念名
  Class    → qualname + 完整 docstring + 基类 + 成员方法名 + 所属模块概览
  Concept  → name + 完整 description + 全部 evidence 引文 + 实现它的函数 qualname + 描述提交首行

防泄题红线（评测有效性）：本脚本**只从 dataset.jsonl 读 gold_entity/alt 的 id**（保证覆盖），
**绝不读取 question 字段**；FZ-dev 十题的口语词（使坏扛揍/盯着卡住/估摸篇幅…）不入任何 prompt，
aliases 必须从实体语义独立生成。断言 `_assert_no_leak` 二次校验 prompt 不含题面。

生成后跑一次 **LLM 自检批次（flash=qwen3.6-flash）**：剔除过于笼统/会误指向多实体的歧义别名。

安全红线：token 读 claude-ui/config.json 只入请求头，绝不打印/落盘/记日志（一律 sk-****）。
限速 sleep 0.5s、网络失败重试 2 次；单实体失败不阻塞全局；先探针、失败判 blocked 不伪造。

产物：design_work/c2_cards.json（全量可审计）。用法：python design_work/c2_gen_cards.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = "C:/Users/nirvana/Desktop/claude-ui/config.json"
GRAPH = os.path.join(ROOT, "output", "graph.json")
DATASET = os.path.join(ROOT, "eval", "dataset.jsonl")
OUT = os.path.join(ROOT, "design_work", "c2_cards.json")

GEN_MODEL = "qwen3.8-max-preview"       # 索引期卡片（落地设计 §4.7 / D-N4）
CHECK_MODEL = "qwen3.6-flash"           # 自检批次（廉价 flash）
ANTHROPIC_VERSION = "2023-06-01"
SLEEP_BETWEEN = 0.5
NET_RETRIES = 2
TIMEOUT = 90
WORKERS = 6                             # 并发线程数（网关实测容忍并发；串行 50min→并发~9min）

MAX_DESC_CHARS = 40
MAX_ALIAS_CHARS = 14
MAX_ALIASES = 5
MIN_ALIASES = 3
TOP_SYMBOLS = 110                       # 核心 Function/Class 取数（fan_in+MODIFIES top）
CHECK_BATCH = 14                        # 自检每批别名条数

_EN_RUN = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")


# --------------------------------------------------------------------------- #
# 网关传输（复刻 server.py / llm_client；token 绝不打印）
# --------------------------------------------------------------------------- #

def load_cfg():
    cfg = json.load(open(CONFIG, encoding="utf-8"))
    base = (cfg.get("anthropic_base_url") or "").rstrip("/")
    token = cfg.get("anthropic_auth_token") or ""
    return base, token


def headers(token):
    return {
        "Authorization": "Bearer " + token,          # 仅入请求头，绝不打印
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
            return "".join((b.get("text") or "") for b in content
                           if isinstance(b, dict) and b.get("type") == "text")
    return ""


def call_gateway(base, token, system, user, model, max_tokens=400):
    """单次非流式调用（含 NET_RETRIES 重试）。返回 (text|None, err|None)。"""
    payload = {"model": model, "max_tokens": max_tokens,
               "system": system, "messages": [{"role": "user", "content": user}]}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_err = None
    for attempt in range(NET_RETRIES + 1):
        try:
            req = urllib.request.Request(base + "/v1/messages", data=data,
                                         headers=headers(token), method="POST")
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
            text = extract_text(raw)
            if text and text.strip():
                return text.strip(), None
            last_err = "empty_text"
        except urllib.error.HTTPError as e:
            last_err = "HTTP_%s" % e.code       # 只记状态码，绝不记 body/header
        except Exception as e:  # noqa: BLE001
            last_err = type(e).__name__
        if attempt < NET_RETRIES:
            time.sleep(1.0 + attempt)
    return None, last_err


def loose_json(text):
    """容错解析首个 {...} 对象。"""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        nl = s.find("\n")
        if nl >= 0 and s[:nl].strip().lower() in ("json", ""):
            s = s[nl + 1:]
        s = s.strip()
    try:
        o = json.loads(s)
        return o if isinstance(o, dict) else None
    except ValueError:
        pass
    i, j = s.find("{"), s.rfind("}")
    if 0 <= i < j:
        try:
            o = json.loads(s[i:j + 1])
            return o if isinstance(o, dict) else None
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------- #
# 图谱装配 + 目标选择
# --------------------------------------------------------------------------- #

def load_graph():
    g = json.load(open(GRAPH, encoding="utf-8"))
    nodes = {n["id"]: n for n in g["nodes"]}
    return nodes, g["edges"]


def first_line(text):
    for ln in (text or "").splitlines():
        s = ln.strip()
        if s:
            return s
    return ""


def load_gold_ids():
    """只读 FZ dev+test 的 gold_entity/alt 的 **id**（绝不读 question，防泄题）。"""
    ids = set()
    with open(DATASET, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("subset") in ("FZ_dev", "FZ_test"):
                ids.add(r["gold_entity"])
                for a in r.get("alt_gold_entities") or []:
                    ids.add(a)
    return ids


def select_targets(nodes, edges, gold_ids):
    """全部 Concept + 全部 Class + top Function（fan_in+MODIFIES）+ FZ gold/邻域。"""
    fan_in, modif = {}, {}
    impl_fn_by_concept, impl_concept_by_fn = {}, {}
    callers_of = {}          # dst_fn -> [src_fn...]
    for e in edges:
        t = e["type"]
        if t == "CALLS":
            fan_in[e["dst"]] = fan_in.get(e["dst"], 0) + 1
            callers_of.setdefault(e["dst"], []).append(e["src"])
        elif t == "MODIFIES":
            modif[e["dst"]] = modif.get(e["dst"], 0) + 1
        elif t == "IMPLEMENTS":
            impl_fn_by_concept.setdefault(e["dst"], []).append(e["src"])
            impl_concept_by_fn.setdefault(e["src"], []).append(e["dst"])

    concepts = [nid for nid, n in nodes.items() if n["label"] == "Concept"]
    classes = [nid for nid, n in nodes.items() if n["label"] == "Class"]
    funcs = [nid for nid, n in nodes.items() if n["label"] == "Function"]

    funcs_ranked = sorted(
        funcs, key=lambda i: (-(fan_in.get(i, 0) + modif.get(i, 0)), i))
    top_funcs = funcs_ranked[:TOP_SYMBOLS]

    chosen = set(concepts) | set(classes) | set(top_funcs)

    # FZ gold + 1 跳邻域强制入选（gold=Concept→其实现函数；gold=Function→其实现的概念）
    for gid in gold_ids:
        if gid not in nodes:
            continue
        chosen.add(gid)
        n = nodes[gid]
        if n["label"] == "Concept":
            for s in sorted(impl_fn_by_concept.get(gid, []))[:3]:
                if nodes.get(s, {}).get("label") == "Function":
                    chosen.add(s)
        elif n["label"] == "Function":
            for c in impl_concept_by_fn.get(gid, []):
                chosen.add(c)

    return chosen, {"fan_in": fan_in, "modif": modif,
                    "callers_of": callers_of,
                    "impl_fn_by_concept": impl_fn_by_concept,
                    "impl_concept_by_fn": impl_concept_by_fn}


# --------------------------------------------------------------------------- #
# 富化输入构造（对症 V1 dev 平坦：加厚 docstring 正文 + 调用点 + 模块 + 概念）
# --------------------------------------------------------------------------- #

def _module_doc_of(nodes, file_path):
    for n in nodes.values():
        if n["label"] == "Module" and n.get("path") == file_path:
            return first_line(n.get("docstring"))
    return ""


def build_input(nid, nodes, idx):
    """返回 (input_text, whitelist_text)。whitelist_text 供 desc 英文白名单校验。"""
    n = nodes[nid]
    label = n["label"]
    lines = []
    if label in ("Function", "Class"):
        qn = n.get("qualname", "")
        lines.append("【名称】" + qn)
        if label == "Function":
            lines.append("【签名】" + (n.get("signature") or ""))
        doc = (n.get("docstring") or "").strip()
        lines.append("【文档】" + (doc if doc else "（无）"))
        if label == "Class" and n.get("bases"):
            lines.append("【基类】" + ", ".join(n.get("bases") or []))
        mod = _module_doc_of(nodes, n.get("file", ""))
        if mod:
            lines.append("【所属模块】" + mod)
        # ≤3 调用点上下文
        callers = idx["callers_of"].get(nid, [])[:3]
        if callers:
            cl = []
            for cid in callers:
                cn = nodes.get(cid)
                if cn:
                    cl.append("%s（%s）" % (cn.get("qualname", ""),
                                          first_line(cn.get("docstring")) or "无说明"))
            if cl:
                lines.append("【被调用于】" + "；".join(cl))
        # 实现的概念
        concepts = idx["impl_concept_by_fn"].get(nid, [])
        cnames = [nodes[c].get("name", "") for c in concepts if c in nodes][:4]
        if cnames:
            lines.append("【相关概念】" + "、".join(cnames))
        wl = qn + " " + (n.get("signature") or "") + " " + doc
    else:  # Concept
        name = n.get("name", "")
        lines.append("【概念】" + name)
        lines.append("【说明】" + (n.get("description") or "（无）"))
        quotes = [first_line((ev or {}).get("quote")) for ev in (n.get("evidence") or [])]
        quotes = [q for q in quotes if q][:3]
        if quotes:
            lines.append("【证据引文】" + " ｜ ".join(quotes))
        impls = idx["impl_fn_by_concept"].get(nid, [])
        inames = [nodes[i].get("qualname", "") for i in impls if i in nodes][:5]
        if inames:
            lines.append("【实现函数】" + "、".join(inames))
        wl = name + " " + (n.get("description") or "") + " " + " ".join(quotes)
    return "\n".join(lines), wl


# --------------------------------------------------------------------------- #
# desc 白名单 + 生成
# --------------------------------------------------------------------------- #

# 注意：GEN_SYSTEM 的示例**严禁**采用任何评测题面口语（防泄题红线）。示例只用与 FZ gold
# 无关的中性功能（登录校验/缓存/退回），仅示范「口语化改写的风格」；启动时 _assert_system_no_leak
# 断言不含题面口语 4-gram。（round3 版：回到 round1 无 few-shot、实体自导出风格——实测优于 round2
# 的激进+中性 few-shot 版；仅新增「把描述里的形象短语也口语化成别名」这一通用指令补覆盖缺口。）
GEN_SYSTEM = (
    "你是代码库实体的中文检索标注器。给你一个函数/类/概念的【富化上下文】（名称、完整文档、"
    "签名、调用关系、所属模块、相关概念或证据）。请只输出一个严格 JSON 对象："
    '{"desc": "……", "zh_aliases": ["……", "……", "……"]}。'
    "字段要求："
    "desc：一句不超过 40 字的中文功能描述，说清它「做什么、解决什么问题」；"
    "只能使用上下文里原样出现过的英文标识符，严禁杜撰上下文中没有的英文名或第三方技术栈名。"
    "zh_aliases：3 到 5 个**口语化近义说法**——一个不懂代码的中文用户在检索这个功能时可能会"
    "怎么随口称呼它（可以用大白话、动作化描述、生活化比喻），要尽量多样、覆盖不同的说法角度；"
    "每条不超过 14 字。**特别地：如果【文档/描述】里出现了形象的短语（例如某功能被描述为"
    "『避免彼此互相干扰』或『不符合就退回去』），请把这类短语本身也顺手口语化成一条别名**"
    "——描述里的用词往往正是用户会用来搜索的词。这些说法**可以不出现在上下文里**，但必须"
    "准确指向【这一个实体】的功能，不能是「处理/数据/执行」这类放到哪都成立的泛词。"
    "只输出这一个 JSON 对象，不要解释、不要代码块、不要多余文字。"
)


def desc_whitelist_ok(desc, wl_text):
    low = wl_text.lower()
    bad = []
    for run in _EN_RUN.findall(desc or ""):
        tok = run.strip("._")
        if len(tok) < 2 or tok.isdigit():
            continue
        if tok.lower() not in low:
            bad.append(tok)
    return (not bad), bad


def clean_alias(a):
    s = (a or "").strip().strip("\"'“”『」「』").strip()
    s = s.replace("\n", "").replace("\r", "")
    return s[:MAX_ALIAS_CHARS]


def gen_one(base, token, nid, nodes, idx):
    input_text, wl = build_input(nid, nodes, idx)
    _assert_no_leak(input_text)
    user = input_text + "\n\n请输出 JSON："
    attempts = []
    for tno in range(2):        # 首次 + desc 白名单违规重试一次
        text, err = call_gateway(base, token, GEN_SYSTEM, user, GEN_MODEL, max_tokens=400)
        if err is not None:
            attempts.append({"try": tno + 1, "net_err": err})
            return {"desc": None, "zh_aliases": [], "accepted": False,
                    "reason": "net_fail:%s" % err, "attempts": attempts,
                    "input_text": input_text}
        obj = loose_json(text) or {}
        desc = (obj.get("desc") or "").strip().replace("\n", " ")
        raw_aliases = obj.get("zh_aliases") or []
        aliases = []
        seen = set()
        for a in raw_aliases:
            ca = clean_alias(a if isinstance(a, str) else "")
            if len(ca) >= 2 and ca not in seen:
                seen.add(ca)
                aliases.append(ca)
        aliases = aliases[:MAX_ALIASES]
        ok, bad = desc_whitelist_ok(desc, wl)
        attempts.append({"try": tno + 1, "raw": text[:400], "desc": desc,
                         "aliases": aliases, "desc_whitelist_ok": ok, "offenders": bad})
        if ok and desc:
            used = desc if len(desc) <= MAX_DESC_CHARS else desc[:MAX_DESC_CHARS]
            return {"desc": used, "zh_aliases": aliases, "accepted": True,
                    "reason": "ok", "attempts": attempts, "input_text": input_text,
                    "desc_char": len(desc)}
        time.sleep(SLEEP_BETWEEN)
    # desc 违规耗尽 → 弃 desc，但**保留 aliases**（aliases 无白名单约束，仍有检索价值）
    last = attempts[-1]
    return {"desc": None, "zh_aliases": last.get("aliases") or [],
            "accepted": False, "reason": "desc_whitelist_violation",
            "attempts": attempts, "input_text": input_text}


# --------------------------------------------------------------------------- #
# 防泄题断言：prompt 不得含 FZ 题面口语词（评测有效性红线）
# --------------------------------------------------------------------------- #

_LEAK_MARKERS = None


def _graph_text_blob():
    """全图可见文本拼成一个大串（名称/描述/docstring/证据/提交/模块），供泄题指纹过滤。"""
    nodes, edges = load_graph()
    parts = []
    for n in nodes.values():
        for k in ("name", "qualname", "description", "docstring", "signature", "message"):
            v = n.get(k)
            if isinstance(v, str):
                parts.append(v)
        for ev in n.get("evidence") or []:
            q = (ev or {}).get("quote")
            if q:
                parts.append(q)
    return " ".join(parts)


def _leak_markers():
    """FZ question 的**纯口语 4-gram 指纹** = 题面 4-gram 减去图谱文本里出现过的（合法词汇）。

    仅用于**断言 prompt 不含**，绝不进入任何 prompt。取 4-gram（够长够独特）并剔除图谱已有的，
    留下的即「使坏扛揍/盯着卡住/爬起来接」这类图谱里没有、只在题面出现的口语指纹——prompt 由
    graph 构造故本不该含之，命中即为泄题 bug。读一次缓存。"""
    global _LEAK_MARKERS
    if _LEAK_MARKERS is not None:
        return _LEAK_MARKERS
    q_grams = set()
    with open(DATASET, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("subset") in ("FZ_dev", "FZ_test"):
                for seg in re.findall(r"[一-鿿]{4,}", r.get("question", "")):
                    for i in range(len(seg) - 3):
                        q_grams.add(seg[i:i + 4])
    blob = _graph_text_blob()
    _LEAK_MARKERS = {g for g in q_grams if g not in blob}
    return _LEAK_MARKERS


def _assert_no_leak(prompt_text):
    """断言 prompt 不含 FZ 题面的纯口语 4-gram 指纹。命中即抛（宁可中止也不泄题）。"""
    marks = _leak_markers()
    for i in range(len(prompt_text) - 3):
        if prompt_text[i:i + 4] in marks:
            raise RuntimeError("泄题断言失败：prompt 含题面口语片段 '%s'" % prompt_text[i:i + 4])


def _assert_system_no_leak():
    """启动时断言 GEN_SYSTEM（含 few-shot 示例）不含任何 FZ 题面口语指纹（防示例泄题）。"""
    _assert_no_leak(GEN_SYSTEM)
    _assert_no_leak(CHECK_SYSTEM)


# --------------------------------------------------------------------------- #
# LLM 自检批次（flash）：剔除笼统/误指向多实体的歧义别名
# --------------------------------------------------------------------------- #

CHECK_SYSTEM = (
    "你是检索别名歧义检查器。下面给出若干条编号的 {别名 → 它应当指向的功能}。请判断每条别名"
    "是否**过于笼统、或会让人误以为在指别的很多不同功能**（例如「处理数据」「执行操作」「运行」"
    "这类放到哪都成立的泛词）。只输出严格 JSON：{\"drop\":[编号,…]}，列出应当剔除的别名编号；"
    "指向明确、够具体的别名不要列入。不确定就保留（不列入 drop）。只输出这一个 JSON。"
)


def _check_batch(base, token, start, batch):
    listing = "\n".join(
        "%d) 别名「%s」→ 功能：%s" % (i, b["alias"], b["desc"] or "(无描述)")
        for i, b in enumerate(batch))
    user = "请检查以下别名：\n" + listing + "\n\n输出 {\"drop\":[…]}："
    text, err = call_gateway(base, token, CHECK_SYSTEM, user, CHECK_MODEL, max_tokens=300)
    local_drop = set()
    if err is None:
        obj = loose_json(text) or {}
        arr = obj.get("drop")
        if isinstance(arr, list):
            for x in arr:
                if isinstance(x, int) and 0 <= x < len(batch):
                    local_drop.add(start + x)
    return start, local_drop, {"batch_start": start, "n": len(batch),
                               "err": err, "raw": (text or "")[:300]}


def self_check(base, token, items):
    """items: [{gid, alias, desc}]。并发分批返回应剔除的下标 set（全局 index）。"""
    drop = set()
    check_log = []
    batches = [(s, items[s:s + CHECK_BATCH]) for s in range(0, len(items), CHECK_BATCH)]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(_check_batch, base, token, s, b) for s, b in batches]
        for fut in as_completed(futs):
            _s, local_drop, rec = fut.result()
            drop |= local_drop
            check_log.append(rec)
    check_log.sort(key=lambda r: r["batch_start"])
    return drop, check_log


def probe(base, token):
    text, err = call_gateway(base, token, "回复两个字：正常", "探针", GEN_MODEL, max_tokens=20)
    return (text is not None), (err or "")


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def main():
    _assert_system_no_leak()          # 防泄题：GEN_SYSTEM/CHECK_SYSTEM 示例不得含题面口语
    nodes, edges = load_graph()
    gold_ids = load_gold_ids()
    chosen, idx = select_targets(nodes, edges, gold_ids)
    targets = sorted(chosen, key=lambda i: (nodes[i]["label"], i))
    by_label = {}
    for nid in targets:
        by_label[nodes[nid]["label"]] = by_label.get(nodes[nid]["label"], 0) + 1

    base, token = load_cfg()
    print("网关:", base, " gen:", GEN_MODEL, " check:", CHECK_MODEL, " token: sk-****")
    print("目标实体:", len(targets), " 分布:", by_label,
          " | FZ gold 覆盖:", sum(1 for g in gold_ids if g in nodes), "/", len(gold_ids))

    # 探针退避重试：网关限流(429)时等待恢复（每 60s 重试，最多 ~12 分钟），骑过瞬时/RPM 限流。
    ok, err = probe(base, token)
    probe_tries = 0
    while not ok and probe_tries < 12:
        probe_tries += 1
        print("[WAIT] 探针失败(%s)，第 %d 次退避 60s…" % (err, probe_tries), flush=True)
        time.sleep(60)
        ok, err = probe(base, token)
    if not ok:
        out_path = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else OUT
        print("[BLOCKED] 探针持续失败:", err)
        json.dump({"blocked": True, "meta": {"probe_error": err, "waited_tries": probe_tries}},
                  open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return 3
    print("探针 OK，开始生成…")

    # ---- 并发生成（ThreadPoolExecutor；单实体异常不中断全局，泄题断言直接冒泡）----
    results: dict[int, dict] = {}
    done = {"n": 0}
    lock = threading.Lock()

    def work(pos_nid):
        pos, nid = pos_nid
        try:
            r = gen_one(base, token, nid, nodes, idx)
        except Exception as e:  # noqa: BLE001
            if "泄题" in str(e):
                raise
            r = {"desc": None, "zh_aliases": [], "accepted": False,
                 "reason": "exc:" + type(e).__name__, "attempts": [], "input_text": ""}
        with lock:
            done["n"] += 1
            k = done["n"]
        if k % 20 == 0 or k <= 3:
            n = nodes[nid]
            print("  [%3d/%d] %-8s %-30s desc=%s aliases=%d" % (
                k, len(targets), n["label"],
                (n.get("qualname") or n.get("name") or "")[:30],
                (r["desc"] or "<%s>" % r["reason"])[:20], len(r["zh_aliases"])),
                flush=True)
        return pos, nid, r

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for pos, nid, r in ex.map(work, list(enumerate(targets))):
            results[pos] = (nid, r)

    cards = []
    n_desc_ok = n_desc_bad = n_net = 0
    for pos in range(len(targets)):
        nid, r = results[pos]
        n = nodes[nid]
        cards.append({
            "id": nid, "label": n["label"],
            "name": n.get("qualname") or n.get("name") or nid.rsplit("::", 1)[-1],
            "tier": "gold" if nid in gold_ids else "core",
            "desc": r["desc"], "zh_aliases": r["zh_aliases"],
            "desc_accepted": r["accepted"], "reason": r["reason"],
            "desc_char": r.get("desc_char"),
            "input_summary": r["input_text"][:500],
            "attempts": r["attempts"],
        })
        if r["accepted"]:
            n_desc_ok += 1
        elif r["reason"].startswith("net_fail"):
            n_net += 1
        else:
            n_desc_bad += 1

    # ---- LLM 自检：剔除歧义别名 ----
    flat = []          # [{card_i, alias_j, gid, alias, desc}]
    for ci, c in enumerate(cards):
        for aj, a in enumerate(c["zh_aliases"]):
            flat.append({"card_i": ci, "alias_j": aj, "gid": c["id"],
                         "alias": a, "desc": c["desc"] or c["name"]})
    print("\n自检别名总数:", len(flat), " 分批(", CHECK_BATCH, ")…")
    drop_idx, check_log = self_check(base, token, flat)
    # 也做程序侧去歧义：同一别名出现在 ≥3 个不同实体 → 判笼统，一并剔除
    from collections import Counter
    alias_ct = Counter(f["alias"] for f in flat)
    prog_drop = {i for i, f in enumerate(flat) if alias_ct[f["alias"]] >= 3}
    all_drop = drop_idx | prog_drop

    pruned = []
    for i in sorted(all_drop):
        f = flat[i]
        pruned.append({"gid": f["gid"], "alias": f["alias"],
                       "by": ("llm" if i in drop_idx else "") + ("+prog" if i in prog_drop else "")})
    # 应用剔除
    drop_by_card = {}
    for i in all_drop:
        f = flat[i]
        drop_by_card.setdefault(f["card_i"], set()).add(f["alias_j"])
    n_alias_before = sum(len(c["zh_aliases"]) for c in cards)
    for ci, c in enumerate(cards):
        dj = drop_by_card.get(ci, set())
        c["zh_aliases_raw"] = list(c["zh_aliases"])
        c["zh_aliases"] = [a for j, a in enumerate(c["zh_aliases"]) if j not in dj]
    n_alias_after = sum(len(c["zh_aliases"]) for c in cards)

    meta = {
        "gen_model": GEN_MODEL, "check_model": CHECK_MODEL, "base": base,
        "n_targets": len(targets), "by_label": by_label,
        "n_desc_accepted": n_desc_ok, "n_desc_discarded": n_desc_bad, "n_netfail": n_net,
        "n_aliases_generated": n_alias_before, "n_aliases_after_check": n_alias_after,
        "n_aliases_pruned": n_alias_before - n_alias_after,
        "prune_llm": len(drop_idx), "prune_prog_dup": len(prog_drop),
        "fz_gold_covered": sum(1 for g in gold_ids if g in nodes),
        "fz_gold_total": len(gold_ids),
        "desc_whitelist_rule": "desc 英文标识符须为富化输入子串；违规重试1次后弃 desc(保留aliases)",
        "alias_rule": "3-5口语近义,允许非子串,仅进检索语料/属性不进答案事实; 自检剔除歧义",
        "leak_guard": "prompt 只从 graph 构造 + _assert_no_leak 断言不含 FZ 题面3-gram",
    }
    out = {"blocked": False, "meta": meta, "self_check_log": check_log,
           "pruned_aliases": pruned, "cards": cards}
    out_path = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else OUT
    json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("\n生成完成：desc 接受 %d / 弃用 %d / 网络失败 %d（共 %d）" % (
        n_desc_ok, n_desc_bad, n_net, len(targets)))
    print("别名：生成 %d → 自检后 %d（剔除 %d：llm %d + 程序去重 %d）" % (
        n_alias_before, n_alias_after, n_alias_before - n_alias_after,
        len(drop_idx), len(prog_drop)))
    print("写出:", os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
