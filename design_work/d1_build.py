# -*- coding: utf-8 -*-
"""D1 主评测集生成脚本（记录 L2 闭包/调用链等 gold 的生成口径）。

产物：eval/dataset_main.jsonl（60 题，L1 20 / L2 30 / L3 10，dev/test 各半）。
gold 全部由 _d1_graph.Graph.derive(recipe) 从真实图谱确定性派生；本脚本同时执行
存在性 + 题面泄漏（答案实体名不得出现在题面）自检。独立复核见 d1_goldcheck.py。

运行：python design_work/d1_build.py
"""
from __future__ import annotations
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
import _d1_graph as G

OUT = os.path.join(os.path.dirname(__file__), "..", "eval", "dataset_main.jsonl")

# 题面自然语言 + recipe。subject=被问主体（允许出现在题面，泄漏检查豁免）。
# gold 由 recipe 派生，绝不手写实体清单。
QUESTIONS = [
 # ================= L1 一跳事实（20；dev10/test10）=================
 # -- fn -> 所属模块（gold=Module）--
 {"id":"L1-01","split":"dev","difficulty":"easy","variant":"正式·定义定位",
  "question":"函数 _resolve_workspace 是在哪个源码文件里定义的？",
  "subject":[("fn","_resolve_workspace")],"recipe":{"op":"fn_module","args":{"fn":"_resolve_workspace"}}},
 {"id":"L1-02","split":"dev","difficulty":"easy","variant":"口语·定义定位",
  "question":"帮我定位下 _git 这个函数，它到底落在哪个模块文件里？",
  "subject":[("fn","_git")],"recipe":{"op":"fn_module","args":{"fn":"_git"}}},
 {"id":"L1-03","split":"test","difficulty":"easy","variant":"正式·定义定位",
  "question":"append_system_event 归属于哪个 .py 文件？",
  "subject":[("fn","append_system_event")],"recipe":{"op":"fn_module","args":{"fn":"append_system_event"}}},
 {"id":"L1-04","split":"test","difficulty":"med","variant":"正式·方法定位",
  "question":"Store 类的 _begin 方法写在哪个模块文件中？",
  "subject":[("fn","Store._begin")],"recipe":{"op":"fn_module","args":{"fn":"Store._begin"}}},
 # -- fn -> 直接调用方（gold=callers）--
 {"id":"L1-05","split":"dev","difficulty":"easy","variant":"正式·直接调用方",
  "question":"有哪些函数会直接调用 _strip_to_author_fields？",
  "subject":[("fn","_strip_to_author_fields")],"recipe":{"op":"fn_callers","args":{"fn":"_strip_to_author_fields"}}},
 {"id":"L1-06","split":"dev","difficulty":"med","variant":"口语·直接调用方",
  "question":"谁直接调了 _stop_marker_path 这个函数？把直接调用方列一下。",
  "subject":[("fn","_stop_marker_path")],"recipe":{"op":"fn_callers","args":{"fn":"_stop_marker_path"}}},
 {"id":"L1-07","split":"test","difficulty":"easy","variant":"正式·直接调用方",
  "question":"_apply_gate 的直接调用方是哪几个？",
  "subject":[("fn","_apply_gate")],"recipe":{"op":"fn_callers","args":{"fn":"_apply_gate"}}},
 {"id":"L1-08","split":"test","difficulty":"med","variant":"正式·直接调用方",
  "question":"Store._set_status 被哪些方法直接调用？",
  "subject":[("fn","Store._set_status")],"recipe":{"op":"fn_callers","args":{"fn":"Store._set_status"}}},
 # -- 概念 -> 实现函数（gold=Function）--
 {"id":"L1-09","split":"dev","difficulty":"med","variant":"口语·概念落点",
  "question":"崩溃恢复这套算法，具体是靠哪些函数落地的？",
  "subject":[("concept","concept::崩溃恢复算法")],"recipe":{"op":"concept_impl_fns","args":{"concept":"concept::崩溃恢复算法"}}},
 {"id":"L1-10","split":"dev","difficulty":"med","variant":"正式·概念落点",
  "question":"协议层的字段与发送资格校验，分别由哪些函数实现？",
  "subject":[("concept","concept::协议层")],"recipe":{"op":"concept_impl_fns","args":{"concept":"concept::协议层"}}},
 {"id":"L1-11","split":"test","difficulty":"med","variant":"口语·概念落点",
  "question":"把视图拼装出来这件事，落在哪几个函数上？",
  "subject":[("concept","concept::视图组装")],"recipe":{"op":"concept_impl_fns","args":{"concept":"concept::视图组装"}}},
 {"id":"L1-12","split":"test","difficulty":"med","variant":"口语·概念落点",
  "question":"消费停机标志那段逻辑，是哪些函数在干？",
  "subject":[("concept","concept::stop-标志消费")],"recipe":{"op":"concept_impl_fns","args":{"concept":"concept::stop-标志消费"}}},
 # -- 提交 -> 改动函数（gold=Function）--
 {"id":"L1-13","split":"dev","difficulty":"easy","variant":"正式·提交改动",
  "question":"提交 9ae57374 到底改了哪些函数？",
  "subject":[("commit","9ae57374")],"recipe":{"op":"commit_mod_fns","args":{"commit":"9ae57374"}}},
 {"id":"L1-14","split":"dev","difficulty":"med","variant":"正式·提交改动",
  "question":"哈希 d554fa2f 那次提交动了哪几个函数？",
  "subject":[("commit","d554fa2f")],"recipe":{"op":"commit_mod_fns","args":{"commit":"d554fa2f"}}},
 {"id":"L1-15","split":"test","difficulty":"med","variant":"正式·提交改动",
  "question":"ca6e02fc 这笔提交涉及哪些函数的改动？",
  "subject":[("commit","ca6e02fc")],"recipe":{"op":"commit_mod_fns","args":{"commit":"ca6e02fc"}}},
 {"id":"L1-16","split":"test","difficulty":"med","variant":"正式·提交改动",
  "question":"提交 a93ab522 改到了哪些函数？",
  "subject":[("commit","a93ab5")],"recipe":{"op":"commit_mod_fns","args":{"commit":"a93ab5"}}},
 # -- fn -> 实现的概念（gold=Concept）--
 {"id":"L1-17","split":"dev","difficulty":"med","variant":"正式·函数对应概念",
  "question":"_run_verify 这个函数对应实现了哪个设计概念？",
  "subject":[("fn","_run_verify")],"recipe":{"op":"fn_concepts","args":{"fn":"_run_verify"}}},
 {"id":"L1-18","split":"test","difficulty":"med","variant":"正式·函数对应概念",
  "question":"_unwrap_agent_output 体现的是哪个设计概念？",
  "subject":[("fn","_unwrap_agent_output")],"recipe":{"op":"fn_concepts","args":{"fn":"_unwrap_agent_output"}}},
 # -- 类 -> 所属模块（gold=Module）--
 {"id":"L1-19","split":"dev","difficulty":"easy","variant":"正式·类定位",
  "question":"ChaosHarness 类定义在哪个文件里？",
  "subject":[("class","multi-agent-orch::src/orch/chaos/__init__.py::ChaosHarness")],
  "recipe":{"op":"class_module","args":{"cls":"multi-agent-orch::src/orch/chaos/__init__.py::ChaosHarness"}}},
 {"id":"L1-20","split":"test","difficulty":"easy","variant":"正式·类定位",
  "question":"Store 类是在哪个模块文件中声明的？",
  "subject":[("class","multi-agent-orch::src/orch/store/__init__.py::Store")],
  "recipe":{"op":"class_module","args":{"cls":"multi-agent-orch::src/orch/store/__init__.py::Store"}}},

 # ================= L2 多跳影响面（30；dev15/test15）=================
 # -- 反向 CALLS 闭包：改 X 波及哪些函数（gold=闭包，不含自身）--
 {"id":"L2-01","split":"dev","difficulty":"med","variant":"影响面·3跳",
  "question":"如果动了 _handle_terminate 的实现，沿调用链往上三跳，会波及到哪些函数？",
  "subject":[("fn","_handle_terminate")],"recipe":{"op":"rev_calls_closure","args":{"fn":"_handle_terminate","depth":3}}},
 {"id":"L2-02","split":"test","difficulty":"med","variant":"影响面·3跳",
  "question":"改 check_watchdogs 会牵连到哪些上游函数（三跳以内）？",
  "subject":[("fn","check_watchdogs")],"recipe":{"op":"rev_calls_closure","args":{"fn":"check_watchdogs","depth":3}}},
 {"id":"L2-03","split":"dev","difficulty":"med","variant":"影响面·3跳",
  "question":"ensure_worktrees 一旦改坏，三跳以内哪些函数会受影响？",
  "subject":[("fn","ensure_worktrees")],"recipe":{"op":"rev_calls_closure","args":{"fn":"ensure_worktrees","depth":3}}},
 {"id":"L2-04","split":"test","difficulty":"med","variant":"影响面·3跳",
  "question":"要重构 _run_bench_series，往上三跳的调用方闭包是哪些？",
  "subject":[("fn","_run_bench_series")],"recipe":{"op":"rev_calls_closure","args":{"fn":"_run_bench_series","depth":3}}},
 {"id":"L2-05","split":"dev","difficulty":"hard","variant":"影响面·2跳",
  "question":"只看两跳，改 _load_config 会直接和间接影响到哪些函数？",
  "subject":[("fn","_load_config")],"recipe":{"op":"rev_calls_closure","args":{"fn":"_load_config","depth":2}}},
 {"id":"L2-06","split":"test","difficulty":"med","variant":"影响面·3跳",
  "question":"_find_gate_request 改了之后，三跳内的调用方有哪些？",
  "subject":[("fn","_find_gate_request")],"recipe":{"op":"rev_calls_closure","args":{"fn":"_find_gate_request","depth":3}}},
 {"id":"L2-07","split":"dev","difficulty":"med","variant":"影响面·2跳",
  "question":"_summarize 改动后，两跳以内的调用方都有谁？",
  "subject":[("fn","_summarize")],"recipe":{"op":"rev_calls_closure","args":{"fn":"_summarize","depth":2}}},
 {"id":"L2-08","split":"test","difficulty":"hard","variant":"影响面·3跳",
  "question":"改 _write_scope 的签名，三跳以内会冲击到哪些函数？",
  "subject":[("fn","_write_scope")],"recipe":{"op":"rev_calls_closure","args":{"fn":"_write_scope","depth":3}}},
 {"id":"L2-09","split":"dev","difficulty":"hard","variant":"影响面·3跳",
  "question":"_last_ok_commit 这个函数要是改了，三跳内哪些上游函数会被波及？",
  "subject":[("fn","_last_ok_commit")],"recipe":{"op":"rev_calls_closure","args":{"fn":"_last_ok_commit","depth":3}}},
 {"id":"L2-10","split":"test","difficulty":"med","variant":"影响面·3跳",
  "question":"动 estimate_tokens 会影响到哪些调用它的函数（三跳内）？",
  "subject":[("fn","estimate_tokens")],"recipe":{"op":"rev_calls_closure","args":{"fn":"estimate_tokens","depth":3}}},
 {"id":"L2-11","split":"dev","difficulty":"med","variant":"影响面·3跳",
  "question":"_new_thread_id 改了以后，往上三跳会波及哪些函数？",
  "subject":[("fn","_new_thread_id")],"recipe":{"op":"rev_calls_closure","args":{"fn":"_new_thread_id","depth":3}}},
 {"id":"L2-12","split":"test","difficulty":"med","variant":"影响面·3跳",
  "question":"改 _render_replay_lines 的话，三跳以内受影响的函数有哪些？",
  "subject":[("fn","_render_replay_lines")],"recipe":{"op":"rev_calls_closure","args":{"fn":"_render_replay_lines","depth":3}}},
 {"id":"L2-13","split":"dev","difficulty":"med","variant":"影响面·3跳",
  "question":"_stop_marker_path 一改，三跳内的上游调用方会有哪些？",
  "subject":[("fn","_stop_marker_path")],"recipe":{"op":"rev_calls_closure","args":{"fn":"_stop_marker_path","depth":3}}},
 {"id":"L2-14","split":"test","difficulty":"med","variant":"影响面·3跳",
  "question":"改 _thread_roles 会波及哪些函数（三跳以内）？",
  "subject":[("fn","_thread_roles")],"recipe":{"op":"rev_calls_closure","args":{"fn":"_thread_roles","depth":3}}},
 # -- 调用链：A 到 B 经过哪些函数（gold=中间节点，gold_paths 记录整链）--
 {"id":"L2-15","split":"dev","difficulty":"hard","variant":"调用链",
  "question":"从 run_thread 出发，要走到 append_system_event，中间会依次经过哪些函数？",
  "subject":[("fn","run_thread"),("fn","append_system_event")],
  "recipe":{"op":"calls_chain","args":{"path_quals":["run_thread","_dispatch_group","_apply_bb_if_eligible","append_system_event"]}}},
 {"id":"L2-16","split":"test","difficulty":"hard","variant":"调用链",
  "question":"cmd_run 一路调到 _git，中途会经过哪些函数？",
  "subject":[("fn","cmd_run"),("fn","_git")],
  "recipe":{"op":"calls_chain","args":{"path_quals":["cmd_run","_build_adapters_from_config","ensure_worktrees","_git"]}}},
 {"id":"L2-17","split":"dev","difficulty":"med","variant":"调用链",
  "question":"check_watchdogs 到 _next_event_id 之间隔着哪个函数？",
  "subject":[("fn","check_watchdogs"),("fn","_next_event_id")],
  "recipe":{"op":"calls_chain","args":{"path_quals":["check_watchdogs","_raise_gate","_next_event_id"]}}},
 {"id":"L2-18","split":"test","difficulty":"hard","variant":"调用链",
  "question":"render_view 最终会调到 _summarize，这条链上中间经过哪些函数？",
  "subject":[("fn","render_view"),("fn","_summarize")],
  "recipe":{"op":"calls_chain","args":{"path_quals":["render_view","_classify","_bg_line","_summarize"]}}},
 {"id":"L2-19","split":"dev","difficulty":"med","variant":"调用链",
  "question":"apply_gate_decision 要触达 _run_gate_op，中间夹着哪个函数？",
  "subject":[("fn","apply_gate_decision"),("fn","_run_gate_op")],
  "recipe":{"op":"calls_chain","args":{"path_quals":["apply_gate_decision","run_privileged_and_callbacks","_run_gate_op"]}}},
 {"id":"L2-20","split":"test","difficulty":"med","variant":"调用链",
  "question":"ChaosHarness.run 到 ChaosHarness._build_adapters，中间过了哪个方法？",
  "subject":[("fn","ChaosHarness.run"),("fn","ChaosHarness._build_adapters")],
  "recipe":{"op":"calls_chain","args":{"path_quals":["ChaosHarness.run","ChaosHarness._ensure_baseline","ChaosHarness._build_adapters"]}}},
 # -- 影响面到模块层（gold=Module）--
 {"id":"L2-21","split":"dev","difficulty":"hard","variant":"影响面·跨模块",
  "question":"改 _load_config 的影响面会跨到哪些模块（文件）？",
  "subject":[("fn","_load_config")],"recipe":{"op":"impact_modules","args":{"fn":"_load_config","depth":3}}},
 {"id":"L2-22","split":"test","difficulty":"hard","variant":"影响面·跨模块",
  "question":"_handle_terminate 的三跳影响面覆盖了哪些模块文件？",
  "subject":[("fn","_handle_terminate")],"recipe":{"op":"impact_modules","args":{"fn":"_handle_terminate","depth":3}}},
 {"id":"L2-23","split":"dev","difficulty":"med","variant":"影响面·跨模块",
  "question":"动 _run_bench_series，波及面落在哪些模块上？",
  "subject":[("fn","_run_bench_series")],"recipe":{"op":"impact_modules","args":{"fn":"_run_bench_series","depth":3}}},
 {"id":"L2-24","split":"test","difficulty":"med","variant":"影响面·跨模块",
  "question":"ensure_worktrees 改动的影响会扩散到哪几个模块？",
  "subject":[("fn","ensure_worktrees")],"recipe":{"op":"impact_modules","args":{"fn":"ensure_worktrees","depth":3}}},
 # -- 跨 IMPLEMENTS+MODIFIES：改过“实现概念X的函数”的提交（gold=Commit）--
 {"id":"L2-25","split":"dev","difficulty":"hard","variant":"跨边·概念→函数→提交",
  "question":"那些实现了权限三件套的函数，历史上被哪些提交改动过？",
  "subject":[("concept","concept::权限三件套")],"recipe":{"op":"concept_fns_commits","args":{"concept":"concept::权限三件套"}}},
 {"id":"L2-26","split":"test","difficulty":"hard","variant":"跨边·概念→函数→提交",
  "question":"实现视图组装的那几个函数，分别在哪些提交里被动过？",
  "subject":[("concept","concept::视图组装")],"recipe":{"op":"concept_fns_commits","args":{"concept":"concept::视图组装"}}},
 {"id":"L2-27","split":"dev","difficulty":"hard","variant":"跨边·概念→函数→提交",
  "question":"负责消费停机标志的函数，历史上有哪些提交改过它们？",
  "subject":[("concept","concept::stop-标志消费")],"recipe":{"op":"concept_fns_commits","args":{"concept":"concept::stop-标志消费"}}},
 {"id":"L2-28","split":"test","difficulty":"hard","variant":"跨边·概念→函数→提交",
  "question":"实现协议层校验的函数，被哪些提交修改过？",
  "subject":[("concept","concept::协议层")],"recipe":{"op":"concept_fns_commits","args":{"concept":"concept::协议层"}}},
 # -- 跨 MODIFIES+IMPLEMENTS：提交改的函数各自实现的概念（gold=Concept）--
 {"id":"L2-29","split":"dev","difficulty":"hard","variant":"跨边·提交→函数→概念",
  "question":"提交 eb0ce75f 改的那些函数，各自实现了哪些设计概念？",
  "subject":[("commit","eb0ce75f")],"recipe":{"op":"commit_fns_concepts","args":{"commit":"eb0ce75f"}}},
 {"id":"L2-30","split":"test","difficulty":"hard","variant":"跨边·提交→函数→概念",
  "question":"610b4127 这笔提交动过的函数，落在哪些设计概念上？",
  "subject":[("commit","610b4127")],"recipe":{"op":"commit_fns_concepts","args":{"commit":"610b4127"}}},

 # ================= L3 设计溯源（10；dev5/test5）=================
 {"id":"L3-01","split":"dev","difficulty":"hard","variant":"溯源·为什么",
  "question":"为什么这个项目要给每次运行套一层权限保护——工作区隔离、写域审计再加自动留存？这套设计的依据是什么？",
  "subject":[("concept","concept::权限三件套")],"recipe":{"op":"design_provenance","args":{"concept":"concept::权限三件套"}},
  "gold_aspects":["ensure_worktrees 建隔离 worktree","audit_write_scope 写域审计","autocommit 自动提交","三件套接入 core（C-1/§8.1）"]},
 {"id":"L3-02","split":"test","difficulty":"hard","variant":"溯源·为什么",
  "question":"停机的时候为什么要专门去消费一个 stop 标志？这个设计背后的原因是什么？",
  "subject":[("concept","concept::stop-标志消费")],"recipe":{"op":"design_provenance","args":{"concept":"concept::stop-标志消费"}},
  "gold_aspects":["修复 stop 语义链条断裂（C-2）","闭合停机语义链条","配套 orch run 常驻命令"]},
 {"id":"L3-03","split":"dev","difficulty":"hard","variant":"溯源·为什么",
  "question":"看门狗在升级时为什么要引入水位来做去重？",
  "subject":[("concept","concept::看门狗升级水位去重")],"recipe":{"op":"design_provenance","args":{"concept":"concept::看门狗升级水位去重"}},
  "gold_aspects":["用水位标记防止重复升级","看门狗升级去重","随 §8.2 首轮审计兜底/§5.4 终止保留 pending 同批加固"]},
 {"id":"L3-04","split":"test","difficulty":"hard","variant":"溯源·为什么",
  "question":"为什么要设计增量式的热续渲染，而不是每次都全量重渲一遍视图？",
  "subject":[("concept","concept::render-delta-热续增量")],"recipe":{"op":"design_provenance","args":{"concept":"concept::render-delta-热续增量"}},
  "gold_aspects":["只带黑板 diff + 新事件 + 指令尾","needs_cold_start 判据","对应 §6.5 热续"]},
 {"id":"L3-05","split":"dev","difficulty":"hard","variant":"溯源·为什么",
  "question":"门禁决策的应用为什么要做成幂等的？",
  "subject":[("concept","concept::apply-gate-decision幂等化")],"recipe":{"op":"design_provenance","args":{"concept":"concept::apply-gate-decision幂等化"}},
  "gold_aspects":["重复调用只生效一次","与恢复路径同 target pending 合并同批","对应 §9.1/§16"]},
 {"id":"L3-06","split":"test","difficulty":"hard","variant":"溯源·为什么",
  "question":"异步执行路径为什么要加一层终止兜底，还要把会话的 sid 直接作废掉？",
  "subject":[("concept","concept::async-终止兜底与-upsert-session-作废-sid")],
  "recipe":{"op":"design_provenance","args":{"concept":"concept::async-终止兜底与-upsert-session-作废-sid"}},
  "gold_aspects":["异步终止兜底同源同修","upsert_session 直写作废 sid","终局评审发现的闭合项（249 绿 + chaos50）"]},
 {"id":"L3-07","split":"dev","difficulty":"hard","variant":"溯源·为什么",
  "question":"背景压缩比这个指标，为什么要按原文正文来采集，而不是拿摘要串去算？",
  "subject":[("concept","concept::背景压缩比采-bg-orig-原文-body")],
  "recipe":{"op":"design_provenance","args":{"concept":"concept::背景压缩比采-bg-orig-原文-body"}},
  "gold_aspects":["修采集失真：bg_orig 采原文 body 而非摘要串","使压缩比真实反映 _summarize","bg_orig 与 bg_summarized 同口径 body token"]},
 {"id":"L3-08","split":"test","difficulty":"hard","variant":"溯源·为什么",
  "question":"为什么要引入一个故障注入器，并在关键的落盘点挂上故障检查钩子？",
  "subject":[("concept","concept::faultinjector与fault-check钩子")],
  "recipe":{"op":"design_provenance","args":{"concept":"concept::faultinjector与fault-check钩子"}},
  "gold_aspects":["FaultInjector + §4.4 关键落盘点挂 fault_check","3 个 site 内嵌","面向存储/事务路径做故障演练"]},
 {"id":"L3-09","split":"dev","difficulty":"hard","variant":"溯源·为什么",
  "question":"热续为什么要在调度层设三道门控，还要用 version 去作废旧的 sid？",
  "subject":[("concept","concept::热续接入调度层三门控")],
  "recipe":{"op":"design_provenance","args":{"concept":"concept::热续接入调度层三门控"}},
  "gold_aspects":["§6.5 热续接入调度层采用三门控","以 version 作废 sid","盘上可重建判据"]},
 {"id":"L3-10","split":"test","difficulty":"hard","variant":"溯源·为什么",
  "question":"为什么触发批次里的那些事件，不管保留策略怎么设，都要全文塞进焦点窗？",
  "subject":[("concept","concept::触发批次事件一律全文入焦点窗")],
  "recipe":{"op":"design_provenance","args":{"concept":"concept::触发批次事件一律全文入焦点窗"}},
  "gold_aspects":["Q7 裁决 A：触发批次事件不论保留策略一律全文入焦点窗","_classify 增 trigger_ids","view.event_ids 内事件全文进焦点窗"]},
]

LAYER_OF = {"L1":"onehop_fact","L2":"multihop_impact","L3":"design_provenance"}


def resolve_subject(g, spec):
    kind, val = spec
    if kind == "fn":
        return g.fn(val)
    if kind == "concept":
        return val
    if kind == "commit":
        return g.commit(val)
    if kind in ("class", "module"):
        return val
    raise ValueError(spec)


def short(nid):
    return nid.split("::")[-1]


def gold_answer_text(g, layer, subtype, gold, recipe):
    names = [short(x) for x in gold]
    if layer == "L1":
        if subtype in ("fn_module", "class_module"):
            return "所属模块：" + "、".join(names)
        if subtype == "fn_callers":
            return "直接调用方：" + "、".join(names)
        if subtype == "concept_impl_fns":
            return "实现函数：" + "、".join(names)
        if subtype == "commit_mod_fns":
            return "改动的函数：" + "、".join(names)
        if subtype == "fn_concepts":
            return "对应设计概念：" + "、".join(g.N[x].get("name", short(x)) for x in gold)
    if layer == "L2":
        if recipe["op"] in ("rev_calls_closure",):
            return "受影响（反向调用闭包）函数：" + "、".join(names)
        if recipe["op"] == "impact_modules":
            return "波及模块：" + "、".join(names)
        if recipe["op"] == "calls_chain":
            full = [short(g.fn(q)) for q in recipe["args"]["path_quals"]]
            return "调用链：" + " → ".join(full) + "；中间经过：" + "、".join(names)
        if recipe["op"] == "concept_fns_commits":
            return "改动过其实现函数的提交（hash）：" + "、".join(x.split("::")[-1][:12] for x in gold)
        if recipe["op"] == "commit_fns_concepts":
            return "落在的设计概念：" + "、".join(g.N[x].get("name", short(x)) for x in gold)
    if layer == "L3":
        cid = recipe["args"]["concept"]
        commits = [x for x in gold if g.label(x) == "Commit"]
        return ("设计概念：%s；溯源提交（DESCRIBES，hash）：%s"
                % (g.N[cid].get("name", short(cid)),
                   "、".join(c.split("::")[-1][:12] for c in commits)))
    return "、".join(names)


def main():
    g = G.Graph()
    rows = []
    leaks = []
    missing = []
    for q in QUESTIONS:
        layer = q["id"][:2]
        subj_ids = [resolve_subject(g, s) for s in q["subject"]]
        gold = g.derive(q["recipe"])
        # 存在性
        for gid in gold:
            if not g.has(gid):
                missing.append((q["id"], gid))
        # 泄漏检查：非 subject 的 gold 可识别名不得出现在题面
        subj_set = set(subj_ids)
        for gid in gold:
            if gid in subj_set:
                continue
            for s in g.distinctive_strings(gid):
                if s in q["question"]:
                    leaks.append((q["id"], gid, s))
        subtype = q["recipe"]["op"]
        row = {
            "id": q["id"],
            "layer": layer,
            "type": LAYER_OF[layer],
            "subtype": subtype,
            "question": q["question"],
            "variant": q["variant"],
            "difficulty": q["difficulty"],
            "split": q["split"],
            "subject_entities": subj_ids,
            "gold_entities": gold,
            "gold_n": len(gold),
            "gold_answer": gold_answer_text(g, layer, subtype, gold, q["recipe"]),
            "recipe": q["recipe"],
        }
        if q["recipe"]["op"] == "calls_chain":
            path = [g.fn(x) for x in q["recipe"]["args"]["path_quals"]]
            row["gold_paths"] = [path]
        if layer == "L3":
            row["gold_aspects"] = q["gold_aspects"]
            row["eval"] = {"program": "context_recall_provenance", "min_hit": 1,
                           "judge": "design_rubric_one_of"}
        elif layer == "L2":
            row["eval"] = {"program": "context_answer_intersection", "min_hit": 1,
                           "gold_n": len(gold), "judge": "answer_completeness"}
        else:
            row["eval"] = {"program": "context_recall", "min_hit": 1,
                           "gold_n": len(gold), "judge": "answer_accuracy"}
        rows.append(row)

    if missing:
        print("[FAIL] gold 缺失:", missing); sys.exit(1)
    if leaks:
        print("[FAIL] 题面泄漏答案实体:")
        for lid, gid, s in leaks:
            print(f"   {lid}: gold {gid} 的可识别名 {s!r} 出现在题面")
        sys.exit(1)

    with open(OUT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 摘要
    from collections import Counter
    lc = Counter(r["layer"] for r in rows)
    sc = Counter((r["layer"], r["split"]) for r in rows)
    print("[OK] 写出", len(rows), "题 ->", os.path.normpath(OUT))
    print("  层分布:", dict(lc))
    print("  层×split:", {f"{k[0]}/{k[1]}": v for k, v in sorted(sc.items())})
    gz = [r["gold_n"] for r in rows if r["layer"] != "L3"]
    print("  gold_n 范围(L1/L2):", min(gz), "-", max(gz))


if __name__ == "__main__":
    main()
