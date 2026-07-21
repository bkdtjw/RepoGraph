"""grok 协同审查后的回归自测（semantic.py 两处已修缺陷）。

不依赖 pytest，直接 assert；自行把 src 加入 sys.path。
运行：cd <repo> && python tests/test_semantic_grokfix.py

覆盖两条 grok 确认后修复的缺陷：
  F1  对齐时规范名残留在自身 aliases（多成员乱序合并）。
  F2  两个规范化键不同（本应独立）的概念经 _slug 归一后 concept_id 碰撞，
      落图时 merge_node 互相覆盖。
两者在“对多agent协作系统”实跑数据集里未触发（潜伏 bug），故用构造输入复现。
"""
from __future__ import annotations

import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from repograph import models  # noqa: E402
from repograph.models import GraphStore, PipelineStats  # noqa: E402
from repograph.extract import semantic  # noqa: E402

REPO = "demo"


def _settings(tmpdir: str):
    return types.SimpleNamespace(
        semantic_batch_size=20,
        semantic_confidence_min=0.4,   # 让 0.5/0.6 也能落图
        grok_timeout_s=60,
        grok_exe="unused-in-fake",
        output_dir=tmpdir,
    )


def _run(store, fake, tmpdir):
    stats = PipelineStats()
    orig = semantic.ask_grok
    semantic.ask_grok = fake
    try:
        return semantic.run_semantic(store, ".", REPO, stats, _settings(tmpdir)), stats
    finally:
        semantic.ask_grok = orig


# ---------------------------------------------------------------------------
# F1：规范名不得残留在自身别名集
# ---------------------------------------------------------------------------

def test_f1_canonical_not_in_own_aliases() -> None:
    c1 = models.commit_id(REPO, "aaa")
    st = GraphStore()
    # 同一条 message 同时含三处 quote 子串，三概念规范化后同键（去空格/连字符+小写）。
    st.merge_node(c1, "Commit", {"message": "alphabeta / Alpha Beta / alphabeta 皆同义"})

    def fake(prompt, json_schema=None, timeout=300, exe=None):
        # 顺序刻意制造：低→中→高 置信度，中间夹一个不同表面形，
        # 修复前会让最终规范名 'alphabeta' 同时进入 aliases。
        return {"concepts": [
            {"name": "alphabeta", "ctype": "domain_concept", "description": "x",
             "source_ref": c1, "quote": "alphabeta", "confidence": 0.5,
             "implements_candidates": []},
            {"name": "Alpha Beta", "ctype": "domain_concept", "description": "x",
             "source_ref": c1, "quote": "Alpha Beta", "confidence": 0.6,
             "implements_candidates": []},
            {"name": "alphabeta", "ctype": "domain_concept", "description": "x",
             "source_ref": c1, "quote": "alphabeta", "confidence": 0.9,
             "implements_candidates": []},
        ]}

    tmpdir = tempfile.mkdtemp(prefix="rg_sem_f1_")
    summary, _ = _run(st, fake, tmpdir)

    assert summary["concepts"] == 1, summary            # 三者同键 → 合并为一个概念
    cid = models.concept_id(semantic._slug("alphabeta"))
    node = st.get_node(cid)
    assert node is not None, list(st.nodes("Concept"))
    assert node["confidence"] == 0.9, node              # 取最高置信度表面形为规范名
    assert node["name"] == "alphabeta", node
    # 关键断言：规范名不得出现在自身 aliases 中
    assert node["name"] not in node["aliases"], node["aliases"]
    assert node["aliases"] == ["Alpha Beta"], node["aliases"]
    print("[OK] test_f1_canonical_not_in_own_aliases")


# ---------------------------------------------------------------------------
# F2：规范化键不同的概念不得碰撞到同一 concept_id
# ---------------------------------------------------------------------------

def test_f2_no_concept_id_collision() -> None:
    c1 = models.commit_id(REPO, "aaa")
    c2 = models.commit_id(REPO, "bbb")
    st = GraphStore()
    st.merge_node(c1, "Commit", {"message": "采用 foo_bar 命名约定"})
    st.merge_node(c2, "Commit", {"message": "重构 foo-bar 模块"})

    def fake(prompt, json_schema=None, timeout=300, exe=None):
        return {"concepts": [
            {"name": "foo_bar", "ctype": "domain_concept", "description": "下划线形",
             "source_ref": c1, "quote": "foo_bar", "confidence": 0.9,
             "implements_candidates": []},
            {"name": "foo-bar", "ctype": "domain_concept", "description": "连字符形",
             "source_ref": c2, "quote": "foo-bar", "confidence": 0.9,
             "implements_candidates": []},
        ]}

    tmpdir = tempfile.mkdtemp(prefix="rg_sem_f2_")
    summary, _ = _run(st, fake, tmpdir)

    # 规范化键不同（foo_bar vs foobar）→ 两个独立概念，且 _slug 会把二者都归一为
    # 'foo-bar'（修复前碰撞）。修复后 concept_id 用 g.key，二者必须各自成节点。
    assert summary["concepts"] == 2, summary
    concept_nodes = list(st.nodes("Concept"))
    ids = {n["id"] for n in concept_nodes}
    assert len(ids) == 2, ids                            # 两个独立 concept_id
    names = {n["name"] for n in concept_nodes}
    assert names == {"foo_bar", "foo-bar"}, names
    # 两条 DESCRIBES 分别来自 c1 / c2，且指向不同概念
    describes = {(s, d) for s, _t, d, _p in st.edges("DESCRIBES")}
    dst_by_src = {s: d for (s, d) in describes}
    assert set(dst_by_src) == {c1, c2}, describes
    assert dst_by_src[c1] != dst_by_src[c2], describes
    print("[OK] test_f2_no_concept_id_collision")


if __name__ == "__main__":
    test_f1_canonical_not_in_own_aliases()
    test_f2_no_concept_id_collision()
    print("ALL ASSERTIONS PASSED")
