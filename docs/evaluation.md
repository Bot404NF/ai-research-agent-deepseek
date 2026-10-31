# 评测工作入口

第四周真实业务对照、预算及采用结论见[报告](../eval/reports/week4_completion_20261031.md)、[运行前指标](../eval/experiments/week4.md)。固定100题，两组均走真实问答函数；费用与P50改善，但P95和质量未过采用门槛，默认开关保持关闭。不得用历史候选分数代替本轮业务成绩。

当前主评测统一为`eval/datasets/unified.json`的100题，不按新老标注或分别出分。`PYTHONPATH=src`下运行`python eval/run_eval.py --output 新结果路径`先校验，显式加`--generate`进行全量配对。门槛与适用范围见[统一质量验收方案](../eval/experiments/quality_target.md)。原有周次文件仅供复现历史。

第三周按“质量优化、第四周再做性能”的最新安排完成来源排序与固定证据提示词对照：[方案](../eval/experiments/week3.md)、[实际结果与保留问题](../eval/reports/week3_completion_20261031.md)。三组均使用同一模型、共享分题候选池、最终4条证据；结果只代表开发回归实验，不等于业务默认路径已改善。

第二周分题检索对照见[任务、控制变量与验收标准](../eval/experiments/week2.md)。入口为 `eval/run_split_experiment.py`，默认只校验，显式选择真实检索或组合题配对生成；本轮不切换业务默认策略。

已完成真实对照与18份AI复核：[第二周结果](../eval/reports/week2_completion_20261031.md)。旧组合题自动Hit仍4/8、答案正确仍4/8，出现一例新增引用误读，暂不采用分题策略。

第一周全部适用语义审核已由 AI 完成，结果与真人审核区分记录，见[收尾报告](../eval/reports/week1_completion_20261031.md)。新增离线成本重算 `scripts/cost_analysis.py` 与工具基线 `eval/run_tool_baseline.py`；真实工具后端不可用时保留失败，不用模拟成功替代。

第一周基线：运行 `PYTHONPATH=src` 下的 `python eval/run_baseline.py --output 新结果路径` 先校验数据，再显式选择 `--retrieve` 或 `--generate`。详见[分层数据、指标口径与学习步骤](../eval/experiments/week1.md)和[本次基线报告](../eval/reports/week1_baseline_20261031.md)。

实现位于 `src/research_agent/evaluation/`；数据集位于 `eval/datasets/`，实验计划位于 `eval/experiments/`，人工审核与历史总结位于 `eval/reports/`，原始输出保留在 `eval/results/`。

旧的 `python -m research_agent.evaluate`、`research_agent.acceptance`、`research_agent.smoke` 保留兼容入口。新入口可使用 `python -m research_agent.evaluation.retrieval`、`research_agent.evaluation.acceptance`、`research_agent.evaluation.smoke`；开发集也可使用 `PYTHONPATH=src` 下的 `python eval/run_eval.py`。具体 PowerShell 命令及外部服务要求见 [评测说明](../eval/README.md)。

基础 CI 只运行离线自动化测试。真实评测需要显式配置本地模型、Milvus；生成与真实代理冒烟会调用已配置 API。未自动运行、未新增指标成绩；历史冻结校验仍生效，不能为通过评测而改写摘要。
