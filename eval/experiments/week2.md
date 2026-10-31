# 第二周：分题检索单变量实验

## 范围与验收标准

第一周已建立分层基线，主要遗留问题之一是组合题的候选集中在一个子问题。本周完成“问题拆分 → 分题检索 → 证据合并”的可复现实验，并验证对回答的影响。版本角色拼接与引用遗漏另立后续提示词实验，本轮不混改。

| 工作 | 本轮交付 |
|---|---|
| 固定数据 | 保留第一周56题原样，追加用户授权编写的W201跨文档开发题；不声称独立真人验收 |
| 固定控制变量 | 同一基础集合、BGE-M3、当前阈值、每次查询取4条、最终最多4条；生成使用相同当前模型和提示词 |
| 唯一实验策略 | 整题单查询对比连续①②编号拆分；分题候选轮流选取，来源/页码/正文去重并重编号 |
| 运行真实对照 | 57题两组各3次检索，冷热启动分开；记录失败与逐题改进/退化 |
| 回答验证 | 仅9道编号组合题，两组使用同一配置分别生成；保存18份原始回答、usage、引用标签检查和语义复核 |
| 自动回归 | 检查不读答案、最终预算、跨版本保留、部分失败、负题分母和生成异常；运行项目测试与lint/format |
| 决策 | 根据证据保留实验策略或安排独立业务接入；本轮不改变默认业务路径，不重建索引 |

上线候选门槛预先设为：8道旧组合题至少新增1道整题锚点命中、无原命中退化；40道单来源题不得退化；W201单列；最终证据不超过4条；预热分题检索P95不超过整题的3倍；组合题AI整题答案与引用支持不能出现新增退化。满足门槛只支持继续验证，不能证明真实用户问题整体效果提升。真实复杂缺证、多轮指代和工具故障继续使用已有回归，本轮不声称新增真实验收。

## 实验解释

拆分只读取问题中的连续①②③④，不读取题号、参考答案、来源标注或gold锚点。各子题必须自带书名/范围；编号缺失、顺序错误或空子题保留原查询。前缀只作回答指令；因此实验限于子题自身范围完整的显式编号问题，不代表通用自然语言问题拆解。两个子题各检索4条意味着检索调用数与候选总量增加，最终上下文预算仍为4条，必须同时报告延迟代价。

首轮查询单独预热；重复轮内交替两组顺序，降低缓存和执行先后的偏差。若任意子查询失败，该策略记失败；配对质量指标只统计两组检索均成功的正题，失败数和覆盖分母另报。空结果是成功执行但证据不足，不能算正确拒答。负题不入Hit/Recall分母，本轮不重跑它们的生成。

自动锚点Hit与已标注证据Recall复用第一周定义；不等于语义充分性、全库Recall或答案正确率。AI复核需阅读候选和原始回答，分别记录整题证据、整题答案及引用支持；与真人审核分开。回答生成无自动重试，温度0，最多700输出Token，不清洗原始引用。单次生成失败保留检索结果，但不得计生成成功。

题集摘要及引用源文件摘要运行前校验，代码摘要和参数写入结果；不读取或改写向量集合。未建立完整索引快照或chunk级索引指纹，跨环境复现还须人工确认索引版本一致。当前模型读取项目配置；不能将第一周Pro生成成绩与当前Flash直接比较。

## 复现

在项目根目录PowerShell执行，使用新的输出名；默认仅校验，不访问服务。

```powershell
$env:PYTHONPATH = 'src'
$env:HF_HUB_OFFLINE = '1'
.\.venv\Scripts\python.exe eval/run_split_experiment.py --output eval/results/week2_validate_new.json
.\.venv\Scripts\python.exe eval/run_split_experiment.py --retrieve --output eval/results/week2_retrieval_new.json
# 组合题两组各生成一次，共最多18次API请求；单来源/负题仅检索
.\.venv\Scripts\python.exe eval/run_split_experiment.py --generate --output eval/results/week2_answers_new.json
.\.venv\Scripts\python.exe -m pytest --basetemp=.pytest-week2-new-tmp --tb=short
uvx --offline --from ruff==0.17.2 ruff check src/research_agent/evaluation/split_retrieval.py tests/unit/test_split_retrieval.py eval/run_split_experiment.py eval/datasets/build_week2.py
uvx --offline --from ruff==0.17.2 ruff format --check src/research_agent/evaluation/split_retrieval.py tests/unit/test_split_retrieval.py eval/run_split_experiment.py eval/datasets/build_week2.py
```

生成命令同时执行检索；每题保存一次进度，无自动续跑，重跑会重复计费。默认输出同名`.reviews.json`模板，绑定题集和结果摘要。AI复核另存，不覆盖空白真人模板。成本没有核实当前Flash价格时保留null，不沿用Pro价格或把未知费用写成0。

复核后只读汇总，不重复生成：

```powershell
.\.venv\Scripts\python.exe eval/run_split_experiment.py --review-report eval/results/week2_live_20261031.json --reviews eval/reports/week2_ai_reviews_20261031.json --output eval/results/week2_reviewed_new.json
```

已执行结果及不采用当前策略的理由见[第二周结果](../reports/week2_completion_20261031.md)。

`eval/datasets/build_week2.py`用于首次构建数据，拒绝覆盖已存在的week2.json。W201锚点已核对当前Markdown，但未校勘扫描原件，也不是随机抽样或独立真实提问。
