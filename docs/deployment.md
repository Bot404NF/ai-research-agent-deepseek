# 部署与基础检查

当前是本地单用户演示应用。根目录 `docker-compose.yml` 仅启动 Milvus 及其依赖，应用使用 `uv run python -m research_agent.cli web` 启动。安装与配置见 [README](../README.md)，无需新增一份重复的 Compose 文件。

当前默认模型为 `deepseek-flash`，已有本机 `.env` 也需设置 `DEEPSEEK_CHAT_MODEL=deepseek-flash`。操作系统/进程中的同名环境变量优先于 `.env`；修改后重启应用服务才会重新加载配置。模型约定见[模型配置规范](model-spec.md)。

`.github/workflows/ci.yml` 提供离线测试及本次整理模块的 Ruff 检查，不调用真实模型、Milvus 或 DeepSeek。运行时依赖按 `uv.lock` 冻结安装；CI 下载依赖需要网络。既有其他模块的 lint/格式问题另行整理，不在本次批量重写。

应用容器、健康检查、备份恢复、版本回滚和外网部署尚未实现。运行数据保持原路径；禁止通过删除卷或重新运行 `ingest` 来验证目录整理。
