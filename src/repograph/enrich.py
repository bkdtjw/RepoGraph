"""C2 双语属性回填（v0.3 · Phase C2 · 裁定 D-11/D-16）——幂等增量写 graph.json。

把 ``design_work/c2_cards.json``（索引期网关生成的富化双语卡片）**增量**回填到
``output/graph.json`` 的节点属性：

- Function / Class → ``zh_desc``（≤40 字中文功能描述）+ ``zh_aliases``（口语近义说法）
- Concept          → ``zh_aliases``（口语近义说法；grok 原生 ``aliases`` 不动，并存）

**只加属性，绝不动概念集/边集**（D-P2 的 blocking 变更本轮不做，防 gold 漂移；见 §7 C2 纪律）。
``zh_desc``/``zh_aliases`` 是 C2 **全权拥有**字段——每次运行整体覆盖（非追加），故**幂等**：
重跑同一 c2_cards.json 得同一 graph.json；换一版卡片重跑则干净替换、不残留旧别名。

节点/边的 JSON 结构（键序、其它属性）逐一保留，仅在命中节点上增/改这两个键，``git diff``
可清晰审计。找不到卡片 / 卡片 blocked → 明确报错退出，绝不静默（真实数据铁律）。

CLI：``python -m repograph.enrich``（可选 ``--cards``/``--graph``/``--dry-run``）。
只依赖标准库。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))                 # src/repograph
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))               # 仓库根
DEFAULT_CARDS = os.path.join(_REPO_ROOT, "design_work", "c2_cards.json")
DEFAULT_GRAPH = os.path.join(_REPO_ROOT, "output", "graph.json")

# C2 全权拥有的双语属性键（每次运行整体覆盖 → 幂等）
_C2_KEYS = ("zh_desc", "zh_aliases")


def load_cards(path: str) -> list[dict]:
    """读 c2_cards.json，返回 cards 列表。blocked / 缺失 / 结构不符 → 抛异常（不静默）。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"找不到卡片文件：{path}（先跑 design_work/c2_gen_cards.py）")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"卡片文件结构异常（非 dict）：{path}")
    if data.get("blocked"):
        raise RuntimeError(f"卡片生成被 blocked（网关不可用），拒绝回填假数据：{path}")
    cards = data.get("cards")
    if not isinstance(cards, list) or not cards:
        raise ValueError(f"卡片文件无 cards 或为空：{path}")
    return cards


def _dedup(seq) -> list:
    """保序去重（保留首次出现），过滤空串。"""
    out, seen = [], set()
    for x in seq or []:
        if isinstance(x, str):
            s = x.strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
    return out


def build_attr_map(cards: list[dict]) -> dict:
    """卡片 → {node_id: {zh_desc?, zh_aliases}}（每 id 一份 C2 属性，覆盖式）。

    - Function/Class：desc 被 desc_accepted 采纳才写 zh_desc；zh_aliases 恒写（可为空列表）。
    - Concept：只写 zh_aliases（desc 冗余于原生 description，不写）。
    同一 id 多卡（不应发生）取最后一张，记入返回的冲突集。
    """
    attr: dict[str, dict] = {}
    dup_ids: list[str] = []
    for c in cards:
        nid = c.get("id")
        if not nid:
            continue
        if nid in attr:
            dup_ids.append(nid)
        label = c.get("label")
        aliases = _dedup(c.get("zh_aliases"))
        entry: dict = {"zh_aliases": aliases}
        if label in ("Function", "Class") and c.get("desc_accepted") and c.get("desc"):
            entry["zh_desc"] = c["desc"]
        attr[nid] = entry
    attr["__dup_ids__"] = dup_ids  # type: ignore[assignment]
    return attr


def apply_to_graph(graph: dict, attr: dict) -> dict:
    """把 C2 属性覆盖式写入 graph 节点（只改命中节点的 _C2_KEYS，其余原样）。返回统计。"""
    dup_ids = attr.pop("__dup_ids__", [])
    by_id = {n["id"]: n for n in graph["nodes"]}
    stats = {"cards": len(attr), "matched": 0, "missing": [],
             "zh_desc_set": 0, "zh_aliases_set": 0, "aliases_total": 0,
             "dup_card_ids": dup_ids, "by_label": {}}
    for nid, entry in attr.items():
        node = by_id.get(nid)
        if node is None:
            stats["missing"].append(nid)
            continue
        stats["matched"] += 1
        lbl = node.get("label", "?")
        stats["by_label"][lbl] = stats["by_label"].get(lbl, 0) + 1
        # 覆盖式写 C2 键（幂等）：先清旧 C2 键，再按本次卡片写入
        for k in _C2_KEYS:
            node.pop(k, None)
        aliases = entry.get("zh_aliases") or []
        if aliases:
            node["zh_aliases"] = aliases
            stats["zh_aliases_set"] += 1
            stats["aliases_total"] += len(aliases)
        if "zh_desc" in entry:
            node["zh_desc"] = entry["zh_desc"]
            stats["zh_desc_set"] += 1
    return stats


def enrich(cards_path: str = DEFAULT_CARDS, graph_path: str = DEFAULT_GRAPH,
           dry_run: bool = False) -> dict:
    """主流程：读卡片 → 读图 → 覆盖式回填 C2 属性 → （非 dry-run）写回。返回统计 dict。"""
    cards = load_cards(cards_path)
    with open(graph_path, "r", encoding="utf-8") as f:
        graph = json.load(f)
    n_nodes_before, n_edges_before = len(graph["nodes"]), len(graph["edges"])

    attr = build_attr_map(cards)
    stats = apply_to_graph(graph, attr)

    # 不变量：节点/边集大小不变（只加属性，D-P2 blocking 本轮不做）
    assert len(graph["nodes"]) == n_nodes_before, "节点集被改动（禁止）"
    assert len(graph["edges"]) == n_edges_before, "边集被改动（禁止）"
    stats["nodes"] = n_nodes_before
    stats["edges"] = n_edges_before

    if not dry_run:
        # 与 GraphStore.save 同款序列化参数（ensure_ascii=False, indent=1），减小 diff 噪声
        with open(graph_path, "w", encoding="utf-8") as f:
            json.dump(graph, f, ensure_ascii=False, indent=1)
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="C2 双语属性幂等回填 graph.json")
    ap.add_argument("--cards", default=DEFAULT_CARDS)
    ap.add_argument("--graph", default=DEFAULT_GRAPH)
    ap.add_argument("--dry-run", action="store_true", help="只统计不写回")
    args = ap.parse_args(argv)

    try:
        stats = enrich(args.cards, args.graph, dry_run=args.dry_run)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"[FATAL] {e}", file=sys.stderr)
        return 2

    print("=" * 60)
    print("C2 双语属性回填" + ("（DRY-RUN 未写回）" if args.dry_run else ""))
    print(f"  卡片数: {stats['cards']}  命中节点: {stats['matched']}"
          f"  未命中: {len(stats['missing'])}")
    print(f"  zh_desc 写入: {stats['zh_desc_set']}"
          f"  zh_aliases 写入: {stats['zh_aliases_set']}"
          f"  别名总数: {stats['aliases_total']}")
    print(f"  分布: {stats['by_label']}")
    print(f"  图规模不变: 节点 {stats['nodes']} · 边 {stats['edges']}")
    if stats["missing"]:
        print(f"  [WARN] 未命中卡片 id（前5）: {stats['missing'][:5]}")
    if stats["dup_card_ids"]:
        print(f"  [WARN] 重复卡片 id: {stats['dup_card_ids'][:5]}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
