"""自测：repograph.enrich（C2 双语属性幂等回填，v0.3 · Phase C2 · D-11/D-16）。

用**合成**小图 + 合成卡片验证：
  A. Function/Class 写 zh_desc + zh_aliases；Concept 只写 zh_aliases（原生 aliases 不动）；
  B. **只加属性**——节点/边集大小不变（不变量断言）；
  C. **幂等**——重跑同一卡片得同一结果；换一版卡片重跑干净替换（不残留旧别名）；
  D. desc 未采纳（desc_accepted=false）不写 zh_desc，但仍写 zh_aliases。

不碰真实 output/graph.json（用 tempfile），零网关。
真实运行：cd C:/Users/nirvana/Desktop/代码库知识图谱 && python tests/test_enrich.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, "src")

from repograph import enrich


def _write(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _mk_graph():
    return {
        "nodes": [
            {"id": "r::a.py::foo", "label": "Function", "qualname": "foo"},
            {"id": "r::a.py::Bar", "label": "Class", "qualname": "Bar"},
            {"id": "concept::看门狗三级", "label": "Concept", "name": "看门狗三级",
             "aliases": ["原生别名"]},
            {"id": "r::a.py", "label": "Module", "path": "a.py"},
        ],
        "edges": [{"src": "r::a.py", "type": "CONTAINS", "dst": "r::a.py::foo",
                   "properties": {}}],
    }


def _cards(alias_foo):
    return {"blocked": False, "meta": {}, "cards": [
        {"id": "r::a.py::foo", "label": "Function", "name": "foo",
         "desc": "做 foo 的事", "zh_aliases": alias_foo, "desc_accepted": True},
        {"id": "r::a.py::Bar", "label": "Class", "name": "Bar",
         "desc": None, "zh_aliases": ["酒吧", "吧台"], "desc_accepted": False},
        {"id": "concept::看门狗三级", "label": "Concept", "name": "看门狗三级",
         "desc": "监控超时", "zh_aliases": ["盯着干活", "超时监控"], "desc_accepted": True},
    ]}


def test_enrich_basic_and_invariant():
    d = tempfile.mkdtemp()
    gp = os.path.join(d, "graph.json")
    cp = os.path.join(d, "cards.json")
    _write(gp, _mk_graph())
    _write(cp, _cards(["方法一", "调用二"]))

    stats = enrich.enrich(cp, gp)
    g = json.load(open(gp, encoding="utf-8"))
    by = {n["id"]: n for n in g["nodes"]}

    # A. Function 写 zh_desc + zh_aliases
    foo = by["r::a.py::foo"]
    assert foo.get("zh_desc") == "做 foo 的事", foo
    assert foo.get("zh_aliases") == ["方法一", "调用二"], foo
    # desc 未采纳 → 不写 zh_desc，但写 zh_aliases（Class Bar）
    bar = by["r::a.py::Bar"]
    assert "zh_desc" not in bar, bar
    assert bar.get("zh_aliases") == ["酒吧", "吧台"], bar
    # Concept 只写 zh_aliases，原生 aliases 不动
    wd = by["concept::看门狗三级"]
    assert wd.get("aliases") == ["原生别名"], "原生 aliases 被改动"
    assert wd.get("zh_aliases") == ["盯着干活", "超时监控"], wd
    assert "zh_desc" not in wd, "Concept 不应写 zh_desc"

    # B. 只加属性——节点/边集大小不变
    assert len(g["nodes"]) == 4 and len(g["edges"]) == 1
    assert stats["nodes"] == 4 and stats["edges"] == 1
    print("test_enrich_basic_and_invariant OK")


def test_enrich_idempotent_and_replace():
    d = tempfile.mkdtemp()
    gp = os.path.join(d, "graph.json")
    cp = os.path.join(d, "cards.json")
    _write(gp, _mk_graph())
    _write(cp, _cards(["方法一", "调用二"]))

    enrich.enrich(cp, gp)
    g1 = json.load(open(gp, encoding="utf-8"))
    # 幂等：同一卡片重跑，结果不变
    enrich.enrich(cp, gp)
    g2 = json.load(open(gp, encoding="utf-8"))
    assert g1 == g2, "重跑同一卡片非幂等"

    # 换一版卡片：zh_aliases 干净替换（不残留旧别名）
    _write(cp, _cards(["新别名A", "新别名B"]))
    enrich.enrich(cp, gp)
    g3 = json.load(open(gp, encoding="utf-8"))
    foo = {n["id"]: n for n in g3["nodes"]}["r::a.py::foo"]
    assert foo["zh_aliases"] == ["新别名A", "新别名B"], f"应干净替换，实得 {foo['zh_aliases']}"
    assert "方法一" not in foo["zh_aliases"], "残留旧别名（非幂等替换）"
    print("test_enrich_idempotent_and_replace OK")


def test_enrich_rejects_blocked():
    d = tempfile.mkdtemp()
    gp = os.path.join(d, "graph.json")
    cp = os.path.join(d, "cards.json")
    _write(gp, _mk_graph())
    _write(cp, {"blocked": True, "meta": {"probe_error": "x"}})
    try:
        enrich.enrich(cp, gp)
        assert False, "blocked 卡片应抛异常（拒绝回填假数据）"
    except RuntimeError:
        pass
    print("test_enrich_rejects_blocked OK")


if __name__ == "__main__":
    test_enrich_basic_and_invariant()
    test_enrich_idempotent_and_replace()
    test_enrich_rejects_blocked()
    print("\nALL TESTS PASSED")
