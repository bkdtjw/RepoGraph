# -*- coding: utf-8 -*-
"""D1 主评测集 gold 独立核实脚本（对真实图谱，全题 0 失败方可入库）。

与 d1_build.py 解耦：本脚本自建原始邻接（直接读 output/graph.json 的 edges），
逐题按 subtype 用**直接边查询**独立重算 gold，断言与 dataset_main.jsonl 内已写
gold_entities 完全一致；并叠加：
  - 存在性：全部 gold + subject 实体必须是图谱真实节点；
  - 泄漏：非 subject 的 gold 可识别名不得出现在题面；
  - 交叉校验：depth=3 反向 CALLS 闭包大小 == 该函数 blast_radius 属性（metrics.py 出品）；
  - 调用链：gold_paths 每条相邻边必须是真实 CALLS 边，gold == 链中间节点；
  - 分布：L1/L2/L3 = 20/30/10；层内 dev/test 各半；难度/split 字段合法。
任何一项不符即 FAIL 并退出码 1。

运行：python design_work/d1_goldcheck.py
"""
from __future__ import annotations
import json, os, sys, collections

ROOT = os.path.join(os.path.dirname(__file__), "..")
GRAPH = os.path.join(ROOT, "output", "graph.json")
DATA = os.path.join(ROOT, "eval", "dataset_main.jsonl")


def load_graph():
    with open(GRAPH, encoding="utf-8") as f:
        g = json.load(f)
    N = {n["id"]: n for n in g["nodes"]}
    A = {
        "calls_fwd": collections.defaultdict(set),
        "calls_rev": collections.defaultdict(set),
        "contains_parent": collections.defaultdict(list),
        "impl_fwd": collections.defaultdict(set),
        "impl_rev": collections.defaultdict(set),
        "mod_fwd": collections.defaultdict(set),
        "mod_rev": collections.defaultdict(set),
        "desc_rev": collections.defaultdict(set),
    }
    for e in g["edges"]:
        s, t, d = e["src"], e["type"], e["dst"]
        if t == "CALLS":
            A["calls_fwd"][s].add(d); A["calls_rev"][d].add(s)
        elif t == "CONTAINS":
            A["contains_parent"][d].append(s)
        elif t == "IMPLEMENTS":
            A["impl_fwd"][s].add(d); A["impl_rev"][d].add(s)
        elif t == "MODIFIES":
            A["mod_fwd"][s].add(d); A["mod_rev"][d].add(s)
        elif t == "DESCRIBES":
            A["desc_rev"][d].add(s)
    return N, A


def module_of(N, A, fid):
    mods = set()
    for p in A["contains_parent"].get(fid, ()):
        pn = N.get(p)
        if not pn:
            continue
        if pn["label"] == "Module":
            mods.add(p)
        elif pn["label"] == "Class":
            for gp in A["contains_parent"].get(p, ()):
                if N.get(gp, {}).get("label") == "Module":
                    mods.add(gp)
    return mods


def rev_closure(A, fid, depth):
    lv = {fid: 0}
    frontier = [fid]
    for d in range(1, depth + 1):
        nxt = []
        for x in frontier:
            for p in A["calls_rev"].get(x, ()):
                if p not in lv:
                    lv[p] = d
                    nxt.append(p)
        frontier = nxt
        if not frontier:
            break
    return lv


def distinctive(N, nid):
    n = N[nid]; lab = n["label"]; out = set()
    if lab == "Function":
        qn = n.get("qualname", ""); out.add(qn)
        leaf = qn.split(".")[-1]
        if leaf and leaf.lower() not in {"invoke", "__init__", "run", "main", "check"}:
            out.add(leaf)
    elif lab == "Concept":
        out.add(n.get("name", ""))
        out |= set(n.get("aliases") or [])
        out |= set(n.get("zh_aliases") or [])
    elif lab == "Commit":
        out.add(n["hash"][:12]); out.add(n["hash"])
    elif lab == "Module":
        parts = n.get("path", "").replace("\\", "/").split("/")
        if len(parts) >= 2:
            out.add("/".join(parts[-2:]))
    elif lab == "Class":
        out.add(n.get("qualname", n.get("name", "")))
    return {s for s in out if s and len(s) >= 3}


def expected_gold(N, A, row):
    """按 subtype 用直接边查询独立重算 gold（不依赖 build 的 derive）。"""
    st = row["subtype"]
    args = row["recipe"]["args"]
    # 解析 recipe 里的 fn/commit/concept 到 id（借助 subject/gold 已是 id，这里从 args 反解）
    def fn_id(qual):
        ids = [nid for nid, n in N.items()
               if n["label"] == "Function" and n.get("qualname") == qual]
        assert len(ids) == 1, f"qualname 非唯一: {qual} -> {ids}"
        return ids[0]

    def commit_id(pref):
        ids = [nid for nid, n in N.items()
               if n["label"] == "Commit" and n["hash"].startswith(pref)]
        assert len(ids) == 1, f"commit 前缀非唯一: {pref} -> {ids}"
        return ids[0]

    if st == "fn_module":
        return sorted(module_of(N, A, fn_id(args["fn"])))
    if st == "fn_callers":
        return sorted(A["calls_rev"].get(fn_id(args["fn"]), set()))
    if st == "concept_impl_fns":
        return sorted(x for x in A["impl_rev"].get(args["concept"], ())
                      if N[x]["label"] == "Function")
    if st == "commit_mod_fns":
        return sorted(A["mod_fwd"].get(commit_id(args["commit"]), set()))
    if st == "fn_concepts":
        return sorted(x for x in A["impl_fwd"].get(fn_id(args["fn"]), ())
                      if N[x]["label"] == "Concept")
    if st == "class_module":
        return sorted(p for p in A["contains_parent"].get(args["cls"], ())
                      if N.get(p, {}).get("label") == "Module")
    if st == "rev_calls_closure":
        lv = rev_closure(A, fn_id(args["fn"]), args["depth"])
        return sorted(k for k, v in lv.items() if v >= 1)
    if st == "impact_modules":
        lv = rev_closure(A, fn_id(args["fn"]), args["depth"])
        mods = set()
        for nid in lv:
            mods |= module_of(N, A, nid)
        return sorted(mods)
    if st == "calls_chain":
        path = [fn_id(q) for q in args["path_quals"]]
        return sorted(path[1:-1])
    if st == "concept_fns_commits":
        fns = [x for x in A["impl_rev"].get(args["concept"], ())
               if N[x]["label"] == "Function"]
        commits = set()
        for f in fns:
            commits |= A["mod_rev"].get(f, set())
        return sorted(commits)
    if st == "commit_fns_concepts":
        fns = A["mod_fwd"].get(commit_id(args["commit"]), set())
        cs = set()
        for f in fns:
            cs |= {x for x in A["impl_fwd"].get(f, ()) if N[x]["label"] == "Concept"}
        return sorted(cs)
    if st == "design_provenance":
        cid = args["concept"]
        commits = sorted(x for x in A["desc_rev"].get(cid, ())
                         if N[x]["label"] == "Commit")
        return [cid] + commits
    raise ValueError(f"未知 subtype {st}")


def main():
    N, A = load_graph()
    rows = [json.loads(l) for l in open(DATA, encoding="utf-8") if l.strip()]
    fails = []

    def fail(qid, msg):
        fails.append(f"{qid}: {msg}")

    for r in rows:
        qid = r["id"]
        # 1) 存在性
        for gid in r["gold_entities"]:
            if gid not in N:
                fail(qid, f"gold 实体不存在于图谱: {gid}")
        for sid in r.get("subject_entities", []):
            if sid not in N:
                fail(qid, f"subject 实体不存在于图谱: {sid}")
        # 2) gold 与图谱重算一致
        try:
            exp = expected_gold(N, A, r)
        except AssertionError as e:
            fail(qid, f"重算异常: {e}"); exp = None
        if exp is not None and exp != sorted(r["gold_entities"]):
            fail(qid, f"gold 与图谱重算不一致\n    stored={sorted(r['gold_entities'])}\n    graph ={exp}")
        # 3) 非空（L1/L2 至少 1 个答案实体；L3 至少概念+1提交）
        if r["layer"] in ("L1", "L2") and len(r["gold_entities"]) < 1:
            fail(qid, "gold 为空")
        if r["layer"] == "L3" and len(r["gold_entities"]) < 2:
            fail(qid, "L3 gold 应含概念+至少1溯源提交")
        # 4) 泄漏检查
        subj = set(r.get("subject_entities", []))
        for gid in r["gold_entities"]:
            if gid in subj:
                continue
            for s in distinctive(N, gid):
                if s in r["question"]:
                    fail(qid, f"题面泄漏 gold {gid} 的可识别名 {s!r}")
        # 5) calls_chain 路径真实性
        if r["subtype"] == "calls_chain":
            paths = r.get("gold_paths") or []
            if not paths:
                fail(qid, "calls_chain 缺 gold_paths")
            for path in paths:
                for a, b in zip(path, path[1:]):
                    if b not in A["calls_fwd"].get(a, ()):
                        fail(qid, f"调用链非真实边: {a} -> {b}")
                if sorted(path[1:-1]) != sorted(r["gold_entities"]):
                    fail(qid, "gold 与调用链中间节点不符")
        # 6) depth=3 闭包 == blast_radius 属性（metrics.py 交叉校验）
        if r["subtype"] == "rev_calls_closure" and r["recipe"]["args"]["depth"] == 3:
            fnq = r["recipe"]["args"]["fn"]
            fid = [nid for nid, n in N.items()
                   if n["label"] == "Function" and n.get("qualname") == fnq][0]
            br = N[fid].get("blast_radius")
            if br is not None and br != len(r["gold_entities"]):
                fail(qid, f"闭包大小 {len(r['gold_entities'])} != blast_radius 属性 {br}")

    # 7) 分布与 split
    from collections import Counter
    lc = Counter(r["layer"] for r in rows)
    if dict(lc) != {"L1": 20, "L2": 30, "L3": 10}:
        fails.append(f"分布错误: {dict(lc)}")
    for layer, tot in (("L1", 10), ("L2", 15), ("L3", 5)):
        for sp in ("dev", "test"):
            c = sum(1 for r in rows if r["layer"] == layer and r["split"] == sp)
            if c != tot:
                fails.append(f"{layer}/{sp} split 不均衡: {c} != {tot}")
    for r in rows:
        if r["split"] not in ("dev", "test"):
            fails.append(f"{r['id']}: split 非法 {r['split']}")
        if r["difficulty"] not in ("easy", "med", "hard"):
            fails.append(f"{r['id']}: 难度非法 {r['difficulty']}")
    if len({r["id"] for r in rows}) != len(rows):
        fails.append("存在重复 id")
    if len(rows) != 60:
        fails.append(f"题数 {len(rows)} != 60")

    print("=" * 60)
    if fails:
        print(f"[FAIL] {len(fails)} 项不通过：")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print(f"[PASS] 全 {len(rows)} 题 gold 对图谱核实通过（0 失败）")
    print("  分布 L1/L2/L3 =", dict(lc), "；层内 dev/test 各半")
    goldz = [(r["id"], r["gold_n"]) for r in rows]
    print("  gold_n: L1/L2 min-max =",
          min(r["gold_n"] for r in rows if r["layer"] != "L3"), "-",
          max(r["gold_n"] for r in rows if r["layer"] != "L3"))
    # depth3 闭包与 blast_radius 一致的题数
    bc = sum(1 for r in rows if r["subtype"] == "rev_calls_closure"
             and r["recipe"]["args"]["depth"] == 3)
    print(f"  depth3 闭包已与 blast_radius 属性交叉校验：{bc} 题")


if __name__ == "__main__":
    main()
