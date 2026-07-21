"""质量指标汇总（§4.6）。

把图谱规模统计（GraphStore.counts()）与流水线质量指标
（PipelineStats.as_dict()）合并成一份 JSON，供 `repograph stats` 与评测报告
直接消费（§10.3：不另建统计通道）。两者的键互不相交，扁平合并不丢信息。
"""
from __future__ import annotations

import json
import os

from .models import GraphStore, PipelineStats


def write_stats(store: GraphStore, pstats: PipelineStats, path: str) -> dict:
    """合并 store.counts() 与 pstats.as_dict()，写入 JSON 文件并返回合并后的 dict。"""
    merged: dict = {}
    merged.update(store.counts())      # nodes / edges / total_nodes / total_edges
    merged.update(pstats.as_dict())    # call_resolved_rate / parse_skips / ... 等质量指标

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    return merged
