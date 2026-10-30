# 目录整理记录（2026-10-30）

## 范围与迁移

保留包名 `research_agent`、公开业务模块、CLI、静态资源、根目录 Compose 和运行数据位置；不按旧计划示例更名为 `ai_agent`，不引入 PostgreSQL。开始整理时已有 LangGraph、会话存储等未提交修改，迁移以工作区现有文件为准，未覆盖这些功能改动。

| 旧位置 | 当前位置 | 兼容方式 |
|---|---|---|
| `src/research_agent/evaluate.py` | `src/research_agent/evaluation/retrieval.py` | 旧模块保留 `main`、`normalize` 与命令入口 |
| `src/research_agent/acceptance.py` | `src/research_agent/evaluation/acceptance.py` | 旧模块保留 `main`、`digest`、`distribution` |
| `src/research_agent/smoke.py` | `src/research_agent/evaluation/smoke.py` | 旧模块保留 `main` 与命令入口 |
| `eval/questions.json`、`holdout_questions.json`、`holdout_manifest.json` | `eval/datasets/` 同名文件 | 读取路径同步更新，文件原文保留 |
| `eval/summary_*`、`review_*`、`reviews_*`、`acceptance_review*`、`v4_pro_*`、`final_checks*` | `eval/reports/` 同名文件 | 文档引用更新，JSON 历史快照不改写 |
| `tests/test_acceptance.py`、`test_agent_features.py`、`test_core.py`、`test_retrieval_quality.py`、`test_review_regressions.py` | `tests/unit/` 同名文件 | pytest 递归收集 |
| `tests/test_conversation_sqlite.py`、`test_workflow.py`、`test_langgraph_migration.py`、`test_embedding_integration.py` | `tests/integration/` 同名文件 | 保留原标记；修正学习示例的相对路径 |
| `tests/manual_workflow_demo.py` | `scripts/manual_workflow_demo.py` | 演示文档与检查命令更新 |

历史 `eval/results/` 内的冻结 manifest、执行脚本快照和 verification 不改写，里面的旧命令/路径按上表理解。它们描述当时执行情况，不能用于证明当前文件哈希相同。`eval/run_research_acceptance.py` 仍在原位置，以保留快照来源。

## 新增骨架

- `src/research_agent/observability/`：尚未接入运行时。
- `eval/experiments/`：实验说明与空白记录模板；没有新增测量结果。
- `tests/e2e/`、`tests/security/`、`tests/performance/`：测试计划，不放空测试冒充覆盖率。
- `scripts/benchmark.py`、`scripts/cost_analysis.py`：显式提示未实现并非零退出的占位入口。
- `docs/architecture.md`、`evaluation.md`、`optimization.md`、`observability.md`、`deployment.md`：当前边界与后续工作说明。
- `.github/workflows/ci.yml`：默认离线测试与整理模块 lint/format；未配置部署。

## 保留项与删除判断

本次没有确认可删除的废弃功能文件，也没有执行文件清理。仅无静态引用不足以证明 CLI、公开导入接口、手动脚本或历史证据已废弃。

| 保留位置 | 判断与风险 |
|---|---|
| 旧评测模块三个入口 | 现有文档和外部命令仍可使用，需要兼容 |
| `eval/results/`、历史审核、冻结清单 | 复现和追溯证据，不能当作过期垃圾 |
| `.pytest*`、`.ruff_cache/`、`.uv-cache/`、`__pycache__/` | 看似临时产物；未核查是否仍被进程使用，本次不递归清理 |
| 根目录 `web-*.log`、`web-server*.log` | 可能仍是运行服务的输出目标，日志也可能含私有信息，不移动、不读取正文、不提交 |
| `.vscode/`、`.idea/` | 本地编辑器配置，无法据此判定废弃；保持原样 |
| `attachment-read/`、`work/`、`examples/` | 可能含用户附件、演示状态和学习材料；学习示例仍被测试引用，未删除 |
| `.gitignore` 中的 `data/chroma/` | 只有历史忽略规则，没有确认存在的废弃实现；保留可避免旧本地数据被意外提交 |
| `data/`、`.env`、卷、SQLite、索引 | 运行数据和私有配置，本次不迁移、不读凭据、不导入、不重建 |

后续若清理缓存、日志或附件，先确认具体目录、运行进程和保留需求，再单独取得删除确认。

## 验证边界

整理前默认测试：111 通过、1 项真实模型测试排除；另 2 项本机 HTTP 测试因沙箱 WinError 10013 失败。全范围 Ruff 基线为 23 条 lint 问题，9 个文件需格式化。本次只对整理相关文件做必要修复与格式化，保留其余既有问题。

整理后检查：

- 默认离线测试在允许本机 HTTP 的环境下 **113 passed、1 deselected**；没有调用真实模型、DeepSeek 或搜索服务。
- 本次整理模块的 Ruff lint 和 format 均通过，16 个文件已符合格式。
- 全范围检查仍有 **13 条既有 lint 问题、6 个文件格式问题**，集中在未重构的业务代码及原有测试；未批量修复。
- 冻结题集 SHA-256、11 份迁移 JSON 内容、旧新模块入口一致性、Python 语法、CI YAML 解析及 `git diff --check` 通过。
- 更新后的文档没有新增断链；历史报告资料摘录保留 **79 处原有图片相对链接断链**，未猜测来源或改写原文。历史审核内容本身未重新验收。

基础 CI 未在 GitHub 执行，不代表真实模型、搜索、导入、并发或生产部署验收。没有执行提交或推送。
