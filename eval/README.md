# 检索、回答与性能评估

当前模型为 `deepseek-v4-pro`。2026-10-20 已重跑开发集、既有验收集回归、真实生成、性能和HTTP链路，见 [本轮完整报告](../docs/acceptance-2026-10-20.md)、[逐题复核](v4_pro_review_2026-10-20.md) 和 [检查清单](final_checks_2026-10-20.json)。本轮题集已被查看并用于提示词优化，仅属回归；后文的独立验收和性能数字为2026-10-19历史记录，不能直接代表当前模型。

## 数据与报告

| 文件 | 用途 |
|---|---|
| `questions.json` | 20道参与调优的开发集 |
| `summary_round3.md` | 同题集Hit@4从60%到70%的改进记录 |
| `holdout_questions.json` | 20道未参与调参的验收题与4道合成无答案题 |
| `holdout_manifest.json` | 检索前冻结的题集、检索代码摘要及生成样本ID |
| `acceptance_reviews.json` | 独立集逐题审核、回答质量与引用支持判断 |
| `acceptance_review.md` | 完整候选、真实回答和逐题理由 |
| `results/acceptance_*.json` | 原始候选、回答、耗时与环境 |
| `results/smoke_*.json` | 本机HTTP、完整代理、会话恢复记录 |
| `final_checks.json` | 最终自动化和集成检查记录 |

完整结论见 `../docs/acceptance-2026-10-19.md`；简历素材见 `../docs/resume-project.md`。旧轮次文件保留作为历史证据。

## 2026-10-19历史验收

- 开发集：14/20，Hit@4为70%。
- 独立集：16/20，Hit@4为80%；部分3题、无关1题。
- 可回答生成样本：11/12核心答案正确，另1题因指定证据不足合理拒答。
- 4/4合成无答案题拒答；但检索器对4题均返回非空候选，不能宣称阈值已可靠识别无答案。
- 预热后检索60次，中位数41.3 ms、P95 49.1 ms；其余性能口径见完整报告。

## 复现命令

在项目根目录的PowerShell执行，使用已有Milvus和本地模型；不运行ingest，不重建集合。

```powershell
$env:PYTHONPATH = 'src'
$env:HF_HUB_OFFLINE = '1'

# 开发集：仅检索，不调用DeepSeek
.\.venv\Scripts\python.exe -m research_agent.evaluate

# 独立验收集：检索与预热后性能，不调用DeepSeek
.\.venv\Scripts\python.exe -m research_agent.acceptance --repeats 3

# 加上固定16题的真实生成，会使用已配置API密钥并产生用量
.\.venv\Scripts\python.exe -m research_agent.acceptance --generate --repeats 3

# 临时本机HTTP服务，三轮真实代理问答，结束后关闭
.\.venv\Scripts\python.exe -m research_agent.smoke
```

首次模型下载完成后才使用 `HF_HUB_OFFLINE=1`。验收器会核对题集、检索代码及正样本原文SHA-256；版本变化时拒绝混用旧的冻结清单。未来修改实现后应建立新版本验收集或明确把当前集合改为回归集，不覆盖旧结果伪装为独立测试。

生成测试保存原始回答、引用标签、模型结束原因和完整证据；按题逐项审核后另存判断，不能把锚点匹配直接当成答案正确或引用支持。

## 口径

Hit@4：前四条候选包含足以回答整题的证据；题目限定书名、卷次或章节时必须满足相应范围。指定来源外即便事实一致，也不算严格来源命中。部分证据不计命中。

本轮充分命中均有单条支持；未标全所有可能正确段落，因此不计算完整Recall@4。核心答案引用支持也不冒充逐句引用准确率。

无答案题为明确不存在于源Markdown中的合成实体，不代表真实复杂边界问题。原文标注基于Markdown，未逐页对照扫描图片。审核状态不写入开发题集的 `human_review`，以绑定具体运行的审核文件为准。

`runtime/` 是被忽略的独立HTTP测试会话目录；真实使用者会话仍在原配置位置。
