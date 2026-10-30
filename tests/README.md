# 测试分区

- `unit/`：解析、配置、检索统计、工具行为与回归测试，外部调用使用模拟。
- `integration/`：SQLite、LangGraph/工作流及本机 HTTP 的模块协作检查，也包含需要真实 BGE-M3 的显式集成检查。
- `e2e/`、`security/`、`performance/`：专项测试计划，尚未实现专用测试集。

默认 `uv run pytest` 递归收集测试，`pyproject.toml` 配置 `pythonpath = ["src"]`。只有标记 `integration` 的真实模型测试默认排除；**目录名称不会自动打标记**，离线模块协作和本机 HTTP 检查仍默认执行。本机 HTTP 检查需要允许 loopback 连接。

显式运行真实模型检查：`uv run pytest -m integration -s tests/integration/test_embedding_integration.py`，可能下载模型，不属于基础 CI。模拟浏览器演示已迁到 `scripts/manual_workflow_demo.py`。
