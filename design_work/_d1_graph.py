# -*- coding: utf-8 -*-
"""D1 主评测集共享图谱工具 —— 确定性 gold 派生配方（recipe）。

唯一事实源：output/graph.json（真实图谱，510 节点 / 1698 边）。
build 与 goldcheck 均 import 本模块，保证 gold 从图谱同一口径派生、可复现、零臆造。

边方向（已核实）：
  CONTAINS  Module->{Class,Function} / Class->Function
  IMPORTS   Module->Module
  CALLS     Function->Function（caller->callee）
  MODIFIES  Commit->Function
  DESCRIBES Commit->Concept
  IMPLEMENTS Function->Concept / Module->Concept
L2 影响面口径与 src/repograph/retrieve/impact.py 一致：反向 CALLS 分层 BFS。
"""
from __future__ import annotations
import json, os, collections

GRAPH_PATH = os.path.join(os.path.dirname(__file__), "..", "output", "graph.json")


class Graph:
    def __init__(self, path=GRAPH_PATH):
        with open(path, encoding="utf-8") as f:
            g = json.load(f)
        self.N = {n["id"]: n for n in g["nodes"]}
        self.edges = g["edges"]
        self.calls_fwd = collections.defaultdict(set)   # caller->callees
        self.calls_rev = collections.defaultdict(set)   # callee->callers
        self.contains_parent = collections.defaultdict(list)  # child->[parents]
        self.impl_fwd = collections.defaultdict(set)    # fn/mod->concepts
        self.impl_rev = collections.defaultdict(set)    # concept->fn/mod
        self.mod_fwd = collections.defaultdict(set)     # commit->functions
        self.mod_rev = collections.defaultdict(set)     # function->commits
        self.desc_rev = collections.defaultdict(set)    # concept->commits
        for e in self.edges:
            s, t, d = e["src"], e["type"], e["dst"]
            if t == "CALLS":
                self.calls_fwd[s].add(d); self.calls_rev[d].add(s)
            elif t == "CONTAINS":
                self.contains_parent[d].append(s)
            elif t == "IMPLEMENTS":
                self.impl_fwd[s].add(d); self.impl_rev[d].add(s)
            elif t == "MODIFIES":
                self.mod_fwd[s].add(d); self.mod_rev[d].add(s)
            elif t == "DESCRIBES":
                self.desc_rev[d].add(s)
        # qualname -> [function ids]
        self.qual2id = collections.defaultdict(list)
        for n in g["nodes"]:
            if n["label"] == "Function":
                self.qual2id[n.get("qualname", "")].append(n["id"])
        # hash prefix -> commit id
        self.hash2id = {}
        for n in g["nodes"]:
            if n["label"] == "Commit":
                self.hash2id[n["hash"]] = n["id"]

    # ---- resolvers (fail loudly on ambiguity) ----
    def fn(self, qualname):
        ids = self.qual2id.get(qualname, [])
        if len(ids) != 1:
            raise KeyError(f"function qualname 非唯一/缺失: {qualname!r} -> {ids}")
        return ids[0]

    def commit(self, hash_prefix):
        hits = [cid for h, cid in self.hash2id.items() if h.startswith(hash_prefix)]
        if len(hits) != 1:
            raise KeyError(f"commit hash 前缀非唯一/缺失: {hash_prefix!r} -> {hits}")
        return hits[0]

    def has(self, nid):
        return nid in self.N

    def label(self, nid):
        return self.N[nid]["label"]

    # ---- traversal primitives ----
    def module_of(self, fid):
        mods = set()
        for p in self.contains_parent.get(fid, ()):
            pn = self.N.get(p)
            if not pn:
                continue
            if pn["label"] == "Module":
                mods.add(p)
            elif pn["label"] == "Class":
                for gp in self.contains_parent.get(p, ()):
                    if self.N.get(gp, {}).get("label") == "Module":
                        mods.add(gp)
        return mods

    def rev_calls_closure(self, fid, depth):
        """反向 CALLS 分层 BFS（含自身 level0）。返回 levels dict。"""
        lv = {fid: 0}
        frontier = [fid]
        for d in range(1, depth + 1):
            nxt = []
            for x in frontier:
                for p in self.calls_rev.get(x, ()):
                    if p not in lv:
                        lv[p] = d
                        nxt.append(p)
            frontier = nxt
            if not frontier:
                break
        return lv

    def forward_path_exists(self, path):
        """校验 path=[a,b,c,...] 每一步 a->b 均有 CALLS 边。"""
        for a, b in zip(path, path[1:]):
            if b not in self.calls_fwd.get(a, ()):
                return False
        return True

    # ---- recipe dispatch: 由 args 派生 gold 实体集合（排序 list） ----
    def derive(self, recipe):
        op = recipe["op"]
        a = recipe.get("args", {})
        if op == "fn_module":
            return sorted(self.module_of(self.fn(a["fn"])))
        if op == "fn_callers":            # 直接调用方（1 跳反向 CALLS）
            return sorted(self.calls_rev.get(self.fn(a["fn"]), set()))
        if op == "concept_impl_fns":      # 概念的实现函数（IMPLEMENTS 反向，仅 Function）
            cid = a["concept"]
            return sorted(x for x in self.impl_rev.get(cid, ()) if self.label(x) == "Function")
        if op == "commit_mod_fns":        # 提交改动的函数（MODIFIES）
            return sorted(self.mod_fwd.get(self.commit(a["commit"]), set()))
        if op == "fn_concepts":           # 函数实现的概念（IMPLEMENTS 正向，仅 Concept）
            fid = self.fn(a["fn"])
            return sorted(x for x in self.impl_fwd.get(fid, ()) if self.label(x) == "Concept")
        if op == "class_module":          # 类所属模块
            cid = a["cls"]
            mods = set()
            for p in self.contains_parent.get(cid, ()):
                if self.N.get(p, {}).get("label") == "Module":
                    mods.add(p)
            return sorted(mods)
        if op == "rev_calls_closure":     # 反向 CALLS 闭包（不含自身）= 会被波及的上游函数
            fid = self.fn(a["fn"])
            lv = self.rev_calls_closure(fid, a["depth"])
            return sorted(k for k, v in lv.items() if v >= 1)
        if op == "impact_modules":        # 闭包（含自身）所覆盖的模块集
            fid = self.fn(a["fn"])
            lv = self.rev_calls_closure(fid, a["depth"])
            mods = set()
            for nid in lv:
                mods |= self.module_of(nid)
            return sorted(mods)
        if op == "calls_chain":           # 调用链中间节点（gold），path 另行校验
            path = [self.fn(q) for q in a["path_quals"]]
            if not self.forward_path_exists(path):
                raise ValueError(f"calls_chain 非真实调用链: {a['path_quals']}")
            return sorted(path[1:-1])      # 中间节点
        if op == "concept_fns_commits":   # 改过“实现概念X的函数”的提交（IMPLEMENTS∘MODIFIES 反向）
            cid = a["concept"]
            fns = [x for x in self.impl_rev.get(cid, ()) if self.label(x) == "Function"]
            commits = set()
            for f in fns:
                commits |= self.mod_rev.get(f, set())
            return sorted(commits)
        if op == "commit_fns_concepts":   # 提交改的函数各自实现的概念（MODIFIES∘IMPLEMENTS）
            cid = self.commit(a["commit"])
            fns = self.mod_fwd.get(cid, set())
            concepts = set()
            for f in fns:
                concepts |= {x for x in self.impl_fwd.get(f, ()) if self.label(x) == "Concept"}
            return sorted(concepts)
        if op == "design_provenance":     # 设计溯源：概念 + DESCRIBES 提交
            cid = a["concept"]
            commits = sorted(x for x in self.desc_rev.get(cid, ()) if self.label(x) == "Commit")
            return [cid] + commits
        raise ValueError(f"未知 recipe op: {op}")

    # ---- 泄漏检查：gold 实体的可识别名不得出现在题面 ----
    def distinctive_strings(self, nid):
        """返回该实体“会泄漏答案”的可识别字符串集合。"""
        n = self.N[nid]
        lab = n["label"]
        out = set()
        if lab == "Function":
            qn = n.get("qualname", "")
            out.add(qn)
            leaf = qn.split(".")[-1]
            # 常见词元 leaf 不作单独判据（invoke/__init__/run/main 等易误伤）
            if leaf and leaf.lower() not in {"invoke", "__init__", "run", "main", "check"}:
                out.add(leaf)
        elif lab == "Concept":
            out.add(n.get("name", ""))
            for al in (n.get("aliases") or []):
                out.add(al)
            for al in (n.get("zh_aliases") or []):
                out.add(al)
        elif lab == "Commit":
            out.add(n["hash"][:12])
            out.add(n["hash"])
        elif lab == "Module":
            # 可识别标识 = 末两段路径（dir/file）。裸目录词（package，如 "store"）
            # 会与被问主体名（如 Store 类）碰撞误伤，故不纳入——末两段已唯一标识答案模块。
            path = n.get("path", "")
            parts = path.replace("\\", "/").split("/")
            if len(parts) >= 2:
                out.add("/".join(parts[-2:]))
        elif lab == "Class":
            out.add(n.get("qualname", n.get("name", "")))
        return {s for s in out if s and len(s) >= 3}
