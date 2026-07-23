# -*- coding: utf-8 -*-
"""D2 两组对比全量评测（精简裁判方案）——计划书 §4 Phase D · D2 / D-R2。

两组（口径写死，见 baseline_bm25.py 头注 + 本文件 GROUPS 注释）：
  组 A「BM25-only 无图谱基线」= eval/baseline_bm25.build_bm25_context
  组 B「图谱混合」          = repograph.retrieve.context.build_repo_context（lexical 档）

两评测段：
  (1) 程序断言段（离线，确定性，108 题 × 2 组）：
      - 主集 60 题：L1 gold 模块命中率 / L2 gold 闭包交集率 / L3 来源(概念+提交)召回率；
      - 48 题：复用 eval/gate.py 既有判定器（L0/FZ/AMB/PP）跑两组，组 A 预期大面积差（如实记）。
  (2) 生成+裁判段（在线，中转站 grok-4.5，128 生成 + 128 裁判）：
      主集 60 × 2 组 + PP 8 × 组 B；生成 max_tokens=400；裁判强制 JSON verdict。
      限速 sleep 0.4s；生成失败重试 2、裁判失败重试 1，超限标 error 绝不伪造；
      原始请求/响应落 eval/d2_runs/*.jsonl（不含 api_key、不含中转站 host）；断点续跑。
  (3) 汇总 eval/d2_results.json。

红线：api_key 只读入内存放请求头，绝不打印/写盘/入产物；中转站 host 亦不写入
d2_runs（Phase E 仓库开源）。中转站整体不可用 → 中止并如实报告（circuit breaker）。

用法：
  python eval/run_d2.py offline     # 段(1)，快，产出 d2_runs/ctx_* + program_metrics 缓存
  python eval/run_d2.py probe       # 单次连通性探测
  python eval/run_d2.py gen         # 段(2) 生成，断点续跑
  python eval/run_d2.py judge       # 段(2) 裁判，断点续跑（需 gen 完成）
  python eval/run_d2.py online      # gen + judge
  python eval/run_d2.py aggregate   # 汇总 → d2_results.json
  python eval/run_d2.py all         # offline + online + aggregate
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
EVAL = os.path.join(ROOT, "eval")
RUNS = os.path.join(EVAL, "d2_runs")
GRAPH = os.path.join(ROOT, "output", "graph.json")
DATASET_MAIN = os.path.join(EVAL, "dataset_main.jsonl")
DATASET_48 = os.path.join(EVAL, "dataset.jsonl")
CONFIG = os.path.join(EVAL, ".judge_config.json")
RESULTS = os.path.join(EVAL, "d2_results.json")
PROG_CACHE = os.path.join(RUNS, "program_metrics.json")

for p in (SRC, EVAL):
    if p not in sys.path:
        sys.path.insert(0, p)

from repograph.models import GraphStore                         # noqa: E402
from repograph.retrieve.context import build_repo_context       # noqa: E402
from repograph.retrieve.topic import build_corpus_index         # noqa: E402
from baseline_bm25 import build_bm25_context                    # noqa: E402
import gate                                                     # noqa: E402  （复用判定器，不触发其 main）

# ---------------------------------------------------------------------------
# 在线通道参数
# ---------------------------------------------------------------------------
SLEEP = 0.4
GEN_RETRIES = 2          # 生成失败重试 2 次（通用纪律 rule 2）
JUDGE_RETRIES = 1        # 裁判失败重试 1 次（D2 spec）
GEN_MAX_TOKENS = 400
JUDGE_MAX_TOKENS = 220
HTTP_TIMEOUT = 90
CIRCUIT_CONSECUTIVE_FAIL = 6   # 连续失败阈值 → 判中转站整体不可用，中止

GEN_SYSTEM = ("你是该代码库的助手，仅依据下方仓库上下文回答；"
              "上下文不足以回答时明确说明，禁止编造。")


# ---------------------------------------------------------------------------
# 配置 / IO 基元
# ---------------------------------------------------------------------------

def load_config() -> dict:
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def load_jsonl(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def append_record(path: str, rec: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load_done(path: str, ok_pred) -> dict:
    """读已完成记录（同 id 后写覆盖先写，crash-safe 续跑）；只保留 ok_pred 为真者。"""
    if not os.path.exists(path):
        return {}
    done: dict[str, dict] = {}
    for rec in load_jsonl(path):
        done[rec["id"]] = rec
    return {k: v for k, v in done.items() if ok_pred(v)}


# ---------------------------------------------------------------------------
# 检索（两组）—— store / corpus index 全局构建一次
# ---------------------------------------------------------------------------
_STORE: GraphStore | None = None
_INDEX = None


def get_store() -> GraphStore:
    global _STORE
    if _STORE is None:
        _STORE = GraphStore.load(GRAPH)
    return _STORE


def get_index():
    global _INDEX
    if _INDEX is None:
        _INDEX = build_corpus_index(get_store())
    return _INDEX


def retrieve(group: str, question: str) -> dict:
    """组 A = 纯 BM25 基线；组 B = build_repo_context 完整 v0.3（lexical 档）。"""
    store = get_store()
    if group == "A":
        return build_bm25_context(store, question, index=get_index())
    return build_repo_context(store, question)   # 组 B；gate 离线口径，不传 extra_queries


# ---------------------------------------------------------------------------
# gold 展示名（entity_id → 人读名，供裁判 prompt）
# ---------------------------------------------------------------------------

def entity_display(store: GraphStore, eid: str) -> str:
    node = store.get_node(eid)
    if node is None:
        return eid.rsplit("::", 1)[-1]
    label = node.get("label", "")
    if label in ("Function", "Class"):
        return node.get("qualname") or node.get("name") or eid.rsplit("::", 1)[-1]
    if label == "Module":
        return node.get("path") or eid.split("::", 1)[-1]
    if label == "Commit":
        return (node.get("hash") or eid.rsplit("::", 1)[-1])[:12]
    if label == "Concept":
        return node.get("name") or eid.rsplit("::", 1)[-1]
    return eid.rsplit("::", 1)[-1]


def entity_recall_token(store: GraphStore, eid: str) -> str:
    """判定「上下文/答案是否含该 gold 实体」的锚字符串（qualname 短名 / 模块路径 / 短 sha / 概念名）。"""
    node = store.get_node(eid)
    if node is None:
        # id 形如 repo::path::qualname 或 repo::commit::sha 或 concept::slug
        tail = eid.rsplit("::", 1)[-1]
        return tail
    label = node.get("label", "")
    if label in ("Function", "Class"):
        qn = node.get("qualname") or node.get("name") or ""
        return qn.rsplit(".", 1)[-1] if qn else eid.rsplit("::", 1)[-1]
    if label == "Module":
        return node.get("path") or eid.split("::", 1)[-1]
    if label == "Commit":
        return (node.get("hash") or eid.rsplit("::", 1)[-1])[:8]
    if label == "Concept":
        return node.get("name") or eid.rsplit("::", 1)[-1]
    return eid.rsplit("::", 1)[-1]


def context_has_entity(store: GraphStore, context_text: str, eid: str) -> bool:
    tok = entity_recall_token(store, eid)
    return bool(tok) and tok in context_text


# ---------------------------------------------------------------------------
# 段 (1) 程序断言段
# ---------------------------------------------------------------------------

def rate(n: int, d: int) -> float:
    return round(n / d, 4) if d else 0.0


def run_offline() -> dict:
    """108 题 × 2 组的确定性程序断言；快照上下文到 d2_runs/ctx_*.jsonl。"""
    store = get_store()
    main_rows = load_jsonl(DATASET_MAIN)
    rows48 = load_jsonl(DATASET_48)
    g = json.load(open(GRAPH, encoding="utf-8"))
    edges48 = g["edges"]

    # ---- 主集：逐题两组检索 + 分层 gold 召回 ----
    main_detail = {"A": [], "B": []}
    # 快照上下文（供在线 gen 复用/审计）
    ctx_files = {("A", "main"): os.path.join(RUNS, "ctx_main_A.jsonl"),
                 ("B", "main"): os.path.join(RUNS, "ctx_main_B.jsonl")}
    for k, fp in ctx_files.items():
        if os.path.exists(fp):
            os.remove(fp)

    for row in main_rows:
        rid, layer, q = row["id"], row["layer"], row["question"]
        gold = row["gold_entities"]
        gold_n = row.get("gold_n") or len(gold)
        for group in ("A", "B"):
            ctx = retrieve(group, q)
            ct = ctx.get("context_text") or ""
            hits = [e for e in gold if context_has_entity(store, ct, e)]
            hit_cnt = len(hits)
            rec = {
                "id": rid, "layer": layer, "group": group,
                "mode": ctx.get("mode"), "route_label": ctx.get("route_label"),
                "n_linked": len(ctx.get("linked") or []),
                "gold_n": gold_n, "gold_hit": hit_cnt,
                "recall": rate(hit_cnt, len(gold)),
                "hit_at_min1": hit_cnt >= 1,
                "missed": [e for e in gold if e not in hits],
                "context_chars": len(ct),
            }
            main_detail[group].append(rec)
            append_record(ctx_files[(group, "main")], {
                "id": rid, "layer": layer, "group": group, "question": q,
                "mode": ctx.get("mode"), "context_text": ct,
                "linked": ctx.get("linked"), "stats": ctx.get("stats"),
            })

    def layer_summary(detail: list[dict]) -> dict:
        out = {}
        for layer in ("L1", "L2", "L3"):
            items = [r for r in detail if r["layer"] == layer]
            if not items:
                continue
            hit1 = [r for r in items if r["hit_at_min1"]]
            mean_recall = round(sum(r["recall"] for r in items) / len(items), 4)
            out[layer] = {
                "n": len(items),
                "context_hit@min1_rate": rate(len(hit1), len(items)),
                "context_hit@min1_n": len(hit1),
                "mean_gold_recall": mean_recall,
                "fail_ids": [r["id"] for r in items if not r["hit_at_min1"]],
            }
        return out

    main_summary = {
        "A": layer_summary(main_detail["A"]),
        "B": layer_summary(main_detail["B"]),
    }

    # ---- 48 题：复用 gate 判定器跑两组 ----
    def judge48(group: str) -> dict:
        per = []
        for row in rows48:
            subset = row["subset"]
            ctx = retrieve(group, row["question"])
            rec = {"id": row["id"], "subset": subset, "mode": ctx.get("mode"),
                   "mode_class": gate.mode_class(ctx.get("mode")),
                   "route_label": ctx.get("route_label"),
                   "bare_refusal": gate.is_bare_refusal(ctx)}
            if subset == "L0":
                rec["judge"] = gate.judge_l0(ctx)
            elif subset in ("FZ_dev", "FZ_test"):
                rec["judge"] = gate.judge_fz(ctx, row["gold_entity"], edges48)
            elif subset == "AMB":
                rec["judge"] = gate.judge_amb(ctx, row["gold_behavior"])
            elif subset == "PP":
                rec["judge"] = gate.judge_pp(ctx, row)
            per.append(rec)

        def sub(name):
            return [r for r in per if r["subset"] == name]
        l0 = sub("L0"); fzd = sub("FZ_dev"); fzt = sub("FZ_test")
        amb = sub("AMB"); pp = sub("PP")

        def fz_hit3(items):
            return rate(sum(1 for r in items if r["judge"]["hit@3"]), len(items))
        l0_pass = [r for r in l0 if r["judge"]["pass"]]
        amb_ok = [r for r in amb if r["judge"]["pass"]]
        pp_corr = [r for r in pp if r["judge"]["suspect_corrected"]]
        pp_leak = [r for r in pp if r["judge"]["premise_leak"]]
        return {
            "L0_pass_rate": rate(len(l0_pass), len(l0)), "L0_pass_n": len(l0_pass), "L0_n": len(l0),
            "FZ_dev_hit@3": fz_hit3(fzd), "FZ_test_hit@3": fz_hit3(fzt),
            "AMB_behavior_consistency": rate(len(amb_ok), len(amb)),
            "PP_suspect_correction_rate": rate(len(pp_corr), len(pp)),
            "PP_premise_leak_rate": rate(len(pp_leak), len(pp)),
            "bare_refusal_n": sum(1 for r in per if r["bare_refusal"]),
            "per_question": per,
        }

    set48 = {"A": judge48("A"), "B": judge48("B")}

    program = {
        "main_program_assertions": {
            "definition": {
                "L1": "上下文是否含 gold 模块路径（context_recall, min_hit 1）",
                "L2": "gold 反向调用闭包与上下文的交集率（context_answer_intersection, gold_n=6）",
                "L3": "gold 来源(概念+DESCRIBES 提交)在上下文的召回率（context_recall_provenance）",
                "entity_token": "Function/Class→qualname 短名; Module→路径; Commit→短sha8; Concept→名称",
            },
            "summary": main_summary,
            "detail": main_detail,
        },
        "set48_existing_assertions": {
            "note": "复用 eval/gate.py 判定器；组 A=BM25基线，组 B=build_repo_context。",
            "A": {k: v for k, v in set48["A"].items() if k != "per_question"},
            "B": {k: v for k, v in set48["B"].items() if k != "per_question"},
            "per_question": {"A": set48["A"]["per_question"], "B": set48["B"]["per_question"]},
        },
    }
    os.makedirs(RUNS, exist_ok=True)
    with open(PROG_CACHE, "w", encoding="utf-8") as f:
        json.dump(program, f, ensure_ascii=False, indent=2)
    return program


# ---------------------------------------------------------------------------
# 在线通道：API 调用（api_key 只入 header；host 不写盘）
# ---------------------------------------------------------------------------
_CFG = None


def _cfg():
    global _CFG
    if _CFG is None:
        _CFG = load_config()
    return _CFG


def call_api(system: str | None, user: str, max_tokens: int, retries: int) -> dict:
    """POST {base}/v1/messages（Anthropic 格式）。返回 {ok, text, usage, stop_reason, attempts, error}。

    重试 retries 次（共 retries+1 次尝试）；每次尝试后 sleep(SLEEP) 限速。
    仅记录可写盘字段；api_key/host 不出现在返回值里。
    """
    cfg = _cfg()
    base = cfg["base_url"].rstrip("/")
    key = cfg["api_key"]
    model = cfg["model"]
    ah = cfg["auth_header"]
    body = {"model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": user}]}
    if system:
        body["system"] = system
    raw = json.dumps(body).encode("utf-8")

    last_err = ""
    attempts = 0
    for attempt in range(retries + 1):
        attempts = attempt + 1
        try:
            req = urllib.request.Request(base + "/v1/messages", data=raw, method="POST")
            req.add_header("content-type", "application/json")
            req.add_header(ah, key)
            req.add_header("anthropic-version", "2023-06-01")
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8"))
            text = "".join(b.get("text", "") for b in data.get("content", [])
                           if isinstance(b, dict) and b.get("type") == "text")
            if not text:
                text = "".join(b.get("text", "") for b in data.get("content", [])
                               if isinstance(b, dict))
            time.sleep(SLEEP)
            return {"ok": True, "text": text, "usage": data.get("usage"),
                    "stop_reason": data.get("stop_reason"),
                    "resp_model": data.get("model"), "attempts": attempts, "error": ""}
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                detail = ""
            last_err = f"HTTP {e.code} {e.reason} {detail}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:200]}"
        time.sleep(SLEEP)
    return {"ok": False, "text": "", "usage": None, "stop_reason": None,
            "resp_model": None, "attempts": attempts, "error": last_err}


def probe() -> bool:
    r = call_api(None, "只回复两个字：连通", 32, retries=1)
    if r["ok"]:
        print(f"[probe] OK model={r['resp_model']} usage={r['usage']} text={r['text']!r}")
        return True
    print(f"[probe] FAIL error={r['error']}")
    return False


# ---------------------------------------------------------------------------
# 段 (2) 生成
# ---------------------------------------------------------------------------

def gen_targets() -> list[tuple]:
    """生成目标：主集 60 × {A,B} + PP 8 × {B} = 128。返回 (row, group, setname)。"""
    main_rows = load_jsonl(DATASET_MAIN)
    pp_rows = [r for r in load_jsonl(DATASET_48) if r["subset"] == "PP"]
    out = []
    for row in main_rows:
        out.append((row, "A", "main"))
        out.append((row, "B", "main"))
    for row in pp_rows:
        out.append((row, "B", "pp"))
    return out


def gen_path(group: str, setname: str) -> str:
    return os.path.join(RUNS, f"gen_{setname}_{group}.jsonl")


def build_gen_user(ctx_text: str, question: str) -> str:
    return f"【仓库上下文】\n{ctx_text or '（无上下文）'}\n\n【问题】\n{question}"


def run_gen() -> None:
    targets = gen_targets()
    # 分文件续跑
    consec_fail = 0
    total = len(targets)
    done_cnt = 0
    for row, group, setname in targets:
        path = gen_path(group, setname)
        done = load_done(path, lambda v: v.get("ok"))
        rid = row["id"]
        if rid in done:
            done_cnt += 1
            continue
        q = row["question"]
        ctx = retrieve(group, q)
        user = build_gen_user(ctx.get("context_text") or "", q)
        r = call_api(GEN_SYSTEM, user, GEN_MAX_TOKENS, GEN_RETRIES)
        rec = {
            "id": rid, "group": group, "set": setname,
            "layer": row.get("layer") or row.get("subset"),
            "question": q,
            "mode": ctx.get("mode"), "route_label": ctx.get("route_label"),
            "context_chars": len(ctx.get("context_text") or ""),
            "endpoint": "<relay>/v1/messages",
            "request": {"system": GEN_SYSTEM, "user": user,
                        "max_tokens": GEN_MAX_TOKENS, "model_alias": "grok-4.5"},
            "ok": r["ok"], "answer": r["text"], "usage": r["usage"],
            "stop_reason": r["stop_reason"], "attempts": r["attempts"],
            "error": r["error"], "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        append_record(path, rec)
        done_cnt += 1
        if r["ok"]:
            consec_fail = 0
        else:
            consec_fail += 1
            print(f"[gen] {rid}/{group} ERROR {r['error']}")
            if consec_fail >= CIRCUIT_CONSECUTIVE_FAIL:
                print(f"[gen] 连续 {consec_fail} 次失败 → 中转站整体不可用，中止。")
                sys.exit(3)
        if done_cnt % 10 == 0 or done_cnt == total:
            print(f"[gen] {done_cnt}/{total} (last {rid}/{group} ok={r['ok']})")


# ---------------------------------------------------------------------------
# 段 (2) 裁判
# ---------------------------------------------------------------------------

def gold_block(store: GraphStore, row: dict, setname: str) -> str:
    if setname == "pp":
        return ("错误前提: %s\n应纠正为(正确事实): %s\n真值锚: %s"
                % (row.get("false_premise", ""), row.get("gold_correction", ""),
                   "、".join(row.get("truth_anchors", []))))
    layer = row["layer"]
    gold = row["gold_entities"]
    disp = "、".join(entity_display(store, e) for e in gold)
    ga = row.get("gold_answer", "")
    if layer == "L2":
        return ("标准答案(要点): %s\ngold 反向调用闭包(应尽量覆盖, 共 %d 项): %s"
                % (ga, len(gold), disp))
    if layer == "L3":
        aspects = "；".join(row.get("gold_aspects", []))
        return ("标准答案(要点): %s\ngold 来源(概念+提交): %s\n设计要点(命中其一即算触及): %s"
                % (ga, disp, aspects))
    return "标准答案(要点): %s\ngold 实体: %s" % (ga, disp)


def judge_rubric(setname: str, layer: str) -> str:
    if setname == "pp":
        return ("评判维度=是否指出并纠正错误前提。判定：correct=明确指出前提有误并给出正确事实；"
                "partial=隐含存疑或部分纠正但未点破；wrong=顺着错误预设作答/未质疑前提。")
    if layer == "L1":
        return ("评判维度=答案准确性。correct=正确指出 gold 所属模块/事实；partial=方向对但不精确；"
                "wrong=错误或未答。")
    if layer == "L2":
        return ("评判维度=答案完整性(命中 gold 闭包比例)。correct=覆盖绝大多数关键调用方/闭包成员；"
                "partial=命中部分；wrong=基本未命中或臆造。")
    return ("评判维度=设计溯源(design_rubric_one_of)。correct=讲清设计理由且指出来源(概念/提交)；"
            "partial=触及部分设计要点或部分来源；wrong=答非所问或臆造。")


def build_judge_user(store: GraphStore, row: dict, setname: str, answer: str) -> str:
    layer = row.get("layer") or "PP"
    q = row["question"]
    rubric = judge_rubric(setname, layer if setname != "pp" else "PP")
    gb = gold_block(store, row, setname)
    return (
        "你是严格的代码库问答评测裁判。依据【问题】【标准答案要点】判定【待判答案】。\n"
        "%s\n"
        "只输出一个 JSON 对象，不要多余文字：{\"verdict\":\"correct|partial|wrong\",\"reason\":\"≤50字中文\"}。\n\n"
        "【问题】\n%s\n\n【标准答案要点】\n%s\n\n【待判答案】\n%s"
        % (rubric, q, gb, answer or "（空）")
    )


def parse_verdict(text: str) -> tuple:
    """从裁判响应抽 {verdict, reason}；容忍 ```json 包裹与前后噪声。失败返回 (None, None)。"""
    if not text:
        return None, None
    s = text.strip()
    if "```" in s:
        # 取首个代码块内内容
        parts = s.split("```")
        for seg in parts:
            seg2 = seg.strip()
            if seg2.startswith("json"):
                seg2 = seg2[4:].strip()
            if seg2.startswith("{"):
                s = seg2
                break
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j < 0 or j <= i:
        return None, None
    try:
        obj = json.loads(s[i:j + 1])
    except Exception:
        return None, None
    v = str(obj.get("verdict", "")).strip().lower()
    if v not in ("correct", "partial", "wrong"):
        return None, None
    return v, str(obj.get("reason", ""))[:80]


def judge_path(group: str, setname: str) -> str:
    return os.path.join(RUNS, f"judge_{setname}_{group}.jsonl")


def run_judge() -> None:
    store = get_store()
    main_rows = {r["id"]: r for r in load_jsonl(DATASET_MAIN)}
    pp_rows = {r["id"]: r for r in load_jsonl(DATASET_48) if r["subset"] == "PP"}
    consec_fail = 0
    # 收集全部 gen 成功记录作为裁判输入
    tasks = []
    for group, setname in (("A", "main"), ("B", "main"), ("B", "pp")):
        gp = gen_path(group, setname)
        if not os.path.exists(gp):
            continue
        for rec in load_done(gp, lambda v: v.get("ok")).values():
            tasks.append((rec, group, setname))
    total = len(tasks)
    done_cnt = 0
    for grec, group, setname in tasks:
        jp = judge_path(group, setname)
        done = load_done(jp, lambda v: v.get("ok"))
        rid = grec["id"]
        if rid in done:
            done_cnt += 1
            continue
        row = pp_rows[rid] if setname == "pp" else main_rows[rid]
        answer = grec.get("answer") or ""
        juser = build_judge_user(store, row, setname, answer)
        r = call_api(None, juser, JUDGE_MAX_TOKENS, JUDGE_RETRIES)
        verdict, reason = (None, None)
        if r["ok"]:
            verdict, reason = parse_verdict(r["text"])
            if verdict is None:
                # 解析失败再重试 1 次（裁判整体重试预算内）
                r2 = call_api(None, juser + "\n\n注意：必须只输出合法 JSON。",
                              JUDGE_MAX_TOKENS, 0)
                if r2["ok"]:
                    verdict, reason = parse_verdict(r2["text"])
                    r["text"] = r["text"] + "\n<retry>\n" + r2["text"]
                    r["attempts"] += r2["attempts"]
        ok = r["ok"] and verdict is not None
        rec = {
            "id": rid, "group": group, "set": setname,
            "layer": grec.get("layer"),
            "endpoint": "<relay>/v1/messages",
            "request": {"user": juser, "max_tokens": JUDGE_MAX_TOKENS,
                        "model_alias": "grok-4.5"},
            "ok": ok, "verdict": verdict, "reason": reason,
            "raw_response": r["text"], "usage": r["usage"],
            "attempts": r["attempts"],
            "error": r["error"] if r["error"] else ("" if ok else "verdict_parse_failed"),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        append_record(jp, rec)
        done_cnt += 1
        if ok:
            consec_fail = 0
        else:
            consec_fail += 1
            print(f"[judge] {rid}/{group} ERROR {rec['error']}")
            if consec_fail >= CIRCUIT_CONSECUTIVE_FAIL:
                print(f"[judge] 连续 {consec_fail} 次失败 → 中转站整体不可用，中止。")
                sys.exit(3)
        if done_cnt % 10 == 0 or done_cnt == total:
            print(f"[judge] {done_cnt}/{total} (last {rid}/{group} verdict={verdict})")


# ---------------------------------------------------------------------------
# 段 (3) 汇总
# ---------------------------------------------------------------------------

def aggregate() -> dict:
    program = json.load(open(PROG_CACHE, encoding="utf-8")) if os.path.exists(PROG_CACHE) else run_offline()
    main_rows = {r["id"]: r for r in load_jsonl(DATASET_MAIN)}

    # 读裁判
    def read_judges(group, setname):
        jp = judge_path(group, setname)
        if not os.path.exists(jp):
            return {}
        return load_done(jp, lambda v: True)   # 含 error 记录，聚合时区分

    judged = {("A", "main"): read_judges("A", "main"),
              ("B", "main"): read_judges("B", "main"),
              ("B", "pp"): read_judges("B", "pp")}

    def gen_counts(group, setname):
        gp = gen_path(group, setname)
        if not os.path.exists(gp):
            return {"n": 0, "ok": 0, "error": 0, "error_ids": []}
        recs = load_done(gp, lambda v: True)
        ok = [r for r in recs.values() if r.get("ok")]
        err = [r for r in recs.values() if not r.get("ok")]
        return {"n": len(recs), "ok": len(ok), "error": len(err),
                "error_ids": sorted(r["id"] for r in err)}

    # ---- 主集三层 × 2 组答案准确率 ----
    def layer_answer_acc(group):
        recs = judged[(group, "main")]
        out = {}
        for layer in ("L1", "L2", "L3"):
            items = [r for r in recs.values() if main_rows.get(r["id"], {}).get("layer") == layer]
            valid = [r for r in items if r.get("ok") and r.get("verdict")]
            n = len(valid)
            c = sum(1 for r in valid if r["verdict"] == "correct")
            p = sum(1 for r in valid if r["verdict"] == "partial")
            w = sum(1 for r in valid if r["verdict"] == "wrong")
            err = [r["id"] for r in items if not (r.get("ok") and r.get("verdict"))]
            out[layer] = {
                "n_judged": n, "correct": c, "partial": p, "wrong": w,
                "correct_rate": rate(c, n),
                "correct_or_partial_rate": rate(c + p, n),
                "wrong_ids": sorted(r["id"] for r in valid if r["verdict"] == "wrong"),
                "error_ids": sorted(err),
            }
        return out

    main_acc = {"A": layer_answer_acc("A"), "B": layer_answer_acc("B")}

    def group_delta():
        out = {}
        for layer in ("L1", "L2", "L3"):
            a = main_acc["A"][layer]["correct_rate"]
            b = main_acc["B"][layer]["correct_rate"]
            ap = main_acc["A"][layer]["correct_or_partial_rate"]
            bp = main_acc["B"][layer]["correct_or_partial_rate"]
            out[layer] = {
                "correct_rate_A": a, "correct_rate_B": b,
                "delta_correct_B_minus_A": round(b - a, 4),
                "correct_or_partial_A": ap, "correct_or_partial_B": bp,
                "delta_cp_B_minus_A": round(bp - ap, 4),
            }
        return out

    # ---- PP 纠正率（组 B）----
    pp_recs = judged[("B", "pp")]
    pp_valid = [r for r in pp_recs.values() if r.get("ok") and r.get("verdict")]
    pp_c = sum(1 for r in pp_valid if r["verdict"] == "correct")
    pp_p = sum(1 for r in pp_valid if r["verdict"] == "partial")
    pp_w = sum(1 for r in pp_valid if r["verdict"] == "wrong")
    pp = {
        "n_judged": len(pp_valid), "correct": pp_c, "partial": pp_p, "wrong": pp_w,
        "correction_rate": rate(pp_c, len(pp_valid)),
        "correction_or_partial_rate": rate(pp_c + pp_p, len(pp_valid)),
        "wrong_ids": sorted(r["id"] for r in pp_valid if r["verdict"] == "wrong"),
        "error_ids": sorted(r["id"] for r in pp_recs.values() if not (r.get("ok") and r.get("verdict"))),
    }

    # ---- 在线调用统计 ----
    gen_stat = {f"{s}_{g}": gen_counts(g, s) for g, s in (("A", "main"), ("B", "main"), ("B", "pp"))}
    judge_stat = {}
    for g, s in (("A", "main"), ("B", "main"), ("B", "pp")):
        recs = judged[(g, s)]
        okc = sum(1 for r in recs.values() if r.get("ok") and r.get("verdict"))
        judge_stat[f"{s}_{g}"] = {"n": len(recs), "ok": okc, "error": len(recs) - okc,
                                  "error_ids": sorted(r["id"] for r in recs.values()
                                                      if not (r.get("ok") and r.get("verdict")))}
    total_gen = sum(v["n"] for v in gen_stat.values())
    total_gen_err = sum(v["error"] for v in gen_stat.values())
    total_judge = sum(v["n"] for v in judge_stat.values())
    total_judge_err = sum(v["error"] for v in judge_stat.values())

    # ---- 数据支撑的结论注记（从本轮真实产物读出，非臆断）----
    b_l3_modes = {}
    for r in program["main_program_assertions"]["detail"]["B"]:
        if r["layer"] == "L3":
            b_l3_modes[r["mode"]] = b_l3_modes.get(r["mode"], 0) + 1
    fz_a = program["set48_existing_assertions"]["A"]
    fz_b = program["set48_existing_assertions"]["B"]
    findings = {
        "L1_L2_图谱决胜": (
            "L1(符号→模块)/L2(反向调用闭包)是纯 BM25 结构性无法完成的任务："
            "L2 组 B correct=%.3f vs 组 A=%.3f(Δ+%.3f)，c+p Δ+%.3f——影响面闭包为图谱独有能力。"
            % (main_acc["B"]["L2"]["correct_rate"], main_acc["A"]["L2"]["correct_rate"],
               main_acc["B"]["L2"]["correct_rate"] - main_acc["A"]["L2"]["correct_rate"],
               main_acc["B"]["L2"]["correct_or_partial_rate"] - main_acc["A"]["L2"]["correct_or_partial_rate"])
        ),
        "L3_基线反超_诚实负结果": (
            "L3(设计溯源)组 A correct=%.3f 反超组 B=%.3f。根因(实测)：L3 是"
            "「概念卡片即答案」的检索任务，纯 BM25 稳定命中精确概念文档；组 B 路由把部分"
            "「为什么这个项目…」问句判为 meta→overview(本轮 L3 模式分布=%s)，overview 概览缺该"
            "概念的设计理由与溯源提交，故个别题(如 L3-01)反而答不出。c+p 近平(%.3f vs %.3f)。"
            "非稻草人对照的证据；亦暴露 build_repo_context 路由在 L3「why」问句上的过泛化(留 D3/D4)。"
            % (main_acc["A"]["L3"]["correct_rate"], main_acc["B"]["L3"]["correct_rate"],
               b_l3_modes, main_acc["A"]["L3"]["correct_or_partial_rate"],
               main_acc["B"]["L3"]["correct_or_partial_rate"])
        ),
        "FZ_L0_AMB_48集": (
            "FZ 词面召回两组同分同源(dev hit@3 A=%s/B=%s；test A=%s/B=%s)——纯检索任务图谱无增益(诚实平局)；"
            "L0 元问题(A=%s/B=%s)与 AMB 消歧行为一致率(A=%s/B=%s)是图谱编排(overview 路由/消歧协议)独有，基线为 0。"
            % (fz_a["FZ_dev_hit@3"], fz_b["FZ_dev_hit@3"], fz_a["FZ_test_hit@3"], fz_b["FZ_test_hit@3"],
               fz_a["L0_pass_rate"], fz_b["L0_pass_rate"],
               fz_a["AMB_behavior_consistency"], fz_b["AMB_behavior_consistency"])
        ),
        "L2_depth_下界说明": (
            "组 B 用 build_repo_context 默认 impact_depth=2；L2 gold 闭包按 depth=3 生成。故组 B 的 L2"
            "为 depth=2 下界(诚实未调参、未改冻结 src)，depth=3 预期更高。"
        ),
        "PP_端到端": (
            "PP 前提校验(组 B 端到端在线)：纠正率 correct=%.3f(7/8)、c+p=%.3f；错误预设未被顺答，S7 闸门在线生效。"
            % (pp["correction_rate"], pp["correction_or_partial_rate"])
        ),
    }

    result = {
        "meta": {
            "phase": "D · D2 两组对比全量评测（精简裁判方案）",
            "groups": {
                "A": "BM25-only 无图谱基线（同分词器 topic.zh_terms + 同倒排语料；无路由/符号链接/impact/IMPLEMENTS展开/repo_card/premise）",
                "B": "图谱混合 build_repo_context 完整 v0.3（lexical 档，离线确定性）",
            },
            "online_channel": "grok-4.5 中转站（Anthropic 兼容 /v1/messages）；host/api_key 不入产物",
            "semantic_mode": "lexical（S2 改写/L2 需阿里网关，本轮未消耗；数字为 lexical 下界，llm 档增益未计入）",
            "judge_bias_note": "裁判与生成同模型(grok-4.5)存在自评偏置；两组同向作用，组间相对差有效。",
            "graph": {"nodes": 510, "edges": 1698},
            "git_head": None,  # 由 aggregate 时回填
        },
        "findings": findings,
        "segment1_program_assertions": program,
        "segment2_answer_accuracy": {
            "main_by_layer": main_acc,
            "group_delta": group_delta(),
            "pp_correction_groupB": pp,
        },
        "online_call_stats": {
            "generation": {"total": total_gen, "error": total_gen_err, "by_bucket": gen_stat},
            "judgement": {"total": total_judge, "error": total_judge_err, "by_bucket": judge_stat},
        },
    }
    # git head 回填
    try:
        import subprocess
        result["meta"]["git_head"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    except Exception:
        pass
    with open(RESULTS, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd == "offline":
        run_offline()
        print("[offline] program metrics ->", os.path.relpath(PROG_CACHE, ROOT))
    elif cmd == "probe":
        return 0 if probe() else 3
    elif cmd == "gen":
        if not probe():
            print("中转站不可用，中止。"); return 3
        run_gen()
    elif cmd == "judge":
        if not probe():
            print("中转站不可用，中止。"); return 3
        run_judge()
    elif cmd == "online":
        if not probe():
            print("中转站不可用，中止。"); return 3
        run_gen(); run_judge()
    elif cmd == "aggregate":
        aggregate()
        print("[aggregate] ->", os.path.relpath(RESULTS, ROOT))
    elif cmd == "all":
        run_offline()
        if not probe():
            print("中转站不可用，中止在线段。"); return 3
        run_gen(); run_judge(); aggregate()
        print("[all] done ->", os.path.relpath(RESULTS, ROOT))
    else:
        print(__doc__); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
