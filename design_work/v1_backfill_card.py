# -*- coding: utf-8 -*-
"""补跑 v1_cards.json 中 net_fail 的单卡（不阻塞全局的失败卡二次尝试）。

复用 v1_gen_cards 的网关与校验逻辑；成功则合并回 v1_cards.json 并刷新 meta 计数；
仍失败则原样保留记录。绝不打印 token。
"""
from __future__ import annotations

import json
import os

import v1_gen_cards as G

OUT = G.OUT


def main():
    d = json.load(open(OUT, encoding="utf-8"))
    if d.get("blocked"):
        print("blocked，不补跑")
        return 0
    base, token = G.load_cfg()
    changed = 0
    for rec in d["cards"]:
        if rec.get("accepted"):
            continue
        if not str(rec.get("reason", "")).startswith("net_fail"):
            continue
        t = {"input_qualname": rec["input_summary"]["qualname"],
             "input_doc": rec["input_summary"]["doc"], "name": rec["name"]}
        r = G.gen_one(base, token, t)
        print("补跑 %-36s → accepted=%s reason=%s" % (rec["name"][:36], r["accepted"], r["reason"]))
        rec["card_text"] = r["card_text"]
        rec["card_raw"] = r["card_raw"]
        rec["accepted"] = r["accepted"]
        rec["reason"] = r["reason"]
        rec["char_count"] = r.get("char_count")
        rec["attempts"] = r["attempts"]
        if r["accepted"]:
            changed += 1
    # 刷新 meta
    cards = d["cards"]
    n_ok = sum(1 for c in cards if c["accepted"])
    n_dis = sum(1 for c in cards if not c["accepted"] and c["reason"] == "whitelist_violation")
    n_net = sum(1 for c in cards if not c["accepted"] and str(c["reason"]).startswith("net_fail"))
    d["meta"]["n_accepted"] = n_ok
    d["meta"]["n_discarded"] = n_dis
    d["meta"]["n_netfail"] = n_net
    d["meta"]["backfilled"] = d["meta"].get("backfilled", 0) + changed
    json.dump(d, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("补跑成功 %d 张；现 accepted=%d discarded=%d netfail=%d" % (changed, n_ok, n_dis, n_net))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
