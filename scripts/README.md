# 辅助脚本

在仓库根目录运行，先设置 `$env:PYTHONPATH = 'src'`。

- `manual_workflow_demo.py`：从 `tests/` 迁入的模拟浏览器演示，启动命令为 `uv run python scripts/manual_workflow_demo.py --port 8877 --delay 2`。仅模拟研究流程，产物在被忽略的 `work/workflow-demo/`。
- `benchmark.py`、`cost_analysis.py`：性能与成本分析占位入口，当前执行会说明未实现并非零退出，不调用服务、不输出伪造结果。

现有真实验收保留在 `eval/run_research_acceptance.py`；真实检索/生成/HTTP 冒烟入口见 `eval/README.md`，不要将其作为无外部服务的基础检查。
