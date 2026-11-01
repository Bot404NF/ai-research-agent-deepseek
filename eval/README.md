# 检索、回答与性能评估

第四周已执行统一100题真实业务性能/成本对照与200份AI审核，未通过默认采用门槛。入口为`scripts/benchmark.py`、`eval/review_week4.py`和`eval/smoke_week4.py`；共享10元预算，结果及来源限制见[第四周报告](reports/week4_completion_20261031.md)。以下历史统一候选成绩不代表本轮业务成绩。

日常评测统一使用`datasets/unified.json`，不按新老题分组。主入口为`PYTHONPATH=src`下的`python eval/run_eval.py --output 新结果路径`（默认只校验），显式加`--generate`运行全部100题的基础检索/候选配对。目标：整题正确≥85%、正题引用支持≥95%、无答案拒答≥95%、组合题≥80%。见[统一验收方案](experiments/quality_target.md)。以下周次内容保留为历史运行记录，不是当前需要分别执行的题集。

本轮[统一验收结果](reports/unified_completion_20261031.md)：候选完整正确74/82（90.24%）、引用支持80/82（97.56%）、拒答18/18、组合题16/17，达到AI辅助点估计门槛；仍有8道正题未完成、2道新增核心退化，尚未真人复核或切换业务默认。

第三周已执行[质量对照实验](reports/week3_completion_20261031.md)：共享候选池内优先直接书目后，旧组合题锚点Hit由4/8升至5/8；25题三组共75份回答已AI复核。新增提示词未进一步提高核心答案正确数，业务默认保持不变。[任务与复现](experiments/week3.md)，入口 `eval/run_quality_experiment.py`。

第二周已完成[分题检索对照实验](reports/week2_completion_20261031.md)：旧组合题自动整题Hit仍4/8，暂不接入业务默认。任务范围与命令见[第二周计划](experiments/week2.md)，入口为 `eval/run_split_experiment.py`。

第一周现已收尾：见[56题AI全量复核、成本及Milvus恢复后的工具结果](reports/week1_completion_20261031.md)。AI 代审独立标记，原始生成不重跑、不改写；当前摘要为 `reports/baseline.json`。

2026-10-31 新增[第一周分层基线运行与教学](experiments/week1.md)：56 道公开回归题、已标注证据 Recall@4、锚点 Hit@4、人工语义审核模板、真实 Token 和 P50/P95。入口为 `PYTHONPATH=src` 下的 `python eval/run_baseline.py`；[本次报告](reports/week1_baseline_20261031.md) 单列实际执行与阻碍，不混用历史成绩。

目录已整理为 `datasets/`、`experiments/`、`reports/` 和 `results/`；代码实现迁入 `src/research_agent/evaluation/`，下方旧模块命令保留兼容。新增开发集入口为 `PYTHONPATH=src` 下的 `python eval/run_eval.py`。历史 JSON 和原始执行快照中的旧路径不改写，映射见 [目录整理记录](../docs/directory-layout.md)。

2026-10-30 新增[真实专题研究验收](../docs/research-acceptance-2026-10-30.md)：关闭联网搜索，以现有上海地方志运行《安亭志》研究。流程通过，内容审核未完整通过。入口为 `run_research_acceptance.py`，原始任务、报告、模型调用、来源匹配和逐项审核位于 `results/research_20261030_anting_live/`；该案例与下述历史问答评估口径不同，不合并计算准确率。

2026-10-31 起当前默认模型为 `deepseek-flash`，见[模型配置规范](../docs/model-spec.md)。下述 2026-10-20 记录及第一周 56 题生成基线使用 `deepseek-v4-pro`，不代表 Flash 的质量、延迟或成本。切换模型后，新的生成评测必须另存结果并使用匹配的价格配置；检索指标仍需注明资料与检索实现版本。

2026-10-20 使用 `deepseek-v4-pro` 重跑了开发集、既有验收集回归、真实生成、性能和HTTP链路，见 [当轮完整报告](../docs/acceptance-2026-10-20.md)、[逐题复核](reports/v4_pro_review_2026-10-20.md) 和 [检查清单](reports/final_checks_2026-10-20.json)。该题集已被查看并用于提示词优化，仅属回归；后文的独立验收和性能数字为2026-10-19历史记录，不能直接代表当前模型。

## 数据与报告

| 文件 | 用途 |
|---|---|
| `datasets/questions.json` | 20道参与调优的开发集 |
| `reports/summary_round3.md` | 同题集Hit@4从60%到70%的改进记录 |
| `datasets/holdout_questions.json` | 20道未参与调参的验收题与4道合成无答案题 |
| `datasets/holdout_manifest.json` | 检索前冻结的题集、检索代码摘要及生成样本ID |
| `reports/acceptance_reviews.json` | 独立集逐题审核、回答质量与引用支持判断 |
| `reports/acceptance_review.md` | 完整候选、真实回答和逐题理由 |
| `results/acceptance_*.json` | 原始候选、回答、耗时与环境 |
| `results/smoke_*.json` | 本机HTTP、完整代理、会话恢复记录 |
| `reports/final_checks.json` | 最终自动化和集成检查记录 |

完整结论见 `../docs/acceptance-2026-10-19.md`；项目介绍见 `../docs/project-overview.md`。旧轮次文件保留作为历史证据。

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
