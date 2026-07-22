# -*- coding: utf-8 -*-
"""RepoGraph v0.3 门禁脚本（Phase A · 任务 A4）——48 题全量 + 硬指标 + 锁定失败回归。

只**消费** src/repograph 的现有检索函数，绝不修改任何 src 源码；纯标准库。
离线逐题跑 ``build_repo_context``（不经网关、不调 LLM——语义档按 lexical 语义评估），
分子集断言，产出 eval/gate_report.json，并在控制台打印红绿摘要。

判定契约来自 design_work/eval-design.md §7；字段映射见其 §1：
  - v1 入口 build_repo_context 返回 {mode, linked, context_text, stats}
  - anchors = linked[].entity_id (symbol 模式) / linked[].node_id (topic 模式)
  - 当前实现**不存在** needs_disambiguation / premise_flags 字段（AMB/PP 按缺失处理）

运行（Windows 无 make 亦可）：
    cd C:/Users/nirvana/Desktop/代码库知识图谱 && python eval/gate.py
退出码：锁定失败 B-1/B-2/B-3 在 Phase A 预期全红，不作为进程失败依据（返回 0）；
仅当脚本无法加载图谱/数据集或代码入口缺失时返回非 0。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
GRAPH = os.path.join(ROOT, "output", "graph.json")
DATASET = os.path.join(ROOT, "eval", "dataset.jsonl")
REPORT = os.path.join(ROOT, "eval", "gate_report.json")
EXPECTED_TAG = "rebaseline-20260723"

if SRC not in sys.path:
    sys.path.insert(0, SRC)

# 只 import，不修改（参考 tests/test_context.py 的加载方式）
from repograph.models import GraphStore                       # noqa: E402
from repograph.retrieve.context import build_repo_context     # noqa: E402


# ---------------------------------------------------------------------------
# L0 规范事实表（抄自 eval-design.md §2 / §7.1 的真实值）
# ---------------------------------------------------------------------------

L0_FACTS_NUM = ["22", "15", "259", "75", "139"]
L0_FACTS_STR = [
    "multi-agent-orch",
    "_dispatch_group", "run_thread", "render_view", "_handle_terminate",
    "适配层", "用户界面 CLI 子集", "render四层视图组装", "stdlib网关15端点",
    "cli/main.py", "scheduler/core.py",
]

DELTA_SCORE = 20  # eval-design §4：整数分差消歧阈值（v1 代理用）


# ---------------------------------------------------------------------------
# 字段映射工具（eval-design §7.0）
# ---------------------------------------------------------------------------

def anchors_of(resp: dict) -> list:
    """v1: linked[].entity_id / node_id → 有序 id 列表。"""
    out = []
    for a in resp.get("anchors") or resp.get("linked") or []:
        out.append(a.get("id") or a.get("entity_id") or a.get("node_id"))
    return [x for x in out if x]


def mode_class(mode) -> str:
    if mode in ("meta", "overview", "global"):
        return "overview"
    if mode in ("symbol", "entity_local"):
        return "symbol"
    if mode in ("topic", "llm"):
        return "topic"
    return mode  # none / structural / out_of_scope


# ---------------------------------------------------------------------------
# 子集判定器
# ---------------------------------------------------------------------------

def judge_l0(resp: dict) -> dict:
    text = (resp.get("answer") or "") + "\n" + (resp.get("context_text") or "")
    hits = sum(1 for s in L0_FACTS_STR if s in text)
    hits += sum(1 for n in L0_FACTS_NUM if re.search(r"(?<!\d)" + n + r"(?!\d)", text))
    ok_mode = mode_class(resp.get("mode")) == "overview"
    return {"pass": ok_mode and hits >= 3, "mode_ok": ok_mode, "facts_hit": hits}


def gold_equiv_ids(gold_id: str, edges: list) -> set:
    """gold 及其 IMPLEMENTS/DESCRIBES 1 跳邻居（双向）——eval-design §7.2。"""
    eq = {gold_id}
    for e in edges:
        if e["type"] in ("IMPLEMENTS", "DESCRIBES"):
            if e["dst"] == gold_id:
                eq.add(e["src"])   # 概念 ← 实现/提交
            if e["src"] == gold_id:
                eq.add(e["dst"])   # 实现 → 概念
    return eq


def judge_fz(resp: dict, gold_id: str, edges: list) -> dict:
    eq = gold_equiv_ids(gold_id, edges)
    anc = anchors_of(resp)
    return {
        "hit@1": bool(eq & set(anc[:1])),
        "hit@3": bool(eq & set(anc[:3])),
        "anchors_top3": anc[:3],
        "gold_equiv_size": len(eq),
    }


def judge_amb(resp: dict, gold_behavior: str) -> dict:
    """当前实现无 needs_disambiguation 字段 → 一律按「预测自选」评行为一致率。

    - predicted = should_autopick（系统无消歧能力，只会取 top 锚定或回落）
    - should_autopick 一致 ⇔ 确实锚定了实体（linked 非空 且 mode 为 symbol 类）
    - should_disambiguate 一致 ⇔ 系统消歧（当前恒 False → 必不一致 = 漏问）
    过问率/漏问率随附产出（Phase C4 前不作硬门禁）。
    """
    has_nd_field = "needs_disambiguation" in resp
    nd = bool(resp.get("needs_disambiguation")) if has_nd_field else False
    anc = anchors_of(resp)
    anchored = bool(anc) and mode_class(resp.get("mode")) == "symbol"
    predicted = "should_autopick"  # 字段缺失下的确定性预测
    if gold_behavior == "should_disambiguate":
        consistent = (predicted == "should_disambiguate")  # 恒 False
        return {"pass": consistent, "predicted": predicted,
                "over_ask": False, "under_ask": True, "anchored": anchored,
                "has_nd_field": has_nd_field}
    else:  # should_autopick
        consistent = (predicted == "should_autopick") and anchored
        return {"pass": consistent, "predicted": predicted,
                "over_ask": bool(nd), "under_ask": False, "anchored": anchored,
                "has_nd_field": has_nd_field}


_NEG_WORDS = ["没有", "未使用", "不是", "并非", "实际上", "无", "而非"]


def judge_pp(resp: dict, row: dict) -> dict:
    """PP：当前实现无 premise_flags → 按缺失判 B-3 红。

    离线只能看注入体：记录 mode、错误前提关键词是否泄漏进上下文（premise_leak，
    图中本无这些技术名，预期 0）、以及轻量纠正筛查（否定词+真值锚，预期均不触发，
    因为系统给的是沉默概览而非主动纠错）。
    """
    text = (resp.get("answer") or "") + "\n" + (resp.get("context_text") or "")
    low = text.lower()
    has_pf_field = "premise_flags" in resp
    leak = any(k.lower() in low for k in row.get("absent_keywords", []))
    neg = any(w in text for w in _NEG_WORDS)
    truths = row.get("truth_anchors", [])
    truth_present = any(t.lower() in low for t in truths) or not truths
    corrected = neg and truth_present  # §7.4 screen_correction 疑似已纠正
    return {"has_premise_flags_field": has_pf_field,
            "premise_leak": leak, "suspect_corrected": corrected,
            "mode": resp.get("mode")}


def is_bare_refusal(resp: dict) -> bool:
    """裸拒：mode=none 且无任何可行动上下文（eval-design §7.5 收紧到 v1 现状）。"""
    has_action = bool(resp.get("suggestions") or resp.get("candidates")
                      or anchors_of(resp) or (resp.get("context_text") or "").strip())
    return not has_action


# ---------------------------------------------------------------------------
# git 绑定
# ---------------------------------------------------------------------------

def git_info() -> dict:
    def _run(args):
        try:
            return subprocess.check_output(
                ["git"] + args, cwd=ROOT, stderr=subprocess.DEVNULL
            ).decode("utf-8", "replace").strip()
        except Exception:
            return ""
    head = _run(["rev-parse", "HEAD"])
    tags_here = _run(["tag", "--points-at", "HEAD"]).split()
    return {
        "head": head,
        "tags_at_head": tags_here,
        "expected_tag": EXPECTED_TAG,
        "tag_bound": EXPECTED_TAG in tags_here,
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def load_dataset() -> list:
    rows = []
    with open(DATASET, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def rate(n: int, d: int) -> float:
    return round(n / d, 4) if d else 0.0


def main() -> int:
    if not os.path.exists(GRAPH):
        print(f"[FATAL] 缺少图谱 {GRAPH}", file=sys.stderr)
        return 2
    if not os.path.exists(DATASET):
        print(f"[FATAL] 缺少数据集 {DATASET}", file=sys.stderr)
        return 2

    store = GraphStore.load(GRAPH)
    g = json.load(open(GRAPH, encoding="utf-8"))
    edges = g["edges"]
    rows = load_dataset()

    per_q = []
    for row in rows:
        subset = row["subset"]
        q = row["question"]
        ctx = build_repo_context(store, q)   # 离线四层瀑布，不碰网关/LLM
        rec = {
            "id": row["id"], "subset": subset, "question": q,
            "mode": ctx.get("mode"), "mode_class": mode_class(ctx.get("mode")),
            "n_linked": len(ctx.get("linked") or []),
            "bare_refusal": is_bare_refusal(ctx),
        }
        if subset == "L0":
            rec["judge"] = judge_l0(ctx)
        elif subset in ("FZ_dev", "FZ_test"):
            rec["judge"] = judge_fz(ctx, row["gold_entity"], edges)
            rec["gold_entity"] = row["gold_entity"]
        elif subset == "AMB":
            rec["judge"] = judge_amb(ctx, row["gold_behavior"])
            rec["gold_behavior"] = row["gold_behavior"]
        elif subset == "PP":
            rec["judge"] = judge_pp(ctx, row)
        per_q.append(rec)

    def sub(name):
        return [r for r in per_q if r["subset"] == name]

    # ---- 子集汇总 ----
    l0 = sub("L0")
    l0_pass = [r for r in l0 if r["judge"]["pass"]]
    fzd = sub("FZ_dev")
    fzt = sub("FZ_test")
    amb = sub("AMB")
    pp = sub("PP")

    def fz_summary(items):
        h1 = [r for r in items if r["judge"]["hit@1"]]
        h3 = [r for r in items if r["judge"]["hit@3"]]
        return {"n": len(items), "hit@1": rate(len(h1), len(items)),
                "hit@3": rate(len(h3), len(items)),
                "hit@1_n": len(h1), "hit@3_n": len(h3)}

    amb_consistent = [r for r in amb if r["judge"]["pass"]]
    amb_autopick = [r for r in amb if r["gold_behavior"] == "should_autopick"]
    amb_disamb = [r for r in amb if r["gold_behavior"] == "should_disambiguate"]
    over_ask_n = sum(1 for r in amb_autopick if r["judge"]["over_ask"])
    under_ask_n = sum(1 for r in amb_disamb if r["judge"]["under_ask"])

    pp_corrected = [r for r in pp if r["judge"]["suspect_corrected"]]
    pp_leak = [r for r in pp if r["judge"]["premise_leak"]]
    pp_has_pf = any(r["judge"]["has_premise_flags_field"] for r in pp)

    bare_n = sum(1 for r in per_q if r["bare_refusal"])

    subset_summary = {
        "L0": {"n": len(l0), "pass_rate": rate(len(l0_pass), len(l0)),
               "pass_n": len(l0_pass),
               "fail_ids": [r["id"] for r in l0 if not r["judge"]["pass"]]},
        "FZ_dev": fz_summary(fzd),
        "FZ_test": fz_summary(fzt),
        "AMB": {"n": len(amb),
                "behavior_consistency_rate": rate(len(amb_consistent), len(amb)),
                "consistent_n": len(amb_consistent),
                "over_ask_rate": rate(over_ask_n, len(amb_autopick)),
                "under_ask_rate": rate(under_ask_n, len(amb_disamb)),
                "consistent_ids": [r["id"] for r in amb_consistent]},
        "PP": {"n": len(pp),
               "correction_rate": rate(len(pp_corrected), len(pp)),
               "premise_leak_rate": rate(len(pp_leak), len(pp)),
               "premise_flags_capability": pp_has_pf},
    }

    # ---- 路由准确率（48 题标注 gold_mode_class）----
    route_ok = 0
    route_detail = []
    for r, row in zip(per_q, rows):
        gold_mc = row.get("gold_mode_class")
        ok = (r["mode_class"] == gold_mc)
        route_ok += int(ok)
        if not ok:
            route_detail.append({"id": r["id"], "got": r["mode_class"], "want": gold_mc})
    route_acc = rate(route_ok, len(per_q))

    # ---- 锁定失败 B-1 / B-2 / B-3 ----
    l0_02 = next(r for r in l0 if r["id"] == "L0-02")
    b1_red = (l0_02["mode_class"] != "overview")   # 误路由 = 红
    fzd_hit3 = subset_summary["FZ_dev"]["hit@3"]
    b2_red = (fzd_hit3 < 0.8)
    b3_red = (not pp_has_pf)                        # premise_flags 能力缺失 = 红

    locked = {
        "B-1": {"desc": "L0-02 口语元问题误路由至 topic",
                "expected": "red",
                "actual_mode": l0_02["mode"], "actual_mode_class": l0_02["mode_class"],
                "is_red": b1_red},
        "B-2": {"desc": "FZ-dev hit@3 < 0.8",
                "expected": "red", "fz_dev_hit@3": fzd_hit3, "threshold": 0.8,
                "is_red": b2_red},
        "B-3": {"desc": "premise_flags 前提校验能力缺失",
                "expected": "red", "premise_flags_capability": pp_has_pf,
                "is_red": b3_red},
    }

    # ---- 硬指标表（计划书 §5）逐项 PASS/FAIL/PENDING ----
    hard = {
        "裸拒率": {"threshold": "= 0", "effective": "Phase C1",
                 "measured": rate(bare_n, len(per_q)), "measured_n": bare_n,
                 "status": "PASS" if bare_n == 0 else "FAIL"},
        "预设幻觉率(PP顺预设作答)": {"threshold": "= 0", "effective": "Phase C3",
                 "measured_proxy_leak_rate": subset_summary["PP"]["premise_leak_rate"],
                 "status": "PENDING",
                 "note": "需生成层(LLM answer)方能终判；离线注入体仅给泄漏代理；B-3 能力缺失"},
        "锁定失败 B-1/B-2/B-3": {"threshold": "修复后不得回退", "effective": "各自翻绿起",
                 "status": "RED(baseline)",
                 "b1_red": b1_red, "b2_red": b2_red, "b3_red": b3_red},
        "主集三层准确率": {"threshold": "较 v0.1 下降 ≤3pt", "effective": "Phase D",
                 "status": "PENDING", "note": "主集 V4 未落地，无对照"},
        "路由准确率(48题标注)": {"threshold": "≥ 0.9", "effective": "Phase C1",
                 "measured": route_acc, "status": "PENDING",
                 "would_pass": route_acc >= 0.9, "mismatches": route_detail},
        "过问率": {"threshold": "≤ 0.2", "effective": "Phase C4",
                 "measured": subset_summary["AMB"]["over_ask_rate"], "status": "PENDING"},
        "漏问率": {"threshold": "≤ 0.1", "effective": "Phase C4",
                 "measured": subset_summary["AMB"]["under_ask_rate"], "status": "PENDING"},
    }

    report = {
        "meta": {
            "phase": "A (治理与再基线) · 任务 A4 门禁基线",
            "git": git_info(),
            "graph": {"path": os.path.relpath(GRAPH, ROOT),
                      "nodes": len(g["nodes"]), "edges": len(edges)},
            "dataset": {"path": os.path.relpath(DATASET, ROOT), "n": len(rows)},
            "entry": "repograph.retrieve.context.build_repo_context (离线,不经网关/LLM)",
        },
        "subset_summary": subset_summary,
        "locked_failures": locked,
        "hard_metrics": hard,
        "per_question": per_q,
    }

    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    _print_console(report)
    return 0


def _c(flag_red: bool) -> str:
    return "红 RED" if flag_red else "绿 GREEN"


def _print_console(report: dict) -> None:
    ss = report["subset_summary"]
    lk = report["locked_failures"]
    hm = report["hard_metrics"]
    gi = report["meta"]["git"]
    line = "=" * 68
    print(line)
    print("RepoGraph v0.3 门禁 · Phase A 再基线（48 题离线）")
    print(f"HEAD={gi['head'][:12]}  tag={','.join(gi['tags_at_head']) or '(none)'}  "
          f"tag_bound={gi['tag_bound']}")
    print(line)
    print("[子集通过率]")
    print(f"  L0     : pass {ss['L0']['pass_n']}/{ss['L0']['n']} "
          f"= {ss['L0']['pass_rate']}   fail={ss['L0']['fail_ids']}")
    print(f"  FZ_dev : hit@1={ss['FZ_dev']['hit@1']}  hit@3={ss['FZ_dev']['hit@3']} "
          f"({ss['FZ_dev']['hit@3_n']}/{ss['FZ_dev']['n']})")
    print(f"  FZ_test: hit@1={ss['FZ_test']['hit@1']}  hit@3={ss['FZ_test']['hit@3']} "
          f"({ss['FZ_test']['hit@3_n']}/{ss['FZ_test']['n']})")
    print(f"  AMB    : 行为一致率={ss['AMB']['behavior_consistency_rate']} "
          f"过问率={ss['AMB']['over_ask_rate']} 漏问率={ss['AMB']['under_ask_rate']}")
    print(f"  PP     : 纠正率={ss['PP']['correction_rate']} "
          f"泄漏率={ss['PP']['premise_leak_rate']} "
          f"premise_flags能力={ss['PP']['premise_flags_capability']}")
    print(line)
    print("[锁定失败 B-1/B-2/B-3]（Phase A 预期全红）")
    for k in ("B-1", "B-2", "B-3"):
        print(f"  {k}: {_c(lk[k]['is_red'])}  — {lk[k]['desc']}")
    print(line)
    print("[硬指标表 §5]")
    for k, v in hm.items():
        extra = v.get("measured", v.get("measured_proxy_leak_rate", ""))
        extra = f" measured={extra}" if extra != "" else ""
        print(f"  {v['status']:>13} | {k} (阈值 {v['threshold']}, 生效 {v['effective']}){extra}")
    print(line)
    br = hm["裸拒率"]
    print(f"裸拒率 = {br['measured']} → {br['status']}   "
          f"路由准确率 = {hm['路由准确率(48题标注)']['measured']}")
    print(f"报告已写出: {os.path.relpath(REPORT, ROOT)}")
    print(line)


if __name__ == "__main__":
    sys.exit(main())
