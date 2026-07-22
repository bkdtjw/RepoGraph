# -*- coding: utf-8 -*-
"""C2 卡片两轮并集（round1 ∪ round2）——稳健取并，最大化口语别名覆盖、不丢已桥接项。

动机：单轮 LLM 生成有随机性，重生成可能改善欠覆盖实体、却也可能改动已命中实体的别名。
取两轮**并集**（交错保序去重）既纳入 round2 更激进的口语说法、又不丢 round1 已桥接的别名。

规则：
- zh_aliases：round1、round2 交错并集，实体内去重，每实体上限 MAX_MERGED（=6，2 轮并集的
  合理上浮；仍为「受控口语近义说法」）；随后跨实体去泛词（同一别名出现在 ≥3 实体 → 剔除）。
- desc：优先 round2 采纳的 desc（改进 prompt），否则回退 round1 采纳的 desc。
- desc_accepted：任一轮采纳即 True（供 enrich 决定是否写 zh_desc）。

产物：design_work/c2_cards.json（供 enrich 消费）。用法：python design_work/c2_merge_cards.py
"""
from __future__ import annotations

import json
import os
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R1 = os.path.join(ROOT, "design_work", "c2_cards.round1.json")
R2 = os.path.join(ROOT, "design_work", "c2_cards.round2.json")
OUT = os.path.join(ROOT, "design_work", "c2_cards.json")

MAX_MERGED = 6
DUP_ENTITY_CAP = 3      # 同一别名出现在 ≥3 实体 → 判泛词剔除


def load(path):
    d = json.load(open(path, encoding="utf-8"))
    if d.get("blocked"):
        raise RuntimeError(f"{path} blocked，无法并集")
    return {c["id"]: c for c in d["cards"]}, d.get("meta", {})


def interleave_dedup(a, b, cap):
    out, seen = [], set()
    for i in range(max(len(a), len(b))):
        for seq in (a, b):
            if i < len(seq):
                x = (seq[i] or "").strip()
                if len(x) >= 2 and x not in seen:
                    seen.add(x)
                    out.append(x)
        if len(out) >= cap:
            break
    return out[:cap]


def main():
    c1, m1 = load(R1)
    c2, m2 = load(R2)
    all_ids = list(dict.fromkeys(list(c1) + list(c2)))

    merged = {}
    for nid in all_ids:
        a = c1.get(nid, {})
        b = c2.get(nid, {})
        aliases = interleave_dedup(a.get("zh_aliases") or [],
                                   b.get("zh_aliases") or [], MAX_MERGED)
        # desc：优先 round2 采纳，否则 round1 采纳
        if b.get("desc_accepted") and b.get("desc"):
            desc, desc_ok = b["desc"], True
        elif a.get("desc_accepted") and a.get("desc"):
            desc, desc_ok = a["desc"], True
        else:
            desc, desc_ok = (b.get("desc") or a.get("desc")), False
        base = b if b else a
        merged[nid] = {
            "id": nid, "label": base.get("label"), "name": base.get("name"),
            "tier": base.get("tier"),
            "desc": desc, "desc_accepted": desc_ok,
            "zh_aliases": aliases,
        }

    # 跨实体去泛词：同一别名出现在 ≥DUP_ENTITY_CAP 个实体 → 剔除
    ct = Counter()
    for c in merged.values():
        for a in c["zh_aliases"]:
            ct[a] += 1
    n_prog_drop = 0
    for c in merged.values():
        keep = [a for a in c["zh_aliases"] if ct[a] < DUP_ENTITY_CAP]
        n_prog_drop += len(c["zh_aliases"]) - len(keep)
        c["zh_aliases"] = keep

    cards = list(merged.values())
    n_alias = sum(len(c["zh_aliases"]) for c in cards)
    n_desc = sum(1 for c in cards if c["desc_accepted"]
                 and c["label"] in ("Function", "Class"))
    out = {"blocked": False,
           "meta": {"strategy": "round1 ∪ round2 交错并集去重", "cap_per_entity": MAX_MERGED,
                    "n_cards": len(cards), "n_aliases_total": n_alias,
                    "n_zh_desc_symbols": n_desc, "cross_entity_prog_drop": n_prog_drop,
                    "round1_meta": m1, "round2_meta": m2},
           "cards": cards}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("并集卡片:", len(cards), " 别名总数:", n_alias,
          " Function/Class zh_desc:", n_desc, " 跨实体去泛词剔除:", n_prog_drop)
    print("写出:", os.path.relpath(OUT, ROOT))


if __name__ == "__main__":
    main()
