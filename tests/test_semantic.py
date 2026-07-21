"""semantic.py / grok_client.py 自测（不依赖 pytest，直接 assert）。

运行：
    cd <repo> && python tests/test_semantic.py

主测试用 monkeypatch 顶替 semantic.ask_grok，构造三类概念（非法 quote / 低置信 /
合法）+ 一条跨提交同名变体（测对齐），断言校验、对齐、落图三段行为。
末尾另做一次真实 grok 冒烟（1 条小批），验证解析器；超时/失败不判定为测试失败。
"""
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from repograph import models
from repograph.models import GraphStore, PipelineStats
from repograph.extract import semantic
from repograph.extract.grok_client import (
    ask_grok as real_ask_grok, GrokError,
    _parse_structured, _extract_outermost_object,
)

REPO = "demo"
RELPATH = "app/mod.py"
MODULE_ID = models.module_id(REPO, RELPATH)          # demo::app/mod.py
F1 = models.symbol_id(REPO, RELPATH, "Ctx.compress")  # demo::app/mod.py::Ctx.compress
F2 = models.symbol_id(REPO, RELPATH, "run")           # demo::app/mod.py::run
GHOST = models.symbol_id(REPO, RELPATH, "Ghost.method")
C1 = models.commit_id(REPO, "aaa111")
C2 = models.commit_id(REPO, "bbb222")

C1_MSG = "引入三级可续传上下文压缩，以降低上下文预算"
C2_MSG = "完善三级可续传上下文压缩的实现细节"
MOD_DOC = "本模块实现工具执行的安全拦截逻辑"


def _build_store() -> GraphStore:
    st = GraphStore()
    st.merge_node(MODULE_ID, "Module", {"docstring": MOD_DOC, "name": "mod",
                                        "path": RELPATH, "repo": REPO})
    st.merge_node(F1, "Function", {"qualname": "Ctx.compress"})
    st.merge_node(F2, "Function", {"qualname": "run"})
    st.merge_node(C1, "Commit", {"message": C1_MSG, "hash": "aaa111"})
    st.merge_node(C2, "Commit", {"message": C2_MSG, "hash": "bbb222"})
    st.merge_edge(MODULE_ID, "CONTAINS", F1)
    st.merge_edge(MODULE_ID, "CONTAINS", F2)
    st.merge_edge(C1, "MODIFIES", F1, {"lines_added": 10})  # 候选集 = {F1}
    return st


def _fake_ask_grok(prompt, json_schema=None, timeout=300, exe=None):
    """按 prompt 内容区分 commit 批与 docstring 批，返回固定概念集合。"""
    if MOD_DOC in prompt:  # docstring 批
        return {"concepts": [
            {  # D：合法，命中模块内函数 F2
                "name": "安全拦截", "ctype": "domain_concept",
                "description": "工具执行前的安全拦截", "source_ref": MODULE_ID,
                "quote": "安全拦截", "confidence": 0.85,
                "implements_candidates": [F2],
            },
        ]}
    # commit 批
    return {"concepts": [
        {  # A：合法，命中 F1
            "name": "三级可续传上下文压缩", "ctype": "design_decision",
            "description": "分三级压缩历史消息且可续传", "source_ref": C1,
            "quote": "三级可续传上下文压缩", "confidence": 0.9,
            "implements_candidates": [F1],
        },
        {  # A'：与 A 规范化同名（含空格），来自 C2 —— 测对齐合并
            "name": "三级可续传 上下文压缩", "ctype": "design_decision",
            "description": "同一概念的另一处描述", "source_ref": C2,
            "quote": "三级可续传上下文压缩", "confidence": 0.7,
            "implements_candidates": [],
        },
        {  # B：非法 quote（不在原文），且 implements 目标为 ghost（不在候选）
            "name": "无关设计约束", "ctype": "constraint",
            "description": "编造的引用", "source_ref": C1,
            "quote": "这句话根本不在提交信息里面出现过", "confidence": 0.9,
            "implements_candidates": [GHOST],
        },
        {  # C：低置信度，应被丢弃
            "name": "低置信噪声", "ctype": "domain_concept",
            "description": "噪声", "source_ref": C1,
            "quote": "降低上下文预算", "confidence": 0.3,
            "implements_candidates": [],
        },
    ]}


def test_pipeline_with_fake():
    st = _build_store()
    stats = PipelineStats()
    tmpdir = tempfile.mkdtemp(prefix="repograph_sem_")
    settings = types.SimpleNamespace(
        semantic_batch_size=20,
        semantic_confidence_min=0.4,   # 令 B 减半后(0.45)仍可落图，以验证 quote_unverified 标记
        grok_timeout_s=60,
        grok_exe="unused-in-fake",
        output_dir=tmpdir,
    )

    orig = semantic.ask_grok
    semantic.ask_grok = _fake_ask_grok
    try:
        summary = semantic.run_semantic(st, ".", REPO, stats, settings)
    finally:
        semantic.ask_grok = orig

    # ---- 摘要 --------------------------------------------------------------
    assert summary["batches"] == 2, summary                       # 1 commit 批 + 1 docstring 批
    assert summary["concepts"] == 3, summary                      # A(含A'), B, D
    assert summary["describes"] == 3, summary                     # c1->A, c2->A, c1->B
    assert summary["implements"] == 3, summary                    # F1->A, module->D, F2->D

    # ---- 校验计数 ----------------------------------------------------------
    assert stats.semantic_rejected.get("low_confidence") == 1, stats.semantic_rejected  # C
    assert stats.semantic_rejected.get("bad_target") == 1, stats.semantic_rejected      # B 的 ghost
    assert stats.semantic_rejected.get("batch_error") is None, stats.semantic_rejected
    assert stats.semantic_extracted == 4, stats.semantic_extracted  # A,A',B,D 通过校验；C 丢弃

    # ---- 概念节点与对齐 ----------------------------------------------------
    cid_A = models.concept_id(semantic._slug("三级可续传上下文压缩"))
    node_A = st.get_node(cid_A)
    assert node_A is not None and node_A["label"] == "Concept"
    assert node_A["confidence"] == 0.9, node_A                    # 取高置信度
    assert "三级可续传 上下文压缩" in node_A["aliases"], node_A     # 变体进 aliases
    assert len(node_A["evidence"]) == 2, node_A                   # C1 与 C2 两条证据

    # C 被丢弃：图中不应出现该名称的概念
    names = {n.get("name") for n in st.nodes("Concept")}
    assert "低置信噪声" not in names, names
    assert names == {"三级可续传上下文压缩", "无关设计约束", "安全拦截"}, names

    # ---- 边与属性 ----------------------------------------------------------
    describes = {(s, d) for s, _t, d, _p in st.edges("DESCRIBES")}
    cid_B = models.concept_id(semantic._slug("无关设计约束"))
    cid_D = models.concept_id(semantic._slug("安全拦截"))
    assert (C1, cid_A) in describes and (C2, cid_A) in describes and (C1, cid_B) in describes

    implements = {(s, d) for s, _t, d, _p in st.edges("IMPLEMENTS")}
    assert (F1, cid_A) in implements
    assert (MODULE_ID, cid_D) in implements     # 模块 docstring 来源 -> IMPLEMENTS(Module->Concept)
    assert (F2, cid_D) in implements            # 命中候选 -> IMPLEMENTS(Function->Concept)
    assert (GHOST, cid_B) not in implements     # ghost 已被剔除，不建边

    # B 的 DESCRIBES 边应带 quote_unverified 且 confidence 已减半
    b_edge_props = None
    for s, _t, d, p in st.edges("DESCRIBES"):
        if s == C1 and d == cid_B:
            b_edge_props = p
    assert b_edge_props is not None
    assert b_edge_props["quote_unverified"] is True, b_edge_props
    assert abs(b_edge_props["confidence"] - 0.45) < 1e-9, b_edge_props

    # ---- 审计文件 ----------------------------------------------------------
    audit_path = os.path.join(tmpdir, "align_audit.jsonl")
    assert os.path.exists(audit_path), audit_path
    import json
    lines = [json.loads(l) for l in open(audit_path, encoding="utf-8") if l.strip()]
    a_line = next(r for r in lines if r["canonical_name"] == "三级可续传上下文压缩")
    assert a_line["merged"] is True and len(a_line["members"]) == 2, a_line

    print("[OK] test_pipeline_with_fake")


def test_parser_units():
    """离线单测解析器对 grok 信封 JSON 的拆包（据实测格式）。"""
    schema = {"type": "object", "properties": {"concepts": {"type": "array"}},
              "required": ["concepts"]}
    envelope = ('{"text": "{\\"concepts\\":[]}", "stopReason": "EndTurn", '
                '"structuredOutput": {"concepts": [{"name": "x"}]}}')
    got = _parse_structured(envelope, schema)
    assert got == {"concepts": [{"name": "x"}]}, got

    # 无 structuredOutput 时回退到 text 字段
    only_text = '{"text": "{\\"concepts\\":[1,2]}", "stopReason": "EndTurn"}'
    assert _parse_structured(only_text, schema) == {"concepts": [1, 2]}

    # 裸 JSON（无信封）
    assert _parse_structured('{"concepts":[]}', schema) == {"concepts": []}

    # 带包裹日志行 + 花括号提取
    noisy = 'INFO starting\n{"structuredOutput": {"concepts": []}}\nbye'
    assert _parse_structured(noisy, schema) == {"concepts": []}

    assert _extract_outermost_object('x {"a": {"b": 1}} y') == '{"a": {"b": 1}}'
    print("[OK] test_parser_units")


def smoke_real_grok():
    """真实 grok 冒烟：1 条小批，验证 grok_client 解析器能吃下真实 stdout。
    超时或失败仅打印，不判为测试失败（见文件说明）。"""
    schema = {"type": "object",
              "properties": {"concepts": {"type": "array", "items": {"type": "object"}}},
              "required": ["concepts"]}
    prompt = ('从下面文本抽取概念，严格输出 {"concepts": []} 形式的 JSON。\n'
              '[c1] 这是一条无实质设计内容的占位提交信息。')
    try:
        out = real_ask_grok(prompt, json_schema=schema, timeout=120)
    except Exception as exc:  # TimeoutExpired / GrokError / 其它环境问题
        print(f"[SKIP] smoke_real_grok: {type(exc).__name__}: {str(exc)[:120]}")
        return
    ok = isinstance(out, dict) and "concepts" in out
    print(f"[{'OK' if ok else 'WARN'}] smoke_real_grok -> keys={list(out.keys()) if isinstance(out, dict) else type(out)}")


if __name__ == "__main__":
    test_parser_units()
    test_pipeline_with_fake()
    print("ALL ASSERTIONS PASSED")
    smoke_real_grok()
