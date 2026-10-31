# 辅助脚本

在仓库根目录运行，先设置 `$env:PYTHONPATH = 'src'`。

- `manual_workflow_demo.py`：从 `tests/` 迁入的模拟浏览器演示，启动命令为 `uv run python scripts/manual_workflow_demo.py --port 8877 --delay 2`。仅模拟研究流程，产物在被忽略的 `work/workflow-demo/`。
- `check_secrets.py`：标准库凭据扫描；默认检查跟踪文件，`--staged` 检查暂存区，`--paths` 检查指定产物。仅报告位置及规则，详见[防泄露说明](../docs/secret-protection.md)。
- `cost_analysis.py`：读取既有真实 usage 与有来源的价格配置，离线估算成本，不重复调用模型；说明见 `eval/experiments/week1.md`。
- `benchmark.py`：第四周统一100题真实业务性能对照；默认只校验，显式`--run`才调用API，共用10元费用账本，记录首内容/正文结束/轮次完成耗时；方案见`eval/experiments/week4.md`。

现有真实验收保留在 `eval/run_research_acceptance.py`；真实检索/生成/HTTP 冒烟入口见 `eval/README.md`，不要将其作为无外部服务的基础检查。

演示报告定向复验：`eval/replay_research_report.py --source 原始演示JSON --output 新结果JSON`默认只校验，显式加`--run`才复用证据运行检查与报告两步并计入原账本。工具拒绝覆盖，不能将它描述成完整研究链路复验；说明见[演示修复复核](../eval/reports/interview_demo_fix_review_20261101.md)。
