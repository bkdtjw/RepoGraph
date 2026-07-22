# RepoGraph v0.3 门禁（计划书 §4 A4 / §5 硬指标 / 附录 A）
# Windows 无 make 也可直接跑：python eval/gate.py
# 合并前必跑（治理规则 R3）。语义档按 lexical 离线评估，不经网关、不调 LLM。

PYTHON ?= python

.PHONY: gate dataset test clean

# 主门禁：48 题全量 + 硬指标断言 + 锁定失败回归，产出 eval/gate_report.json
gate:
	$(PYTHON) eval/gate.py

# 重新生成 48 题数据集（gold 经 output/graph.json 真实核对）
dataset:
	$(PYTHON) design_work/gen_dataset.py

# 结构层自测（stdlib，无 pytest）
test:
	$(PYTHON) tests/test_context.py
	$(PYTHON) tests/test_topic.py

clean:
	rm -f eval/gate_report.json
