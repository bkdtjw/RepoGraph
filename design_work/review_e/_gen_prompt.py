# -*- coding: utf-8 -*-
"""生成 Phase E MCP 审查提示词：硬约束首行 + 契约 + 维度 + 内嵌带行号源码。"""
import io, os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HEAD = """【硬约束】禁止使用任何工具、禁止读文件、禁止联网、禁止执行代码。只依据本提示词下方内嵌的两份 Python 源码全文（带行号）直接审查作答。你是资深 MCP/协议与测试代码审查员。

【背景契约（只读，勿质疑外部文件存在性）】
本项目 RepoGraph 交付一个 MCP stdio 服务器，把三个检索函数以 MCP 工具暴露给 Claude Code。
- 传输 = 换行分隔的 JSON-RPC 2.0（每行一条完整 JSON 消息；服务器用 json.dumps 无 indent 写出，天然单行）。
- 设计主线：运行时零第三方依赖——**主动选纯 stdlib**，不引官方 mcp SDK（这是刻意决策 D-N8，非疏漏，勿建议改用 FastMCP/SDK）。
- 单进程、单线程、stdin 逐行阻塞读，无并发请求（MCP stdio 客户端顺序发送）；GraphStore 懒加载后进程内只读复用。
- 三工具契约：
  * ask_repo(question:str) —— 包装 build_repo_context，返回固定 11 键结构；**只供检索上下文，不生成答案**，永不裸拒。
  * impact_analysis(symbol:str, depth:int=2∈[1,4], mode:str∈{calls,imports}) —— 确定性影响面；歧义返回 {error:'ambiguous',candidates[]}（这是 P3 设计内的有效响应，非错误，isError 应为 False）；未命中 {error:'not_found'}。
  * repo_overview() —— 读 output/repo_card.json，缺失/损坏则现场确定性重建（degraded=true）。
- 错误分层契约（关键）：**工具执行层面的任何失败（图谱缺失/载入异常/入参非法/工具内部异常）都必须归一为 tools/call 的 isError=True 结果（content+structuredContent），绝不冒泡为 JSON-RPC 协议层 error(-32603)**。协议层 error 只用于：解析失败(-32700)、非对象(-32600)、缺 method(-32600)、未知方法(-32601)。
- 图谱路径：REPOGRAPH_GRAPH 环境变量优先，否则相对仓库 output/graph.json。载入失败不崩传输：initialize/tools/list 仍可用。
- 测试文件把服务器拉起为真实子进程走真实 stdio JSON-RPC（非只测 import），对真实图谱（multi-agent-orch，510 节点，functions=259/concepts=139/modules=22/classes=15/commits=75）断言真实检索值。

【审查维度（按重要性排序，逐条给结论；每条要么指出具体缺陷，要么明确写「未见问题」）】
1) 协议正确性：JSON-RPC 2.0 边界与异常回包是否正确？——
   a. 通知（无 id）是否绝不回响应？id 存在性判定（"id" not in msg，含 id=null 的边界）是否正确？
   b. 各错误码语义是否恰当（-32700/-32600/-32601 vs 工具层 isError）？错误回包结构 {jsonrpc,id,error{code,message,data?}} 是否合规？
   c. initialize 回显 protocolVersion 是否正确？notifications/initialized 等通知吞掉是否正确？
   d. 有无任何工具层异常/图谱异常会意外冒泡成协议层 -32603 或崩掉主循环（违反 isError 归一契约）？batch/半行/EOF/空行处理是否稳健？
2) 工具 schema 与描述准确性：inputSchema（类型/required/default/enum/additionalProperties）是否与实现的入参校验严格一致？depth 白名单（含 bool 是 int 子类的排除）、mode enum、additionalProperties:false 是否名实相符？工具 description 是否与真实返回字段/行为一致、有无过度承诺或误导上游 Agent 路由？structuredContent 与 content[text] 双通道是否保证一致（json 序列化可逆）？
3) 并发与超时：单线程阻塞模型下是否有隐藏的阻塞/死锁/资源泄漏风险？懒加载缓存在异常路径是否被正确标记（_loaded/_error 语义，失败后不重试是否可接受）？子进程测试客户端的后台读线程/队列/atexit 关闭是否有竞态、超时、僵尸进程或管道死锁风险？stderr 泵是否会在满管道时阻塞服务器？
4) 真实性与健壮性风险：测试断言是否可能「假绿」（如断言过弱、把错误吞成通过、mock 掉真实调用）？有无边界输入（超大 depth、非 str symbol、缺 arguments、arguments 非 dict）未被覆盖或处理不当？UTF-8/Windows GBK 处理是否正确？

【输出格式】每条发现给：severity(blocker/major/minor/nit) + 文件:行号 + 问题 + 依据 + 建议修法。无问题的维度明确写「未见问题」。最后给总体结论一句。禁止建议引入第三方库（含官方 mcp SDK）——那违反 D-N8 既定决策。
"""

def emb(relpath, idx, total):
    path = os.path.join(ROOT, relpath)
    with io.open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    out = ["", f"================ 文件 {idx}/{total}: {relpath} ================"]
    for i, ln in enumerate(lines, 1):
        out.append(f"{i:4d}| {ln}")
    return "\n".join(out)

parts = [HEAD]
parts.append(emb("src/repograph/mcp_server.py", 1, 2))
parts.append(emb("tests/test_mcp_server.py", 2, 2))
text = "\n".join(parts)
outp = os.path.join(ROOT, "design_work", "review_e", "prompt_mcp_code.txt")
with io.open(outp, "w", encoding="utf-8") as f:
    f.write(text)
print("wrote", outp, len(text), "chars,", text.count("\n")+1, "lines")
