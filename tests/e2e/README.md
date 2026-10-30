# 端到端测试计划（待实现）

待覆盖真实服务下的计划确认、拒绝/允许联网、刷新恢复、取消与报告下载。预先约定 API 用量、隔离目录、测试索引和验收证据。

现有本机 HTTP 契约检查在 `tests/integration/test_workflow.py`；模拟浏览器入口在 `scripts/manual_workflow_demo.py`。二者不能替代真实模型端到端质量验收。
