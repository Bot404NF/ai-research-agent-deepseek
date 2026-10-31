# 模型配置规范

- 更新日期：2026-10-31（Australia/Sydney）。
- 适用范围：普通问答、单次证据回答、专题研究的计划/证据覆盖判断/分析/检查/报告，以及新运行的生成评测。
- 当前默认生成模型：`deepseek-flash`，由 `src/research_agent/config.py` 的 `Settings.chat_model` 统一提供。

## 模型标识与来源

使用 `deepseek-flash`，不使用拼写错误 `deepseek-flesh`。根据 [DeepSeek 官方中文模型与价格页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)（网站及发布机构：DeepSeek；获取日期：2026-10-31；页面未注明发布日期），查询日该标识对应 DeepSeek-V4.1-Flash-1017。供应商可能更新别名所指版本，因此该映射是查询日记录，不是固定版本保证。

## 配置与调用约定

1. 项目默认值和 `.env.example` 保持一致：`DEEPSEEK_CHAT_MODEL=deepseek-flash`。本机已有 `.env` 中的模型项需同步修改。
2. 环境变量可覆盖默认值；当前配置加载保留操作系统/进程变量优先于 `.env` 的行为。改 `.env` 后应重启应用，现有后台任务持有的 Settings 不会自动更新。
3. `agent._model`、`answer.answer_question` 和评测生成均使用 `settings.chat_model`；专题研究的模型请求复用 `agent._model`。不在节点、工具或提示词中硬编码另一模型。
4. 保留当前非思考模式、温度、超时和重试策略。此次只切换生成模型，不调整向量模型、切分、Milvus 集合或检索阈值，也不需要重建索引。
5. Search/Fetch 为独立工具接口，生成模型名称不改变其联网授权规则。配置对象不得打印 API 密钥，不在文档、结果或命令输出中展示密钥。

## 验证与评测约定

离线配置测试验证无环境覆盖时使用 `deepseek-flash`，并验证显式模型环境变量仍可覆盖；普通问答与 LangGraph 工作流沿用现有模拟回归。

历史 V4-Pro 题集输出、AI 复核、延迟、用量和价格配置继续保留原模型名称。第一周 `eval/reports/baseline.json` 对应 V4-Pro 已运行证据，不因默认模型变更而重写；新模型的生成记录需另存、重新审核，不能复制旧分数。旧 Pro 价格配置不得用于 Flash 成本估算。

```powershell
$env:PYTHONPATH = 'src'

# 只输出是否生效，不打印整个 Settings 或凭据
.\.venv\Scripts\python.exe -c "from research_agent.config import get_settings; print('Flash配置生效:', get_settings().chat_model == 'deepseek-flash')"

# 离线配置与调用链回归，不使用真实 API 密钥
$env:DEEPSEEK_API_KEY = ''
.\.venv\Scripts\python.exe -m pytest --basetemp=.pytest-flash-model-tmp --tb=short
```

完整真实生成评测另行运行并记录模型、题集/代码摘要、证据、usage、耗时和审核结果；离线通过只证明配置和现有调用契约兼容，不证明 Flash 的内容质量与 Pro 相同。

本次切换验证：本机有效配置为 `deepseek-flash`；完整离线回归 127 通过、1 项默认排除，记录位于 `eval/results/flash_switch_tests_20261031.xml`。模型配置与相关测试的 lint、format 检查通过。没有重复运行付费生成或重建索引。
