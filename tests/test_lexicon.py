"""自测：retrieve.lexicon（中文停用词过滤 + 代码缩写双向扩展，v0.3 · Phase C2）。

覆盖落地设计 §4.4 / calibration §3.3 对 D-N1 的修订：停用/疑问/功能词黑名单（整词匹配、
不误删含停用字的实词）+ 缩写双向扩展（ctx↔context）。全部纯函数、零第三方依赖。

真实运行：cd C:/Users/nirvana/Desktop/代码库知识图谱 && python tests/test_lexicon.py
"""
import sys

sys.path.insert(0, "src")

from repograph.retrieve.lexicon import (
    ZH_STOPWORDS, is_zh_stopword, filter_stopwords,
    ABBREVIATIONS, expand_abbreviations,
)


def test_stopword_membership():
    # 疑问/指代/功能词在表内
    for w in ("怎么", "哪个", "那块", "起来", "是不是", "有没有", "这个", "东西"):
        assert is_zh_stopword(w), f"{w} 应为停用词"
    # 内容词不在表内（关键：整词匹配，不因含停用字误删）
    for w in ("终止", "恢复", "看门狗", "故障注入", "适配层", "崩溃", "门禁"):
        assert not is_zh_stopword(w), f"{w} 不应被判停用词"
    print("test_stopword_membership OK")


def test_filter_stopwords():
    terms = ["怎么", "终止", "起来", "派发", "哪个", "恢复"]
    assert filter_stopwords(terms) == ["终止", "派发", "恢复"], filter_stopwords(terms)
    # 保序保重复（承载词频）
    assert filter_stopwords(["恢复", "恢复", "怎么"]) == ["恢复", "恢复"]
    print("test_filter_stopwords OK")


def test_abbrev_bidirectional():
    # ctx ↔ context 双向
    assert "context" in expand_abbreviations("ctx"), expand_abbreviations("ctx")
    assert "ctx" in expand_abbreviations("context"), expand_abbreviations("context")
    # cfg / config
    assert "config" in expand_abbreviations("cfg")
    assert "cfg" in expand_abbreviations("config")
    # 忽略大小写
    assert "context" in expand_abbreviations("CTX")
    # 无扩展 → 空集，不自含
    assert expand_abbreviations("zzzz") == set()
    assert "ctx" not in expand_abbreviations("ctx"), "扩展集不应含自身"
    print("test_abbrev_bidirectional OK")


def test_stopwords_are_content_safe():
    """回归护栏：FZ gold 概念名的任一 n-gram 片段都不应被整词误判为停用词。"""
    # 取若干 FZ gold 概念/语义关键词，确认其字面不落黑名单（否则召回被自伤）
    for w in ("看门狗三级", "崩溃恢复算法", "权限三件套", "门禁裁决入口", "视图组装", "状态层"):
        assert w not in ZH_STOPWORDS, f"{w} 不该在停用表"
    print("test_stopwords_are_content_safe OK")


if __name__ == "__main__":
    test_stopword_membership()
    test_filter_stopwords()
    test_abbrev_bidirectional()
    test_stopwords_are_content_safe()
    print("\nALL TESTS PASSED")
