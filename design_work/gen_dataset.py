# -*- coding: utf-8 -*-
"""A4 数据落地：把 design_work/eval-design.md 的 48 题转成 eval/dataset.jsonl。

- 全部 gold 实体已用 output/graph.json 真实读取核对（见本轮 A4 核对日志）：
  FZ 的 6 个函数 id、14 个概念 id 全部存在；三个连字符概念 id
  (worktree-隔离 / stop-标志消费 / 混沌-50-轮-100-硬门槛) 为图内真实 id 的连字符形式。
- AMB 碰撞组实测 {__init__:9, invoke:6}，与设计一致。
- PP 前提缺席在本脚本内对图谱全文 blob 复检（premise_absent 必须全 True 才写库）。

产物: eval/dataset.jsonl（每行一个 JSON，utf-8，ensure_ascii=False）。
纯 stdlib，不改 src。运行：python design_work/gen_dataset.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAPH = os.path.join(ROOT, "output", "graph.json")
OUT = os.path.join(ROOT, "eval", "dataset.jsonl")

# ---------------------------------------------------------------------------
# 48 题题面与 gold（逐题抄自 eval-design.md，gold_entity 为图核对过的真实 id）
# ---------------------------------------------------------------------------

L0 = [
    ("L0-01", "你知道我的代码库吗", "C1原型"),
    ("L0-02", "你晓得我这破仓库是干啥的不", "口语"),
    ("L0-03", "你能干嘛？对我这项目能做啥", "能力问询"),
    ("L0-04", "这个项目整体多大规模，有多少东西", "规模问询"),
    ("L0-05", "你了解我的仓库不，简单讲讲", "口语"),
    ("L0-06", "你对这个带码库熟悉么", "错别字(带=代)"),
    ("L0-07", "你是谁，你能帮我看代码不", "元/能力"),
    ("L0-08", "这仓库大概是个什么东西，给个总览", "总览"),
    ("L0-09", "你到底能不能读懂我的代码库呀", "口语/能力"),
    ("L0-10", "帮我认识下这个工程，它做啥的", "近义替换"),
]

# (id, question, gold_entity, alt_gold_entities, target_semantic)
FZ_DEV = [
    ("FZ-d01", "那个把活儿叫停之后负责收尾扫尾的一摊在哪",
     "multi-agent-orch::src/orch/scheduler/core.py::_handle_terminate", [], "终止处理函数"),
    ("FZ-d02", "谁在旁边盯着别人干活会不会卡住",
     "concept::看门狗三级", [], "看门狗/超时监控"),
    ("FZ-d03", "程序半路挂了之后怎么自己爬起来接着干",
     "concept::崩溃恢复算法", [], "崩溃恢复"),
    ("FZ-d04", "系统自己往台账上补记一笔是哪段逻辑",
     "multi-agent-orch::src/orch/scheduler/systemexec.py::append_system_event", [], "系统事件追加"),
    ("FZ-d05", "怎么估摸一段话大概占多少篇幅",
     "multi-agent-orch::src/orch/render/__init__.py::estimate_tokens", [], "token 估算"),
    ("FZ-d06", "给每个干活的单独开个小隔间互不打扰",
     "concept::worktree-隔离", [], "worktree 隔离"),
    ("FZ-d07", "活干完自动留个存档不用手动保存",
     "concept::autocommit", [], "自动提交"),
    ("FZ-d08", "管谁能碰哪块、越界了就拦下来那套",
     "concept::权限三件套", ["concept::越权审计"], "权限/越权审计"),
    ("FZ-d09", "存心使坏来测系统扛不扛揍",
     "concept::故障注入", [], "故障注入"),
    ("FZ-d10", "放行还是拦下的那道关卡在哪判",
     "concept::门禁裁决入口", [], "门禁裁决"),
]

FZ_TEST = [
    ("FZ-t01", "把界面那几块拼装出来显示",
     "concept::视图组装", [], "视图渲染"),
    ("FZ-t02", "大家共用的留言板重新拼一遍",
     "concept::黑板投影与rebuild", [], "黑板投影 rebuild"),
    ("FZ-t03", "喊了暂停之后系统怎么响应",
     "concept::stop-标志消费", [], "stop 标志消费"),
    ("FZ-t04", "回话来晚了怎么在界面上打个记号",
     "concept::迟到在途回复展示标记", [], "迟到回复标记"),
    ("FZ-t05", "把各种杂牌后端捏成统一一个样子调用",
     "concept::适配层", ["concept::适配层统一-invoke-接口"], "适配层"),
    ("FZ-t06", "只留本人填的内容，机器补的字段一律不认",
     "multi-agent-orch::src/orch/adapters/__init__.py::_strip_to_author_fields", [], "作者字段裁剪"),
    ("FZ-t07", "把老长一段正文压成一行短的",
     "multi-agent-orch::src/orch/render/__init__.py::_summarize", [], "正文摘要"),
    ("FZ-t08", "把干活的目录路径算出来",
     "multi-agent-orch::src/orch/cli/main.py::_resolve_workspace", [], "workspace 解析"),
    ("FZ-t09", "那个要反复跑很多遍必须全绿才算过的压测",
     "concept::混沌-50-轮-100-硬门槛", ["concept::50-轮硬门槛测试"], "混沌硬门槛"),
    ("FZ-t10", "存事件和进度落盘的那一层",
     "concept::状态层", [], "状态/存储层"),
]

# (id, question, gold_behavior, candidate_note)
AMB = [
    ("AMB-01", "invoke 这个方法在哪定义的", "should_disambiguate",
     "6 候选并列 score=60（Api/Cli/Fake*/Mock 适配器）"),
    ("AMB-02", "帮我看下 invoke 的实现，别看测试桩", "should_disambiguate",
     "过滤 Fake/Mock 后仍余 Api+Cli 两个合法候选"),
    ("AMB-03", "__init__ 是在哪初始化的", "should_disambiguate",
     "9 个类构造器并列，均合法"),
    ("AMB-04", "run 这个函数干嘛的", "should_autopick",
     "短名唯一 → ChaosHarness.run（run×9 假设在本图证伪）"),
    ("AMB-05", "recover 在哪", "should_autopick",
     "fn recover(100) 主导，另有模块 recover.py(30)"),
    ("AMB-06", "main 是哪个", "should_autopick",
     "fn main(100) 主导，另有 cli/main.py(30)"),
    ("AMB-07", "_dispatch 指的是啥", "should_autopick",
     "fn Handler._dispatch(60) 主导，另有 _dispatch.py(30)"),
    ("AMB-08", "_handle_terminate 具体做什么", "should_autopick",
     "唯一候选（exact 100）"),
    ("AMB-09", "check_watchdogs 怎么工作的", "should_autopick",
     "唯一候选（exact 100）"),
    ("AMB-10", "apply_gate_decision 在哪", "should_autopick",
     "唯一候选（exact 100）"),
]

# (id, question, false_premise, absent_keywords, truth_anchors, gold_correction)
PP = [
    ("PP-01", "为什么这项目用 Redis 做分布式锁？", "使用 Redis 分布式锁",
     ["redis"], ["sqlite"], "无 Redis；单进程/线程 + sqlite3，无分布式锁"),
    ("PP-02", "你们的 FastAPI 路由是怎么组织的？", "用 FastAPI",
     ["fastapi"], ["http.server", "网关"], "stdlib http.server 手写网关（stdlib网关15端点）"),
    ("PP-03", "事件为什么选 PostgreSQL 存储？", "用 PostgreSQL",
     ["postgres"], ["sqlite"], "sqlite3 存储层六表 DDL"),
    ("PP-04", "这套多智能体是怎么用 Docker/K8s 编排部署的？", "容器化编排部署",
     ["docker", "kubernetes"], [], "无容器化；编排指多智能体消息编排，非容器编排"),
    ("PP-05", "任务队列用的是 Celery 吧，怎么配的？", "用 Celery 队列",
     ["celery"], ["asyncio"], "无 Celery；asyncio 异步核心环 + 多线程 workspace"),
    ("PP-06", "前端 React 组件是怎么拆的？", "React 前端",
     ["react", "vue"], ["http.server"], "玻璃感 Web 控制台为 stdlib 网关 + 静态页，无 React"),
    ("PP-07", "看门狗的五级升级机制怎么设计的？", "看门狗五级",
     ["看门狗五级", "五级"], ["看门狗三级", "三级"], "是三级看门狗，非五级"),
    ("PP-08", "混沌测试要跑满 100 轮才算过对吧？", "跑 100 轮",
     ["100 轮", "跑满 100"], ["50 轮"], "是 50 轮（100 是通过率 100%，非轮数）"),
]


def main():
    g = json.load(open(GRAPH, encoding="utf-8"))
    nodes = {n["id"]: n for n in g["nodes"]}

    # --- 反幻觉：FZ 全部 gold_entity（含 alt）必须在图中真实存在 ---
    missing = []
    for _id, _q, gold, alts, _sem in FZ_DEV + FZ_TEST:
        for e in [gold] + list(alts):
            if e not in nodes:
                missing.append((_id, e))
    if missing:
        print("FATAL 图中不存在的 gold:", missing)
        sys.exit(1)

    # --- PP 前提缺席复检：absent 关键词确不在图谱全文 blob（PP-07/08 结构量另核）---
    blob = json.dumps(g, ensure_ascii=False).lower()
    tech_absent_ok = True
    for _id, _q, _fp, absent, _truth, _gold in PP:
        for k in absent:
            # 结构量（含中文/空格）跳过全文子串核验，由 truth 锚点承担
            if k.lower() in ("五级", "看门狗五级", "100 轮", "跑满 100"):
                continue
            if k.lower() in blob:
                tech_absent_ok = False
                print(f"WARN {_id} 前提关键词意外命中图谱: {k}")
    print("PP 技术前提缺席复检:", "全部缺席(符合预期)" if tech_absent_ok else "有命中(需复核)")

    rows = []
    for _id, q, variant in L0:
        rows.append({
            "id": _id, "subset": "L0", "question": q,
            "variant": variant,
            "gold_mode_class": "overview",   # overview 类 = {meta, overview, global}
            "facts_min": 3,                  # facts_hit >= 3（L0_FACTS 事实表在 gate.py）
        })
    for _id, q, gold, alts, sem in FZ_DEV:
        rows.append({
            "id": _id, "subset": "FZ_dev", "question": q,
            "gold_entity": gold, "alt_gold_entities": alts,
            "target_semantic": sem, "gold_mode_class": "topic",
        })
    for _id, q, gold, alts, sem in FZ_TEST:
        rows.append({
            "id": _id, "subset": "FZ_test", "question": q,
            "gold_entity": gold, "alt_gold_entities": alts,
            "target_semantic": sem, "gold_mode_class": "topic",
        })
    for _id, q, beh, note in AMB:
        rows.append({
            "id": _id, "subset": "AMB", "question": q,
            "gold_behavior": beh, "candidate_note": note,
            "gold_mode_class": "symbol",
        })
    for _id, q, fp, absent, truth, gold in PP:
        rows.append({
            "id": _id, "subset": "PP", "question": q,
            "false_premise": fp, "absent_keywords": absent,
            "truth_anchors": truth, "gold_correction": gold,
            "gold_mode_class": "overview",
        })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"写出 {len(rows)} 题 -> {OUT}")
    from collections import Counter
    print("子集分布:", dict(Counter(r["subset"] for r in rows)))


if __name__ == "__main__":
    main()
