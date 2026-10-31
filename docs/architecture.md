# 当前架构

CLI / 本机网页 → 普通问答 `agent.py` 或研究服务 `workflow.py` → Milvus + BGE-M3 本地检索 → DeepSeek 回答。研究流程由 `research_graph.py` 的 LangGraph StateGraph 编排，计划和联网决定通过 interrupt 恢复。

普通会话保存在 SQLite，研究 checkpoint 保存在 SQLite，任务 JSON 用于视图和兼容；当前没有 PostgreSQL。Milvus 由根目录 `docker-compose.yml` 管理。现有 API、静态资源与运行数据位置保留。

当前默认生成模型为 `deepseek-flash`，普通问答和专题研究都从 `Settings.chat_model` 读取，通过 `DEEPSEEK_CHAT_MODEL` 覆盖。配置、版本标识和评测隔离约定见[模型配置规范](model-spec.md)。

评测实现归入 `src/research_agent/evaluation/`。最小本地诊断记录请求ID、阶段耗时、逻辑模型次数、状态及实际usage，不记录正文或凭据；实际网络重试次数未知时保留为空。没有监控平台。尚未按 agents/rag/tools/core 全量拆包，避免无必要地改变公开接口。详见 [目录与迁移说明](directory-layout.md)、[研究工作流](research-workflow.md) 和 [会话存储](conversation-storage.md)。
