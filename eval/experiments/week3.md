# 第三周：提高答案正确率

本周按第二周聊天末尾更新的安排推进质量优化；第四周再做性能。先完成来源/候选排序实验，再固定证据做提示词实验。质量没有通过时保留失败，不强行上线。

## 固定方案与采用门槛（运行前定义）

- 复用第二周57题，不改答案、锚点和来源；属于公开开发回归，W201单列，无新增独立验收。
- 检索使用相同连续编号拆分，每子题取12条候选。两组共享同一候选池：plain保持向量顺序；scoped稳定排序，优先文件名包含问题明确书名的候选，再优先明确提及的前言/凡例/发刊词/版权页/题名页。书名无法匹配时保留其他候选，不硬过滤；不使用标准答案。最终均轮流去重取4条。
- 候选预算由第二周4条提高为12条，因此本周内部配对可归因于排序，跨周分数不能只归因于排序。每子题底层向量候选也由40扩大到120；保存最后一轮全部返回候选，重复轮只记录耗时，不报告跨轮质量波动。三次检索共198次子查询，加一次预热；选择耗时与共享检索耗时分别记录。
- plain与scoped使用相同原提示词。constrained与scoped使用完全相同的最终证据，唯一差异为附加概念、版本、角色、逐项引用和缺证约束。不修改业务提示词。
- 生成固定25题：9道编号组合题、8道负题/边界题、Q11/H01/H04/H07/H08/H11/H14/H17八道版本/角色题，三组最多75次API请求。其余32题仅检索，不宣称整集答案正确率。按题轮换请求顺序，温度0、700输出Token、无重试，每题每组一次；不能消除模型随机性。
- 检索候选门槛：8道旧组合题整题锚点命中至少增加1题，原命中和40道单来源题不退化。提示词候选门槛：17道正题整题正确至少净增1题、无新增错误；引用支持和8道负题拒答均不得退化；截断和请求失败单列，不当作成功。两阶段独立决策；达标仅支持后续独立验收及业务路径验证，不自动上线。
- AI逐题核对证据和回答；AI审核与空白真人模板分开，未审核不记正确。引用标签合法不等于事实支持。Flash费用未知则null；记录实际usage，不沿用Pro价格。

## 运行

```powershell
$env:PYTHONPATH = 'src'
$env:HF_HUB_OFFLINE = '1'
$env:DEEPSEEK_CHAT_MODEL = 'deepseek-flash'
.\.venv\Scripts\python.exe eval/run_quality_experiment.py --output eval/results/week3_validate_new.json
.\.venv\Scripts\python.exe eval/run_quality_experiment.py --generate --output eval/results/week3_live_new.json
.\.venv\Scripts\python.exe -m pytest --basetemp=.pytest-week3-new-tmp --tb=short
uvx --offline --from ruff==0.17.2 ruff check src/research_agent/evaluation/quality.py eval/run_quality_experiment.py tests/unit/test_quality.py
uvx --offline --from ruff==0.17.2 ruff format --check src/research_agent/evaluation/quality.py eval/run_quality_experiment.py tests/unit/test_quality.py
```

默认仅核对题集和源文件摘要；真实运行只读基础集合，不重建索引。输出拒绝覆盖、每题落盘，无自动续跑；重跑需新路径且会重复计费。未建立完整索引快照。实验不覆盖增量集合、多轮问答和完整研究图，后续接入需要另验业务路径。

审核后只读汇总（不重跑API）：

```powershell
.\.venv\Scripts\python.exe eval/run_quality_experiment.py --review-report eval/results/week3_live_host_20261031.json --reviews eval/reports/week3_ai_reviews_20261031.json --output eval/results/week3_reviewed_new.json
```

实际结果见[第三周执行报告](../reports/week3_completion_20261031.md)。
