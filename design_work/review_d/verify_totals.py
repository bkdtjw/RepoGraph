# -*- coding: utf-8 -*-
"""Phase D Verify 收尾：逐条加总核对 + 组隔离核对 + DR-01 零影响证明。

只读 d2_runs 原始记录与 d2_results.json，不发起任何在线调用、不覆盖任何产物。
"""
from __future__ import annotations
import json, os, sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
ROOT = os.path.abspath(ROOT)
EVAL = os.path.join(ROOT, "eval")
RUNS = os.path.join(EVAL, "d2_runs")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, EVAL)

from repograph.models import GraphStore
import run_d2  # 复用 entity_recall_token / _has_identifier_token / context_has_entity

def jl(p):
    with open(p, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]

RES = json.load(open(os.path.join(EVAL, "d2_results.json"), encoding="utf-8"))
main_rows = {r["id"]: r for r in jl(os.path.join(EVAL, "dataset_main.jsonl"))}

def rate(n, d):
    return round(n / d, 4) if d else 0.0

problems = []
def check(name, got, exp):
    ok = got == exp
    if not ok:
        problems.append(f"{name}: got={got} exp={exp}")
    print(f"  [{'OK ' if ok else 'MISMATCH'}] {name}: got={got} exp={exp}")

# ============ 1) 记录条数与组隔离 ============
print("=" * 70)
print("1) 记录条数 + 组隔离（gen 输入 mode）")
GRAPH_MODES = {"symbol", "topic", "llm", "overview", "global", "out_of_scope",
               "structural", "entity_local", "meta"}
files = {
    "gen_main_A": jl(os.path.join(RUNS, "gen_main_A.jsonl")),
    "gen_main_B": jl(os.path.join(RUNS, "gen_main_B.jsonl")),
    "gen_pp_B":   jl(os.path.join(RUNS, "gen_pp_B.jsonl")),
    "judge_main_A": jl(os.path.join(RUNS, "judge_main_A.jsonl")),
    "judge_main_B": jl(os.path.join(RUNS, "judge_main_B.jsonl")),
    "judge_pp_B":   jl(os.path.join(RUNS, "judge_pp_B.jsonl")),
    "ctx_main_A": jl(os.path.join(RUNS, "ctx_main_A.jsonl")),
    "ctx_main_B": jl(os.path.join(RUNS, "ctx_main_B.jsonl")),
}
for k, v in files.items():
    ids = [r["id"] for r in v]
    print(f"  {k}: n={len(v)}  唯一 id={len(set(ids))}")

# 组 A 生成输入必须全部 bm25_only；组 B 必须全部图谱模式
a_modes = {r["mode"] for r in files["gen_main_A"]}
b_modes = {r["mode"] for r in files["gen_main_B"]}
print("  组 A gen modes =", a_modes)
print("  组 B gen modes =", b_modes)
check("组A生成输入全为bm25_only", a_modes == {"bm25_only"}, True)
check("组A无任何图谱模式泄漏", a_modes & GRAPH_MODES, set())
check("组B生成输入全为图谱模式(无bm25_only)", "bm25_only" not in b_modes, True)
# ctx 快照 mode 与 gen mode 必须逐题一致（生成输入即对应组上下文）
gmA = {r["id"]: r["mode"] for r in files["gen_main_A"]}
cmA = {r["id"]: r["mode"] for r in files["ctx_main_A"]}
gmB = {r["id"]: r["mode"] for r in files["gen_main_B"]}
cmB = {r["id"]: r["mode"] for r in files["ctx_main_B"]}
check("组A gen.mode == ctx.mode 逐题一致", gmA == cmA, True)
check("组B gen.mode == ctx.mode 逐题一致", gmB == cmB, True)

# ============ 2) 逐条加总重算 segment2，比对 d2_results ============
print("=" * 70)
print("2) segment2 答案准确率：从 judge 原始记录逐条加总重算")

def agg_layer(recs):
    out = {}
    for layer in ("L1", "L2", "L3"):
        items = [r for r in recs if main_rows.get(r["id"], {}).get("layer") == layer]
        valid = [r for r in items if r.get("ok") and r.get("verdict")]
        c = sum(1 for r in valid if r["verdict"] == "correct")
        p = sum(1 for r in valid if r["verdict"] == "partial")
        w = sum(1 for r in valid if r["verdict"] == "wrong")
        n = len(valid)
        out[layer] = {
            "n_judged": n, "correct": c, "partial": p, "wrong": w,
            "correct_rate": rate(c, n), "correct_or_partial_rate": rate(c + p, n),
            "wrong_ids": sorted(r["id"] for r in valid if r["verdict"] == "wrong"),
            "error_ids": sorted(r["id"] for r in items if not (r.get("ok") and r.get("verdict"))),
        }
    return out

for grp, recs in (("A", files["judge_main_A"]), ("B", files["judge_main_B"])):
    got = agg_layer(recs)
    exp = RES["segment2_answer_accuracy"]["main_by_layer"][grp]
    for layer in ("L1", "L2", "L3"):
        check(f"{grp}/{layer} c/p/w+rate+wrong_ids",
              (got[layer]["correct"], got[layer]["partial"], got[layer]["wrong"],
               got[layer]["correct_rate"], got[layer]["correct_or_partial_rate"],
               got[layer]["wrong_ids"]),
              (exp[layer]["correct"], exp[layer]["partial"], exp[layer]["wrong"],
               exp[layer]["correct_rate"], exp[layer]["correct_or_partial_rate"],
               exp[layer]["wrong_ids"]))
        # 自洽：c+p+w == n_judged
        check(f"{grp}/{layer} c+p+w==n_judged",
              got[layer]["correct"] + got[layer]["partial"] + got[layer]["wrong"],
              got[layer]["n_judged"])

# PP
ppv = [r for r in files["judge_pp_B"] if r.get("ok") and r.get("verdict")]
ppc = sum(1 for r in ppv if r["verdict"] == "correct")
ppp = sum(1 for r in ppv if r["verdict"] == "partial")
ppw = sum(1 for r in ppv if r["verdict"] == "wrong")
exp_pp = RES["segment2_answer_accuracy"]["pp_correction_groupB"]
check("PP correct/partial/wrong", (ppc, ppp, ppw), (exp_pp["correct"], exp_pp["partial"], exp_pp["wrong"]))
check("PP n_judged", len(ppv), exp_pp["n_judged"])
check("PP correction_rate", rate(ppc, len(ppv)), exp_pp["correction_rate"])

# ============ 3) online_call_stats 加总 ============
print("=" * 70)
print("3) online_call_stats：total == Σ buckets，error 计数")
gen_total = len(files["gen_main_A"]) + len(files["gen_main_B"]) + len(files["gen_pp_B"])
gen_err = sum(1 for k in ("gen_main_A", "gen_main_B", "gen_pp_B") for r in files[k] if not r.get("ok"))
jud_total = len(files["judge_main_A"]) + len(files["judge_main_B"]) + len(files["judge_pp_B"])
jud_err = sum(1 for k in ("judge_main_A", "judge_main_B", "judge_pp_B")
              for r in files[k] if not (r.get("ok") and r.get("verdict")))
check("gen total", gen_total, RES["online_call_stats"]["generation"]["total"])
check("gen error", gen_err, RES["online_call_stats"]["generation"]["error"])
check("judge total", jud_total, RES["online_call_stats"]["judgement"]["total"])
check("judge error", jud_err, RES["online_call_stats"]["judgement"]["error"])

# ============ 4) DR-01 零影响证明：旧口径(纯子串) vs 新口径(Fn/Class 词边界) ============
print("=" * 70)
print("4) DR-01 零影响证明：ctx 快照上 old(纯子串) vs new(词边界) 命中集逐题比对")
store = GraphStore.load(os.path.join(ROOT, "output", "graph.json"))

def old_has_entity(ct, eid):
    tok = run_d2.entity_recall_token(store, eid)
    return bool(tok) and tok in ct

def new_has_entity(ct, eid):
    return run_d2.context_has_entity(store, ct, eid)

diff_cells = 0
leaf_lens = []
for grp, ctxs in (("A", files["ctx_main_A"]), ("B", files["ctx_main_B"])):
    ctxmap = {r["id"]: r["context_text"] or "" for r in ctxs}
    for rid, row in main_rows.items():
        ct = ctxmap.get(rid, "")
        for eid in row["gold_entities"]:
            o = old_has_entity(ct, eid)
            n = new_has_entity(ct, eid)
            if o != n:
                diff_cells += 1
                print(f"    DIFF {grp}/{rid} {eid}: old={o} new={n}")
check("old==new 命中集逐格 byte-identical（zero diff cells）", diff_cells, 0)

# 重算 segment1 layer_summary（新口径），比对 d2_results 存档（旧口径生成）
def layer_summary(ctxs, grp):
    detail = []
    for r in ctxs:
        rid = r["id"]; row = main_rows[rid]; ct = r["context_text"] or ""
        gold = row["gold_entities"]
        hit = [e for e in gold if new_has_entity(ct, e)]
        detail.append({"id": rid, "layer": row["layer"],
                       "recall": rate(len(hit), len(gold)), "hit1": len(hit) >= 1})
    out = {}
    for layer in ("L1", "L2", "L3"):
        items = [r for r in detail if r["layer"] == layer]
        hit1 = [r for r in items if r["hit1"]]
        out[layer] = {
            "context_hit@min1_rate": rate(len(hit1), len(items)),
            "mean_gold_recall": round(sum(r["recall"] for r in items) / len(items), 4),
        }
    return out

for grp, ctxs in (("A", files["ctx_main_A"]), ("B", files["ctx_main_B"])):
    got = layer_summary(ctxs, grp)
    exp = RES["segment1_program_assertions"]["main_program_assertions"]["summary"][grp]
    for layer in ("L1", "L2", "L3"):
        check(f"segment1 {grp}/{layer} hit@min1+mean_recall(新口径==存档)",
              (got[layer]["context_hit@min1_rate"], got[layer]["mean_gold_recall"]),
              (exp[layer]["context_hit@min1_rate"], exp[layer]["mean_gold_recall"]))

# ============ 5) gold 叶名长度审计（佐证 DR-01 注释 ≥6 字符 + Concept 名长度）============
print("=" * 70)
print("5) gold 实体锚字符串长度审计")
fnclass_tokens = []
concept_tokens = []
for rid, row in main_rows.items():
    for eid in row["gold_entities"]:
        node = store.get_node(eid)
        if node is None:
            continue
        lab = node.get("label", "")
        tok = run_d2.entity_recall_token(store, eid)
        if lab in ("Function", "Class"):
            fnclass_tokens.append((rid, tok, len(tok)))
        elif lab == "Concept":
            concept_tokens.append((rid, tok, len(tok)))
min_fn = min(t[2] for t in fnclass_tokens) if fnclass_tokens else None
print(f"  Fn/Class gold 锚 token 数={len(fnclass_tokens)}  最短叶名长度={min_fn}")
print(f"    最短几例：", sorted(fnclass_tokens, key=lambda x: x[2])[:6])
check("全部 Fn/Class 叶名 ≥6 字符", min_fn is not None and min_fn >= 6, True)
min_con = min(t[2] for t in concept_tokens) if concept_tokens else None
print(f"  Concept gold 锚 token 数={len(concept_tokens)}  最短概念名长度={min_con}")
print(f"    最短几例：", sorted(concept_tokens, key=lambda x: x[2])[:6])

# ============ 6) meta.graph 硬编码核对（Round2-H）============
print("=" * 70)
print("6) meta.graph 硬编码 vs 真实图谱")
g = json.load(open(os.path.join(ROOT, "output", "graph.json"), encoding="utf-8"))
real_nodes, real_edges = len(g["nodes"]), len(g["edges"])
meta_graph = RES["meta"]["graph"]
print(f"  真实图谱 nodes={real_nodes} edges={real_edges}；d2_results.meta={meta_graph}")
check("meta.graph.nodes 与真实一致", meta_graph["nodes"], real_nodes)
check("meta.graph.edges 与真实一致", meta_graph["edges"], real_edges)

print("=" * 70)
if problems:
    print(f"[结论] 发现 {len(problems)} 处不一致：")
    for p in problems:
        print("  -", p)
    sys.exit(1)
print("[结论] 全部核对通过：加总一致 / 组隔离干净 / DR-01 零影响 / 叶名≥6")
