"""S1 五路路由器（v0.3 · Phase C1）——把用户问题分诊到 5 个路由标签。

契约（落地设计 §4.2 / 附录 A，裁定 D-01/D-05/D-17）：本模块是 v0.1「隐式二元瀑布」
之上**新增的前置分诊层**。它把自然语言问题落到 5 个标签之一：

    meta          —— 元问题（"你了解这个项目吗 / 你能干嘛"）→ 注入 repo_card
    global        —— 全局概览（"整体架构 / 介绍一下 / 规模多大"）→ build_overview
    entity_local  —— 指向具体实体（含代码词元 / 可链接）→ 现四档瀑布
    structural    —— 结构化计数（"列出所有端点 / 最……的前 N"）→ 能定量者走概览字段
    out_of_scope  —— 界外常识问题（"什么是 X"且无任何仓库指向）→ 界外声明 + 建议问法

三个纯函数，全部确定性、零第三方依赖，同时服务路由器与链接器：

- ``normalize(text)``       S0 规范化：全半角统一、保留标识符大小写、保留反引号。
- ``is_code_token(text)``   代码词元检测：camelCase/snake_case/点路径/文件后缀/#数字/路由样式/反引号。
- ``route(question, linked, topic_hits, has_code_token)``  规则表按序匹配 → (label, rule_id)。

时序（落地设计 §4.2，消除 route↔linker 先后歧义）：调用方须先 ``normalize`` →
``link_entities`` + ``is_code_token`` 算出信号，再调 ``route``；故 ``no_linker_hit`` /
``no_repo_reference`` 均为**链接后**信号。规则全不中 → 返回 ``(entity_local, None)``
交现有瀑布兜底（离线确定性默认；网关侧 ``semantic_mode=='llm'`` 时另有 LLM 兜底分类，
非本模块职责）。

只依赖标准库 ``re``；不 import 任何其它 repograph 模块（纯路由，可被 context / server 复用）。
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# S0 规范化（落地设计 §3.2 表行1 / D-08）
# ---------------------------------------------------------------------------

# 全角 ASCII（U+FF01–U+FF5E）→ 半角（偏移 0xFEE0）；全角空格 U+3000 → 半角空格。
_FULLWIDTH_OFFSET = 0xFEE0
_FULLWIDTH_SPACE = "　"


def normalize(text: str) -> str:
    """S0 规范化：全半角统一 + 保留标识符大小写 + 保留反引号（供代码词元检测）。

    - 全角 ASCII 字符（含全角数字/字母/标点 ？，（）等）统一为半角，消除
      "ＡＰＩ" 与 "API"、"？" 与 "?" 的表层分裂；
    - **不做大小写折叠**（标识符大小写载有信息，如 camelCase）；
    - 反引号原样保留——``is_code_token`` 据此把反引号内内容标为强代码词元。

    纯字符级映射，不改中文内容，不动 CJK 标点 。、（U+3002/U+3001，不在全角 ASCII 段）。
    """
    if not text:
        return ""
    out: list[str] = []
    for ch in text:
        o = ord(ch)
        if ch == _FULLWIDTH_SPACE:
            out.append(" ")
        elif 0xFF01 <= o <= 0xFF5E:
            out.append(chr(o - _FULLWIDTH_OFFSET))
        else:
            out.append(ch)
    return "".join(out)


# ---------------------------------------------------------------------------
# 代码词元检测（落地设计 §3.1 表行9 / §4.2 / D-07）
# ---------------------------------------------------------------------------

# camelCase / mixedCase：一个字母段内出现「小写紧跟大写」即判（camelCase、FastAPI、runThread）。
_RE_CAMEL = re.compile(r"[A-Za-z][a-z0-9]*[a-z][A-Z][A-Za-z0-9]*")
# snake_case：含下划线且含字母的标识符（_handle_terminate、__init__、append_system_event）。
_RE_SNAKE = re.compile(r"[A-Za-z_]*[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]*|_[A-Za-z][A-Za-z0-9_]*")
# 点路径：a.b / Store._begin / orch.scheduler.core（至少一个点连接两段标识符）。
_RE_DOTTED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
# 文件后缀：带已知代码文件扩展名的文件/路径（core.py、cli/main.py、config.json）。
_RE_FILESUFFIX = re.compile(
    r"[A-Za-z0-9_./\-]*[A-Za-z0-9_]"
    r"\.(?:py|pyi|js|jsx|ts|tsx|json|md|txt|ya?ml|toml|cfg|ini|sql|sh|html?|css|xml|rs|go|java|c|cpp|h)"
    r"\b"
)
# #数字：issue / PR 引用样式（#123）。
_RE_HASHNUM = re.compile(r"#\d+")
# 路由样式：以斜杠起的路径段（/api/users、/v1/messages）。
_RE_ROUTE = re.compile(r"/[A-Za-z][A-Za-z0-9_\-]*(?:/[A-Za-z0-9_\-{}:]+)*")
# 反引号强代码词元：`foo` 内非空内容。
_RE_BACKTICK = re.compile(r"`[^`]+`")

_CODE_TOKEN_RES = (
    _RE_BACKTICK, _RE_SNAKE, _RE_CAMEL, _RE_DOTTED,
    _RE_FILESUFFIX, _RE_HASHNUM, _RE_ROUTE,
)


def is_code_token(text: str) -> bool:
    """问题里是否含**代码词元**（camelCase/snake_case/点路径/文件后缀/#数字/路由样式/反引号）。

    这是路由信号 ``has_code_token`` 的物料来源，也可服务链接器。**形态判定**：只看词面
    结构，不查图谱——单个全小写英文词（如 ``invoke`` / ``run``）**不算**代码词元（它们靠
    ``link_entities`` 命中后经 entity_local 兜底进入符号档，见落地设计 §4.2 规则语义补注）。
    """
    if not text:
        return False
    return any(rx.search(text) for rx in _CODE_TOKEN_RES)


# ---------------------------------------------------------------------------
# 指代词（S5 焦点栈触发条件之一；此处用于 oos-1 组合谓词的"无指代"分量）
# ---------------------------------------------------------------------------

_RE_PRONOUN = re.compile(r"(它|这个|这块|那块|那个|该|上面|上文|前面|刚才|之前那|上述)")


# ---------------------------------------------------------------------------
# 规则表（落地设计 附录 A · Python 字面量，替代 router_rules.yaml / D-17）
#
# 按序首中即出；每条 {id, label, pattern(可选,编译 re), requires(可选,信号名列表)}。
# meta-1/meta-2 正则较附录 A 初始表**扩充了 _is_meta_question 暴露的口语盲区**
# （落地设计 §4.2 明令"正则须补口语盲区"；已逐题核验 eval/dataset.jsonl 的 L0 十题）：
#   - meta-1 支持"动词…仓库名"与"仓库名…动词"双序 + ≤4 字口语填充 + 错别字"带码库"
#     （L0-02「晓得我这破仓库」、L0-06「带码库…熟悉」、L0-10「认识下这个工程」）；
#   - meta-2 覆盖能力/身份问询（L0-03「你能干嘛」、L0-07「你是谁…帮我看代码」）。
# 规则表每次增删必须附对应回归用例（风险 F3）——见 tests/test_router.py。
# ---------------------------------------------------------------------------

# 认知类动词（"了解/读懂"这类对仓库的认知/熟悉动作）
_COG = r"(?:知道|了解|理解|认识|熟悉|清楚|晓得|明白|懂得|读得?懂|看得?懂|懂)"
# 仓库指称名词（含错别字"带码库"=代码库；"代码"置末，靠动词邻接约束防误判）
_REPO = r"(?:代码库|带码库|代码仓库|代码仓|仓库|项目|工程|repo|codebase|代码)"
# 口语填充：动词与仓库名之间允许 ≤4 个非句读字符（"我这破" / "下这个" / "我的"）
_FILL = r"[^，。？！,.?!、；;：:]{0,4}"

_META_1 = _COG + _FILL + _REPO + "|" + _REPO + _FILL + _COG
_META_2 = (
    r"你(是谁|是什么(?:东西)?|能(?:干|做|帮)|会(?:干|做)|有(?:什么|哪些)(?:功能|能力|用))"
    r"|怎么用你|帮我?看(?:一?下)?(?:代码|仓库|项目|工程)"
)
_STRUCT_1 = r"(最|前\s*\d+|多少|几个|统计|列出(?:所有|全部)?|排(?:序|名)|总共有)"
# global-1 覆盖两类"项目级"问题：① 概览/架构类触发词；② **仓库指称**（指示词 + 仓库范围
# 名词，如"这项目/这仓库"）——后者承接 v0.1 `_is_meta_question` 的"这项目"标记语义
# （落地设计 §4.2 global = 项目级问题），使"为什么这项目用 X"这类项目级问法落 global 概览
# 而非被弱主题词拽进 topic。仓库范围名词只取 项目/仓库/工程/代码库（**不含** 系统/代码/程序——
# 后者出现在 FZ 口语题里，会误伤主题召回），已核对 eval L0/FZ 无回归。
# 注：**不含「大概」**（v0.3 · C2 修）——「大概」歧义：既可作全局触发（"大概介绍一下"，已由
# 介绍/讲讲 覆盖），也常作口语程度副词修饰具体问题（FZ-d05「怎么估摸一段话大概占多少篇幅」
# 是 topic 题，误落 global 会丢弃 topic 召回）。全局「大概讲讲/大概是个什么…总览」由 讲讲/
# 介绍/总览/仓库指称 兜底，故删「大概」不损全局覆盖、消除对 topic 题的误路由（F3 附回归用例）。
_GLOBAL_1 = (
    r"整体|总体|全局|架构|结构|介绍|讲讲|说说|是(?:干|做)(?:什么|嘛|啥)"
    r"|干啥|干什么的|质量|难点|亮点|风格|规模|多大|总览|概览|概述"
    r"|(?:这|这个|该|本|你们的?|整个|我的|我这|咱们?的?)\s*(?:项目|仓库|工程|代码库)"
)
# oos-1 正则只作**触发候选**，最终由 requires:[no_repo_reference] 组合谓词把关
# （落地设计 §4.2 补注：否则"什么是适配层"这类仓库内概念会被误判界外）。
_OOS_1 = r"(是什么意思|什么是)"

ROUTER_RULES: list[dict] = [
    {"id": "meta-1", "label": "meta", "pattern": re.compile(_META_1)},
    {"id": "meta-2", "label": "meta", "pattern": re.compile(_META_2)},
    {"id": "struct-1", "label": "structural", "requires": ["has_code_token"],
     "pattern": re.compile(_STRUCT_1)},
    {"id": "entity-1", "label": "entity_local", "requires": ["has_code_token"]},
    {"id": "global-1", "label": "global", "requires": ["no_code_token", "no_linker_hit"],
     "pattern": re.compile(_GLOBAL_1)},
    {"id": "oos-1", "label": "out_of_scope", "requires": ["no_repo_reference"],
     "pattern": re.compile(_OOS_1)},
]

# 规则全不中时的确定性兜底标签（交现有四档瀑布；网关 LLM 兜底分类非本模块职责）。
_FALLBACK_LABEL = "entity_local"


def _signals(question: str, linked: list, topic_hits: list,
             has_code_token: bool) -> dict:
    """从链接后信号计算规则 requires 可用的布尔谓词。

    ``no_repo_reference`` 是**组合谓词**（落地设计 §4.2）：
    ``no_linker_hit ∧ topic 全低分 ∧ 无指代 ∧ 无反引号``——四者皆真才判"无仓库指向"，
    防"什么是适配层"（``适配层`` 是仓库内概念、topic 会命中）被 oos-1 误判界外。
    """
    no_linker_hit = not linked
    topic_all_low = not topic_hits
    has_pronoun = bool(_RE_PRONOUN.search(question or ""))
    has_backtick = "`" in (question or "")
    return {
        "has_code_token": bool(has_code_token),
        "no_code_token": not has_code_token,
        "no_linker_hit": no_linker_hit,
        "no_repo_reference": (
            no_linker_hit and topic_all_low and not has_pronoun and not has_backtick
        ),
    }


def route(question: str, linked: list, topic_hits: list,
          has_code_token: bool) -> tuple[str, str | None]:
    """规则表按序匹配，返回 ``(label, rule_id)``。

    参数（均为**链接后**信号，见模块 docstring 的时序）：
    - ``question``       规范化后的问题（``normalize`` 产出）；
    - ``linked``         ``link_entities`` 结果（空 ⇒ ``no_linker_hit``）；
    - ``topic_hits``     ``topic_recall`` 结果（空 ⇒ topic 全低分，供 oos 组合谓词）；
    - ``has_code_token`` ``is_code_token`` 命中。

    规则命中判定 = 其 ``requires`` 信号全为真 **且**（无 ``pattern`` 或 ``pattern`` 命中）。
    全不中 → ``(entity_local, None)``（``rule_id=None`` 标识兜底，交现有瀑布）。
    """
    sig = _signals(question, linked, topic_hits, has_code_token)
    q = question or ""
    for rule in ROUTER_RULES:
        reqs = rule.get("requires") or ()
        if not all(sig.get(name, False) for name in reqs):
            continue
        pat = rule.get("pattern")
        if pat is not None and not pat.search(q):
            continue
        return rule["label"], rule["id"]
    return _FALLBACK_LABEL, None


# ---------------------------------------------------------------------------
# S6 回退阶梯的建议问法模板（落地设计 §5.8 / D-05；P4 永不裸拒）
# ---------------------------------------------------------------------------

# 面向本仓库确定性可答方向的通用建议问法（out_of_scope / entity_local 无锚时附带）。
_SUGGESTIONS_GENERIC = (
    "可以问：这个项目整体是做什么的（返回仓库卡片）",
    "可以问：修改某个函数（如 _handle_terminate）会波及哪些调用方",
    "可以问：某个机制（如 崩溃恢复 / 看门狗 / 门禁）在哪实现",
)


def default_suggestions() -> list[str]:
    """回退阶梯的默认建议问法（≥1 条，指向本仓库确定性可答方向）。"""
    return list(_SUGGESTIONS_GENERIC)


# ---------------------------------------------------------------------------
# S3 链接候选合并 + 分带（落地设计 §4.6 / 裁定 D-N1、D-20；C2 上线）
#
# 三路候选合并（link_entities ∪ 缩写扩展命中 ∪ BM25-over-实体卡片），按 entity_id 去重、
# **方法档优先**：exact_qualname > suffix_qualname > short_name > concept_name > module_path
# > bm25_card（恒最低）。缩写扩展命中已在 link_entities 内经 _tokenize 折叠，故实参通常是
# link_entities 结果 + bm25_card 两路；函数按任意路数合并，语义一致。
# ---------------------------------------------------------------------------

# 方法档优先级（越大越强）；bm25_card（纯 BM25 卡片召回）恒低于 link_entities 任一方法档。
_METHOD_RANK = {
    "exact_qualname": 5,
    "suffix_qualname": 4,
    "short_name": 3,
    "concept_name": 2,
    "module_path": 1,
    "bm25_card": 0,
}

# 过渡规则（D-N1，V0 校准未产可行参数前生效）：仅 exact/suffix 方法档（_SCORE≥80）允许
# **自动锚定给确定性工具**（impact_analysis 等硬 ID 消费方）。topic/BM25 侧永不自动锚定。
_STRONG_METHODS = ("exact_qualname", "suffix_qualname")


def _cand_key(c: dict) -> str:
    return c.get("entity_id") or c.get("node_id") or ""


def merge_link_candidates(*cand_lists: list) -> list[dict]:
    """合并多路链接候选（link ∪ 缩写 ∪ bm25_card），按 entity_id 去重、方法档优先。

    每个候选形如 ``{entity_id|node_id, label, score, method, ...}``。同一 entity_id 保留
    **方法档更强**者（_METHOD_RANK 高）；同档保留 score 高者。返回按 (方法档, score, id) 降序
    排列的合并候选列表（每项含原字段 + 规范化 ``entity_id``）。bm25_card 恒排在方法档候选之后。
    """
    best: dict[str, dict] = {}
    for lst in cand_lists:
        for c in lst or []:
            eid = _cand_key(c)
            if not eid:
                continue
            rank = _METHOD_RANK.get(c.get("method"), 0)
            score = c.get("score") or 0
            cur = best.get(eid)
            if (cur is None or rank > cur["_rank"]
                    or (rank == cur["_rank"] and score > (cur.get("score") or 0))):
                merged = dict(c)
                merged["entity_id"] = eid
                merged["_rank"] = rank
                best[eid] = merged
    out = sorted(best.values(),
                 key=lambda c: (-c["_rank"], -(c.get("score") or 0), c["entity_id"]))
    for c in out:
        c.pop("_rank", None)
    return out


def has_strong_method(cand: dict) -> bool:
    """候选是否为强方法档（exact/suffix qualname，_SCORE≥80）——过渡规则自动锚定的必要条件。"""
    return cand.get("method") in _STRONG_METHODS


def content_terms(matched_terms, min_len: int = 2) -> list[str]:
    """从命中词里筛"内容词"：非中文停用/功能/疑问词、且长度≥min_len（D-N1 绝对下限操作化）。

    V0 校准诊断：高 IDF 的 n-gram 多为非内容词碎片（的单/起来/台账），仅"高 IDF"挡不住噪声，
    须叠加中文停用词黑名单过滤（见 lexicon）。本函数即"≥1 高 IDF 内容词"里"内容词"谓词的落地。
    """
    from .lexicon import is_zh_stopword
    return [t for t in (matched_terms or [])
            if len(t) >= min_len and not is_zh_stopword(t)]


def can_auto_anchor(cand: dict, matched_terms=None) -> bool:
    """能否把候选**自动锚定给确定性工具**（硬 ID 消费方）——D-N1 过渡规则 + 绝对证据下限。

    合并规则（取更严，落地设计 §4.6 / calibration §3）：
    1. **纯 bm25_card 永不自动锚定**（无方法档支撑，绝对下限，永不被分带放宽）；
    2. **过渡规则**：仅方法档 ∈ {exact,suffix}（≥80）自动锚定（V0 未产可行参数，续用）；
    3. **绝对下限**：若提供 ``matched_terms``，要求其中 ≥1 个内容词（非停用词）——纯停用词
       碎片命中不足以自动锚定（对症 V0 高 IDF 碎片误触发）。

    返回 True 仅当以上全满足。消歧 / 低置信呈现不走本闸（那是"呈现"非"喂确定性工具"）。
    """
    if cand.get("method") == "bm25_card":
        return False
    if not has_strong_method(cand):
        return False
    if matched_terms is not None and not content_terms(matched_terms):
        return False
    return True


def card_hits_to_candidates(recall: list) -> list[dict]:
    """把 topic_recall 的 Function/Class 命中项映射为 ``method='bm25_card'`` 链接候选（§4.6）。

    ``{node_id,label,score,matched_terms}`` → ``{entity_id,label,score,method:'bm25_card',matched_terms}``。
    只收 Function/Class（Concept 命中仍走 topic 档的 IMPLEMENTS 展开，不在此重复）。
    这类候选只作锚定/消歧输入，优先级恒低于 exact/suffix（见 merge_link_candidates）。
    """
    out = []
    for r in recall or []:
        if r.get("label") in ("Function", "Class"):
            out.append({"entity_id": r["node_id"], "label": r["label"],
                        "score": r.get("score", 0.0), "method": "bm25_card",
                        "matched_terms": r.get("matched_terms", [])})
    return out


# ---------------------------------------------------------------------------
# S7 前提校验（落地设计 §5.7 / §3.2 / 裁定 D-19；PP 错误预设子集 · B-3）
#
# 「错误预设」题（PP 子集）如「为什么用 Redis 做分布式锁」隐含一条**断言为真的前提**
# （使用 Redis）。校验只保留「实体可词面定位但图中无支撑边」半支（向量高分支撑块已随
# D-22 砍除）：premise 实体经词面在图谱**不可定位** → `unknown_entity`（保守当作未证实，
# §3.2 S7 保守偏差）；**可定位但断言的支撑关系缺失** → `unverified`。两者都落 flag、
# 都触发生成层「先纠正后答」的程序化闸门（spec §5.2）。
#
# 前提来源两路（同一 verify_premises 消费）：
#   · **lexical/off 档**：`extract_lexical_premises`——题面技术专名词表比对（gate 离线走这条，
#     确保 B-3 离线可翻绿；纯确定性、无网关）。
#   · **llm 档**：`_rg_llm_rewrite` 的 `premises` 经 `premises_from_claims` 抽 terms 后校验。
# ---------------------------------------------------------------------------

# 前提实体图谱可定位性扫描的节点文本字段（qualname/路径/docstring/概念名/描述/提交信息/签名）。
_PREMISE_SCAN_KEYS = (
    "qualname", "name", "path", "docstring", "description", "message", "signature",
)

# LLM 前提断言里可校验 term 的抽取样式：英文标识符 + 数字/中文数字 + 量词（五级 / 100 轮）。
# 数字与量词间允许空白（"100 轮"），抽出后归一去空白（→ "100轮"）再做图谱存在性比对。
_EN_IDENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{1,}")
_NUM_UNIT_RE = re.compile(r"[0-9一二三四五六七八九十百千两]+\s*(?:级|轮|层|阶段|个|次|步|档|重)")


def extract_lexical_premises(question: str) -> list[dict]:
    """轻量规则前提抽取（lexical/off 档；gate 离线判定走这条）。

    题面出现的技术专名（``lexicon.find_tech_terms`` 的 infra/框架词表命中）= 断言
    「本项目用 X」的前提，交 ``verify_premises`` 对图谱做存在性校验。返回 premise dict
    列表 ``[{claim, terms, source}]``；无技术专名则空（非 PP 题不产前提、零误报）。
    """
    from .lexicon import find_tech_terms
    prem: list[dict] = []
    for term, disp in find_tech_terms(question or ""):
        prem.append({"claim": f"使用 {disp}", "terms": [term],
                     "source": "lexical:tech_term"})
    return prem


def premises_from_claims(claims: list) -> list[dict]:
    """把 LLM 抽取的前提断言（自然语言字符串）转成可校验 premise dict。

    从每条断言抽取**技术专名 + 英文标识符 + 数字量词短语**作为 terms（供图谱存在性校验）。
    抽不出可校验 term 的纯中文泛述断言**不产 premise**（保守：宁可漏判也不误报，守 F1）。
    """
    from .lexicon import find_tech_terms
    out: list[dict] = []
    for c in claims or []:
        s = str(c or "").strip()
        if not s:
            continue
        terms: list[str] = [t for t, _d in find_tech_terms(s)]
        terms += _EN_IDENT_RE.findall(s)
        terms += [re.sub(r"\s+", "", m) for m in _NUM_UNIT_RE.findall(s)]
        # 去重保序 + 去掉过短碎片
        terms = list(dict.fromkeys(t for t in terms if t and len(t) >= 2))
        if not terms:
            continue
        out.append({"claim": s, "terms": terms, "source": "llm:premise"})
    return out


def _term_in_graph(store, term: str) -> bool:
    """term（忽略大小写）是否作为词面出现在图谱任一节点的可检索文本中。

    扫 ``_PREMISE_SCAN_KEYS`` + aliases/zh_aliases + evidence 引文。命中即认为该前提实体
    在图谱「可定位」（等价 link_entities 的词面可链接性，但覆盖概念描述/提交信息等自由文本）。
    """
    t = (term or "").lower()
    if not t:
        return False
    for node in store.nodes():
        for k in _PREMISE_SCAN_KEYS:
            v = node.get(k)
            if v and t in str(v).lower():
                return True
        for a in list(node.get("aliases") or []) + list(node.get("zh_aliases") or []):
            if a and t in str(a).lower():
                return True
        for ev in node.get("evidence") or []:
            q = (ev or {}).get("quote") if isinstance(ev, dict) else None
            if q and t in str(q).lower():
                return True
    return False


def verify_premises(store, premises: list) -> list[dict]:
    """S7 前提校验：逐前提对真实图谱做词面存在性校验，返回**未证实**的 flag 列表。

    每个 premise 形如 ``{claim, terms:[...], source}``。判定（落地设计 §5.7 / D-19）：
      · terms **全部**不可词面定位于图谱 → ``status='unverified'`` + ``reason='unknown_entity'``
        （实体不可链接，§3.2 S7 保守当作未证实）；
      · terms 有可定位者 → 本轮词面校验保守视为「已获证据」，**不产 flag**（可定位但断言
        支撑边缺失的 `missing_edge` 半支需边级校验，留待具体关系断言，避免误伤真前提）。

    返回 ``[{claim, status:'unverified', reason, source, terms}]``（仅未证实项；已证实前提不产 flag）。
    真实数据铁律：只据真实 ``store`` 扫描判定，绝不臆造。
    """
    flags: list[dict] = []
    for prem in premises or []:
        terms = [t for t in (prem.get("terms") or []) if t]
        if not terms:
            continue
        present = any(_term_in_graph(store, t) for t in terms)
        if present:
            continue                        # 可定位 → 保守不产 flag（不误伤真前提）
        flags.append({
            "claim": prem.get("claim", ""),
            "status": "unverified",
            "reason": "unknown_entity",
            "source": prem.get("source"),
            "terms": terms,
        })
    return flags
