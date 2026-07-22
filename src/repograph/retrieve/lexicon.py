"""检索侧共享词表（v0.3 · Phase C2/C3）——中文停用/疑问/功能词 + 代码缩写扩展 + 技术专名词表。

落地设计 §4.4 + `eval/calibration.md` §3.3 对 D-N1 的修订：V0 校准诊断显示 FZ-dev 每题
Top-1 恒为**非 gold 的高 IDF 非内容词碎片**（n-gram：`的单/起来/台账/放行`；jieba：`把/在/挂/拦`）。
"仅高 IDF"不足以挡噪声——必须把「内容词」落为程序谓词：**中文停用/功能/疑问词黑名单过滤**。
本模块把该黑名单与代码缩写扩展表集中一处，供 ``topic``（查询侧去噪）与 ``context._tokenize``
（缩写双向扩展）共用，避免两处分叉。

设计取舍（诚实标注）：
- **停用词只作用于查询侧 n-gram**（``topic_recall`` 的 ``q_terms``、``router`` 的证据下限判定），
  **不改语料索引**（``build_corpus_index`` 仍用原始 ``zh_terms``）——保持 V1 自校验「base 语料
  BM25 == 真实 topic_recall」的可复现性；语料里的停用碎片只要查询不再匹配它们即失效，无需删档。
- 黑名单**保守取词**：只收明确的疑问/指代/功能/填充词，不收可能承载语义的实词（如 `之后/负责/
  台账`），避免误伤召回。词表每次增删须复跑 gate 验证 FZ 无回归。

只依赖标准库；不 import 任何其它 repograph 模块（纯词表，可被 topic/context/router 复用）。
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 中文停用/疑问/功能/填充词黑名单（作用于查询侧 n-gram term）
#
# 全部为 2–4 字的连续片段；``topic``/``context`` 产出的 n-gram term 若**整体等于**表中任一
# 词即被丢弃（不做子串包含判定，避免误删含停用字的实词，如「终止」不因含「止」被删）。
# ---------------------------------------------------------------------------

# 疑问词（问法本身，不携带检索信号）
_ZH_STOP_QUESTION = frozenset({
    "怎么", "怎样", "咋", "咋弄", "咋整", "如何", "为什么", "为何", "什么", "啥",
    "哪个", "哪些", "哪块", "哪里", "哪儿", "多少", "几个", "是不是", "是否",
    "有没有", "能不能", "会不会", "要不要", "到底", "究竟", "干嘛", "干啥",
    "怎么办", "怎么样", "什么样", "是啥", "干什么",
})

# 指代/指示词（"这个/那块/它"等，无实体落点）
_ZH_STOP_DEIXIS = frozenset({
    "这个", "那个", "这块", "那块", "这里", "那里", "这儿", "那儿", "它们",
    "他们", "这种", "那种", "某个", "某种", "一个", "一种", "一摊", "一块",
    "这套", "那套", "这道", "那道", "这段", "那段", "上面", "下面", "前面",
    "后面", "上述", "该项", "此处", "本处",
})

# 功能/填充/助词碎片（含 V0 校准点名的 `起来` 一类非内容词补语）
_ZH_STOP_FUNC = frozenset({
    "起来", "出来", "下来", "上去", "过来", "过去", "进去", "开来",
    "的话", "一下", "一样", "一律", "一并", "一遍", "一笔", "一番",
    "的那", "的时", "时候", "就是", "还是", "或者", "然后", "接着",
    "东西", "玩意", "家伙", "帮我", "帮忙", "我的", "你的", "咱的", "我这",
    "别人", "各种", "所有", "全部", "整个", "那么", "这么", "多大",
})

# 汇总黑名单（查询侧过滤用）
ZH_STOPWORDS: frozenset = _ZH_STOP_QUESTION | _ZH_STOP_DEIXIS | _ZH_STOP_FUNC


def is_zh_stopword(term: str) -> bool:
    """term 是否为中文停用/疑问/功能词（整词匹配，供 router 证据下限判定复用）。"""
    return term in ZH_STOPWORDS


def filter_stopwords(terms):
    """从 term 序列滤除中文停用词（保序、保重复的非停用词，承载词频）。"""
    return [t for t in terms if t not in ZH_STOPWORDS]


# ---------------------------------------------------------------------------
# 代码缩写扩展表（落地设计 §3.1 表行5 / §4.4）——双向：ctx↔context
#
# 作用于 ``context._tokenize`` 的英文标识符候选：问题里出现缩写（或全称）时，额外把对侧
# 形态也加入候选，命中 qualname/别名。纯字符串操作、无语义。全小写键值，匹配时忽略大小写。
# ---------------------------------------------------------------------------

# 单向"规范"表：缩写 → 全称。构造时自动补全反向，得到双向等价类。
_ABBREV_CANON = {
    "ctx": "context",
    "cfg": "config",
    "conf": "config",
    "impl": "implementation",
    "repo": "repository",
    "msg": "message",
    "req": "request",
    "resp": "response",
    "res": "result",
    "err": "error",
    "exec": "execute",
    "init": "initialize",
    "auth": "authentication",
    "db": "database",
    "addr": "address",
    "arg": "argument",
    "param": "parameter",
    "buf": "buffer",
    "tmp": "temp",
    "calc": "calculate",
    "gen": "generate",
    "val": "value",
    "num": "number",
    "idx": "index",
    "dir": "directory",
    "func": "function",
    "fn": "function",
    "cls": "class",
    "obj": "object",
    "str": "string",
    "dict": "dictionary",
    "doc": "document",
    "sess": "session",
    "ws": "workspace",
    "concat": "concatenate",
    "attr": "attribute",
    "sync": "synchronize",
    "async": "asynchronous",
    "recv": "receive",
    "eval": "evaluate",
    "info": "information",
    "stat": "statistics",
    "stats": "statistics",
}


def _build_bidirectional(canon: dict) -> dict:
    """把 缩写→全称 单向表补成双向等价映射：token → {对侧形态...}（不含自身）。"""
    out: dict[str, set[str]] = {}
    for abbr, full in canon.items():
        out.setdefault(abbr, set()).add(full)
        out.setdefault(full, set()).add(abbr)
    return out


# token（全小写）→ 其等价扩展集合（不含自身）
ABBREVIATIONS: dict = _build_bidirectional(_ABBREV_CANON)


def expand_abbreviations(token: str) -> set:
    """返回 token 的缩写/全称对侧形态集合（忽略大小写；无扩展则空集）。

    仅对**整词**扩展（``ctx`` → ``{context}``），不拆分复合标识符——复合词的逐段
    扩展由 ``_tokenize`` 的分段候选自然覆盖，此处只补整词等价，避免组合爆炸。
    """
    if not token:
        return set()
    return set(ABBREVIATIONS.get(token.lower(), ()))


# ---------------------------------------------------------------------------
# 技术专名词表（v0.3 · Phase C3 · 前提校验 S7 / 裁定 D-19）
#
# 常见基础设施 / 框架 / 组件专名——问题里出现这类专名即等价于**断言「本项目用 X」**。
# ``router.verify_premises`` 拿这些命中词逐一对真实图谱做存在性校验：图谱里查无此词 →
# 该前提「未获图谱证据」（PP 错误预设子集：Redis/FastAPI/Docker… 等缺席技术栈）。
#
# **通用、非数据集耦合**：词表是领域通用的技术命名词典（任何仓库问到未用到的 X 都会被标）；
# 是否成 flag 由**图谱存在性**决定，不硬编码任何题目。全小写键 → 展示名（用于 claim 文案）。
# ---------------------------------------------------------------------------

_TECH_TERMS: dict = {
    # 缓存 / KV / 消息 / 队列
    "redis": "Redis", "memcached": "Memcached", "kafka": "Kafka",
    "rabbitmq": "RabbitMQ", "celery": "Celery", "zeromq": "ZeroMQ",
    "nats": "NATS", "pulsar": "Pulsar",
    # 关系 / 文档数据库
    "postgresql": "PostgreSQL", "postgres": "PostgreSQL", "mysql": "MySQL",
    "mariadb": "MariaDB", "mongodb": "MongoDB", "cassandra": "Cassandra",
    "elasticsearch": "Elasticsearch", "clickhouse": "ClickHouse",
    "sqlite": "SQLite",            # 本仓库实际在用 → 图谱命中 → 不成 flag（存在性把关）
    # Web / RPC 框架
    "fastapi": "FastAPI", "flask": "Flask", "django": "Django",
    "tornado": "Tornado", "sanic": "Sanic", "aiohttp": "aiohttp",
    "graphql": "GraphQL", "grpc": "gRPC", "thrift": "Thrift",
    # 前端框架
    "react": "React", "vue": "Vue", "angular": "Angular", "svelte": "Svelte",
    "jquery": "jQuery", "webpack": "Webpack", "vite": "Vite",
    # 容器 / 编排 / 部署
    "docker": "Docker", "kubernetes": "Kubernetes", "k8s": "Kubernetes",
    "nginx": "Nginx", "apache": "Apache", "terraform": "Terraform",
    "ansible": "Ansible", "helm": "Helm",
    # 计算 / 数据 / ML
    "spark": "Spark", "hadoop": "Hadoop", "flink": "Flink", "airflow": "Airflow",
    "tensorflow": "TensorFlow", "pytorch": "PyTorch", "numpy": "NumPy",
    # 云 / 其它
    "kubectl": "kubectl", "prometheus": "Prometheus", "grafana": "Grafana",
    "consul": "Consul", "etcd": "etcd", "zookeeper": "ZooKeeper",
}


def find_tech_terms(text: str) -> list:
    """从文本里找出提到的技术专名，返回 ``[(小写词, 展示名)]``（去重、保序）。

    latin 词按**词边界**匹配（``(?<![a-z0-9])term(?![a-z0-9])``），避免 ``react`` 命中
    ``reaction``、``spark`` 命中 ``sparkle``；含数字的专名（``k8s``）同规则。纯字符串、
    忽略大小写、无语义。命中即候选前提，是否成 flag 由图谱存在性最终裁决（见 router）。
    """
    if not text:
        return []
    low = text.lower()
    out: list = []
    seen: set = set()
    for term, disp in _TECH_TERMS.items():
        if term in seen:
            continue
        if re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", low):
            seen.add(term)
            out.append((term, disp))
    return out
