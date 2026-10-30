# 评测工作入口

实现位于 `src/research_agent/evaluation/`；数据集位于 `eval/datasets/`，实验计划位于 `eval/experiments/`，人工审核与历史总结位于 `eval/reports/`，原始输出保留在 `eval/results/`。

旧的 `python -m research_agent.evaluate`、`research_agent.acceptance`、`research_agent.smoke` 保留兼容入口。新入口可使用 `python -m research_agent.evaluation.retrieval`、`research_agent.evaluation.acceptance`、`research_agent.evaluation.smoke`；开发集也可使用 `PYTHONPATH=src` 下的 `python eval/run_eval.py`。具体 PowerShell 命令及外部服务要求见 [评测说明](../eval/README.md)。

基础 CI 只运行离线自动化测试。真实评测需要显式配置本地模型、Milvus；生成与真实代理冒烟会调用已配置 API。未自动运行、未新增指标成绩；历史冻结校验仍生效，不能为通过评测而改写摘要。
