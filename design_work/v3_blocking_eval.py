"""V3 概念对齐 blocking 替代实测（Phase B · D-P2）。

背景：生产对齐 blocking = 精确 norm_key（semantic.py:_align + _norm_key，仅规范化名
完全相等才并组），是最弱 blocking。本实验对比两个替代 blocking：
  (a) 词面 blocking：norm_key 归一 + 字符 bigram Jaccard ≥ τ  —— 纯 stdlib
  (b) 索引期一次性离线 API embedding blocking —— 需 embeddings 端点

matching 层（LLM 裁决同义与否）两案均不变；blocking 只决定「哪些对送裁决」。
判定规则（计划书 §3 V3）：召回差 ≤10% 取 (a)；否则取 (b)。

产物（UTF-8，可复现）：
  design_work/v3_concepts_dump.txt   —— 139 概念名（人工标 gold 用）
  design_work/v3_pairs_ranked.txt    —— 全对 bigram Jaccard 降序（供 gold 复核）
  design_work/v3_blocking_eval.json  —— 指标全表
密钥仅入内存/请求头，全程写作 sk-****，不落任何产物。
"""
import json, re, itertools, sys, urllib.request, urllib.error

ROOT = ".."
GRAPH = "output/graph.json" if False else "../output/graph.json"


def load_concepts():
    d = json.load(open("../output/graph.json", encoding="utf-8"))
    return [n["name"] for n in d["nodes"] if n["label"] == "Concept" and n.get("name")]


def norm_key(name: str) -> str:
    # 复刻 semantic._norm_key：小写、去空格与连字符
    return re.sub(r"[\s\-]+", "", name.strip().lower())


def bigrams(s: str) -> set:
    s = norm_key(s)
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i+2] for i in range(len(s) - 1)}


def jaccard(a: str, b: str) -> float:
    ga, gb = bigrams(a), bigrams(b)
    if not ga or not gb:
        return 0.0
    inter = len(ga & gb)
    uni = len(ga | gb)
    return inter / uni if uni else 0.0


def dump():
    cons = load_concepts()
    with open("v3_concepts_dump.txt", "w", encoding="utf-8") as f:
        for i, c in enumerate(sorted(cons)):
            f.write(f"{i:3d}\t{c}\n")
    # 全对 Jaccard 降序（只写 >=0.15 的，供人工复核 gold）
    pairs = []
    for a, b in itertools.combinations(sorted(set(cons)), 2):
        j = jaccard(a, b)
        if j >= 0.15:
            pairs.append((j, a, b))
    pairs.sort(reverse=True)
    with open("v3_pairs_ranked.txt", "w", encoding="utf-8") as f:
        f.write(f"# 全对 bigram Jaccard >=0.15，共 {len(pairs)} 对（概念总数 {len(cons)}）\n")
        for j, a, b in pairs:
            f.write(f"{j:.3f}\t{a}\t||\t{b}\n")
    print("DUMP_DONE concepts=%d pairs_ge_0.15=%d" % (len(cons), len(pairs)))


def probe_embedding():
    """真探针：网关是否有 embeddings 端点。密钥只入请求头，不打印。"""
    cfg = json.load(open("../../claude-ui/config.json", encoding="utf-8"))
    base = cfg["anthropic_base_url"].rstrip("/")
    tok = cfg["anthropic_auth_token"]
    results = {}
    for path in ["/v1/embeddings", "/embeddings"]:
        url = base + path
        body = json.dumps({"model": "text-embedding-v3", "input": "测试"}).encode()
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer " + tok)
        req.add_header("anthropic-version", "2023-06-01")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                txt = r.read(400).decode("utf-8", "replace")
                results[path] = {"status": r.status, "ok": True, "body_head": txt[:200]}
        except urllib.error.HTTPError as e:
            eb = e.read(300).decode("utf-8", "replace")
            results[path] = {"status": e.code, "ok": False, "body_head": eb[:200]}
        except Exception as e:
            results[path] = {"status": None, "ok": False, "error": type(e).__name__ + ":" + str(e)[:120]}
    # 掩码：确保任何 sk- 泄漏被替换（防御性）
    dump = json.dumps(results, ensure_ascii=False)
    dump = re.sub(r"sk-[A-Za-z0-9_\-]+", "sk-****", dump)
    print("EMBED_PROBE", dump)
    return json.loads(dump)


# ---- Gold 同义概念对（收口 agent 人工语义标注，全部取自真实 139 概念名；披露判断）----
# 正样本 = 同一底层概念的不同表面名（blocking 应把二者送入 matching 层裁决）。
# 含表面相似(高 Jaccard)与表面相异(低 Jaccard，跨语言/改述)两类，避免偏向词面案。
GOLD_POSITIVES = [
    ("多线程 workspace 运行", "多线程workspace"),
    ("worktree 隔离", "worktree 隔离装配"),
    ("玻璃感 Web 控制台", "玻璃感 Web 控制台网关"),
    ("用户界面 CLI", "用户界面 CLI 子集"),
    ("验证钩子", "验证钩子模块"),
    ("CLI §12 typer 骨架命令子集", "CLI §12 子集"),
    ("events端点 artifacts/bb_ops 投影", "events端点投影补全"),
    ("ChaosHarness mock层与注入点", "混沌 harness"),          # 跨语言，较难
    ("50 轮硬门槛测试", "混沌 50 轮 100% 硬门槛"),
    ("render四层视图组装", "视图组装"),
    ("启动时对线程机械执行崩溃恢复", "崩溃恢复算法"),          # 改述，较难
    ("async核心环", "异步版核心环"),
    ("适配层", "适配层统一 invoke 接口"),                      # 较难
    ("异步作业", "长作业真异步"),                              # 较难
    ("停机重启", "停机-重启-approve-terminate控制流"),         # 最难，表面重合最低
]
# 负样本 = 词面高相似但不同概念（测 blocking 误纳；matching 层负责最终剔除）。
GOLD_NEGATIVES = [
    ("ApiAdapter", "CliAdapter"),
    ("CliAdapter", "FakeCliAdapter"),
    ("ApiAdapter", "FakeApiAdapter"),
    ("M3冻结接口契约", "M4冻结接口契约"),
    ("FakeApiAdapter", "FakeCliAdapter"),
]


def evaluate():
    cons = set(load_concepts())
    # 校验 gold 名全部真实存在
    missing = [p for pair in (GOLD_POSITIVES + GOLD_NEGATIVES) for p in pair if p not in cons]
    if missing:
        print("GOLD_MISSING", json.dumps(missing, ensure_ascii=False))
        sys.exit(1)

    def exact_key_block(a, b):   # 生产现状 blocking
        return norm_key(a) == norm_key(b)

    pos = [(a, b, jaccard(a, b), exact_key_block(a, b)) for a, b in GOLD_POSITIVES]
    neg = [(a, b, jaccard(a, b), exact_key_block(a, b)) for a, b in GOLD_NEGATIVES]

    taus = [0.15, 0.20, 0.25, 0.30, 0.35]
    n_all_pairs = len(cons) * (len(cons) - 1) // 2
    # 全对 Jaccard（算候选数=成本代理）
    all_j = [jaccard(a, b) for a, b in itertools.combinations(sorted(cons), 2)]

    sweep = []
    for t in taus:
        pos_hit = sum(1 for *_, j, _ in [(a, b, jaccard(a, b), None) for a, b in GOLD_POSITIVES] if j >= t)
        recall = pos_hit / len(GOLD_POSITIVES)
        neg_admit = sum(1 for a, b in GOLD_NEGATIVES if jaccard(a, b) >= t)
        cand = sum(1 for j in all_j if j >= t)
        sweep.append({"tau": t, "pos_recall": round(recall, 3), "pos_hit": pos_hit,
                      "neg_admitted": neg_admit, "candidate_pairs": cand,
                      "candidate_frac": round(cand / n_all_pairs, 4)})

    exact_recall = sum(1 for *_, e in pos if e) / len(GOLD_POSITIVES)

    out = {
        "concepts": len(cons),
        "all_pairs": n_all_pairs,
        "gold_positives": len(GOLD_POSITIVES),
        "gold_negatives": len(GOLD_NEGATIVES),
        "embedding_endpoint": "404 (无 /v1/embeddings 与 /embeddings；grok 402 断供) → 索引期 embedding blocking 在 as-built 不可运行",
        "baseline_exact_norm_key_recall": round(exact_recall, 3),
        "lexical_bigram_jaccard_sweep": sweep,
        "positive_detail": [{"a": a, "b": b, "jaccard": round(j, 3), "exact_key": e} for a, b, j, e in pos],
        "negative_detail": [{"a": a, "b": b, "jaccard": round(j, 3)} for a, b, j, _ in neg],
    }
    with open("v3_blocking_eval.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    # ASCII-safe 摘要到终端
    print("EVAL_DONE concepts=%d all_pairs=%d gold_pos=%d gold_neg=%d"
          % (out["concepts"], out["all_pairs"], out["gold_positives"], out["gold_negatives"]))
    print("baseline_exact_key_recall=%.3f" % exact_recall)
    for s in sweep:
        print("tau=%.2f pos_recall=%.3f (%d/%d) neg_admitted=%d cand_pairs=%d (%.2f%%)"
              % (s["tau"], s["pos_recall"], s["pos_hit"], len(GOLD_POSITIVES),
                 s["neg_admitted"], s["candidate_pairs"], s["candidate_frac"] * 100))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dump"
    if cmd == "dump":
        dump()
    elif cmd == "probe":
        probe_embedding()
    elif cmd == "eval":
        evaluate()
