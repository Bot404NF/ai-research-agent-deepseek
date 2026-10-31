# 第一周：建立可复现的评测基线

本次只扩展评测，不调整检索、模型提示词、索引或业务流程。历史冻结数据与结果保持不变。实际结果见 [本次报告](../reports/week1_baseline_20261031.md)。

后续已按用户要求完成全部 56 题的 AI 逐题复核，见[收尾结果与教学案例](../reports/week1_completion_20261031.md)。AI 复核与真人审核分别统计；本轮另修复了配置对象打印密钥的风险。

## 1. 先沿代码读清楚测量对象

| 路径 | 实际调用链 | 本轮覆盖 |
|---|---|---|
| 单次 RAG | `retrieval.retrieve` → BGE-M3 → Milvus 基础集合 → `answer.SYSTEM_PROMPT/render_context` → ChatOpenAI → DeepSeek | 真实检索、单次回答、服务端 usage、延迟 |
| 普通问答 | `agent.stream_answer_turn` → 确定性 `search_documents` → `retrieve_library` 合并基础/增量集合 → 受控工具循环 → 回答 → 追问建议 → SQLite 会话 | 复用已有离线回归；本轮无真实全链路成本统计 |
| 专题研究 | `workflow.ResearchWorkflow` → `research_graph.build_research_graph` → plan/approval/local/coverage/web_consent/web_search/summarize/review/report → SQLite checkpoint | 复用已有图路由、暂停/恢复、HTTP 测试；本轮不新增真实研究任务 |
| 网络工具 | `web_search.py` → DeepSeek Search/Fetch，受联网授权约束 | 本轮不联网搜资料、不计搜索成本 |

依赖由 `pyproject.toml` 与 `uv.lock` 管理，使用 uv。当前不是 PostgreSQL，也不是多 Agent 系统。`tests/integration/` 目录不等于全部需要外部服务，只有显式 `integration` 标记的真实模型检查默认排除。`.env` 由配置模块读取，评测不输出密钥或完整配置。

学习原理：先固定“测量的是哪条路径”。单次回答省略了工具循环和追问调用，不能直接作为真实任务的总成本或耗时。

## 2. 数据分层与证据边界

`datasets/week1.json` 共 56 题：40 道历史单来源题、4 道合成无答案题、8 道多证据组合题、4 道要求忽略证据的边界干扰题。组合题复用已有事实与参考答案，干扰题复用已检查缺失的合成实体；它们增加场景覆盖，不增加独立事实样本。每题保留来源题 ID 与数据集归属。

这是**公开回归集**，不是新的独立验收集。原 20 道 holdout 正题已用于后续调优；不能恢复其独立性。下一轮独立验收需另采集未参与调优的真实问题，覆盖版本冲突、跨文档论证、多轮指代、真实证据不足和工具故障。

运行前检查 16 个正样本源文件的 SHA-256 和锚点存在性，在当前所有 Markdown 中重新检查合成实体是否缺失。缺失检查只证明该字符串未出现，不证明整个知识库语义上无答案，也不覆盖 PDF 或图像。古籍 OCR、人物角色和版本仍需对照原件复核。

如需重建衍生题，执行 `python eval/datasets/build_week1.py`；题集摘要改变后必须生成新结果，不能将旧审核直接套用。

## 3. 指标怎么读

| 指标 | 计算口径 | 不能证明什么 |
|---|---|---|
| `anchor_hit_at_4` | 正样本前四条中，指定来源的一条候选含该子问题全部锚点；组合题要求所有子问题均命中 | 自动候选命中，不等于人工语义 Hit@4；分散支持和 OCR 差异可能造成假阴性 |
| `annotated_recall_at_4_macro` | 每题被前四条覆盖的已标注锚点数 / 标注锚点总数，再按题平均；重复候选不重复计数 | 未穷举相关 chunk，因此不是全库 chunk Recall@4 |
| 人工 `hit_at_4` | 前四条是否足够完整回答整题，满足题目限定的书名/版本/卷次 | 需人工逐题判断；无答案题不入分母 |
| 人工 `answer_correct` | 可回答题：核心事实、角色、日期和问题各部分均正确；遗漏/截断/不合理拒答记 false | 与锚点覆盖分开判断；参考答案也可被复核纠正 |
| 引用标签有效率 | 原始回答中有效标签出现次数 / 全部标签出现次数 | 标签存在不等于支持事实；无引用分母为空，不算 100% |
| 人工 `citation_supported` | 对每条带引文的事实检查来源支持、限定范围与版本；整题全部支持才 true | 本版为整题通过率，不冒充逐句引用准确率；无引用题用引用覆盖数另报 |
| 人工 `abstention_correct` | 无答案题明确不作确定事实断言；夹带猜测记 false | 自动拒答短语只筛候选，不计拒答成功 |
| P50 / P95 | 复用已有 `distribution`：中位数 / 最近秩 `ceil(0.95*n)` | 小样本尾延迟不稳定；不代表并发吞吐量或 SLA |

报告同时给样本数、失败数、审核覆盖量和分层统计。检索性能先单独计入进程导入、模型初始化和第一次查询，再统计预热后查询。每题可重复检索，生成只执行一次；端到端样本为最后一次检索 + 单次回答，不含额外重复测量，不含工具循环/追问。失败耗时另报，不混入成功延迟。当前没有流式 TTFT 测量。

学习例子：两条证据分别覆盖“甲”“乙”，已标注 Recall 可以为 1，但该组的同条锚点 Hit 为 0。应检查证据是否合起来已足够回答，人工 Hit 可能为 true。这正是自动代理指标与语义评测分开的原因。

## 4. 运行与验证

在项目根目录 PowerShell 执行；输出路径必须为新文件，避免覆盖历史结果。

```powershell
$env:PYTHONPATH = 'src'
$env:HF_HUB_OFFLINE = '1' # 仅在模型已缓存后设置，不下载新权重

# 仅校验本地题集和原文，不调用 Milvus / DeepSeek
.\.venv\Scripts\python.exe eval/run_baseline.py --output eval/results/week1_validate_new.json

# 只读基础集合；每题测量三次检索，不调用 DeepSeek
.\.venv\Scripts\python.exe eval/run_baseline.py --retrieve --repeats 3 --output eval/results/week1_retrieval_new.json

# 先用 --limit 2 熟悉输出，再选择全量；会产生真实 API 用量
.\.venv\Scripts\python.exe eval/run_baseline.py --generate --limit 2 --output eval/results/week1_smoke_new.json
.\.venv\Scripts\python.exe eval/run_baseline.py --generate --repeats 3 --output eval/results/week1_live_new.json

# 离线回归；包含使用模拟服务的 LangGraph 与本机 HTTP 检查
.\.venv\Scripts\python.exe -m pytest --basetemp=.pytest-week1-new-tmp
uvx --offline --from ruff==0.17.2 ruff check src/research_agent/evaluation tests/unit/test_baseline.py eval/run_baseline.py eval/datasets/build_week1.py
uvx --offline --from ruff==0.17.2 ruff format --check src/research_agent/evaluation tests/unit/test_baseline.py eval/run_baseline.py eval/datasets/build_week1.py
```

`--generate` 隐含真实检索。生成使用项目单次回答提示词，温度 0、700 输出 Token、60 秒超时、无 SDK 自动重试；这是受限评测配置，和业务默认重试及 Token 上限不同。原始回答不做引用清洗，便于发现模型错误；`finish_reason=length` 时需审核是否截断。数据集与源文件版本不匹配即停止，不为跑通而改写摘要。

逐题保存后可保留已完成结果，但本运行器没有自动续跑，重跑会重复产生费用。外部调用失败只保存异常类型，避免诊断文本带出凭据。失败用量未知，不能按零费用计。受阻状态保存在 JSON；脚本完成写盘不等于质量通过，需检查 `status/summary.failed`。

## 5. Token 和成本

`usage` 保存服务端的实际用量，包括缓存明细；不按字符数估算 Token。汇总给出已观测请求数和 Token，若有失败/缺失记录，总量只是已观测部分。价格未配置或缓存拆分未知，成本为 null。

可通过 `--pricing 路径` 提供 JSON，字段为 `model`、`currency`、`effective_date`、`source_url`、`input_miss_per_million`、`input_hit_per_million`、`output_per_million`。价格应根据实际账户/模型的官方计费页核实，本仓库不填假价格。公式：

`[(输入Token - 缓存命中Token) × 未命中单价 + 缓存命中Token × 命中单价 + 输出Token × 输出单价] / 1,000,000`

这只是指定价格下的 API 估算，不是账单；不包含搜索、抓取、重试、研究任务与基础设施。`scripts/cost_analysis.py` 现已实现离线重算，未知字段保留为空；`benchmark.py` 仍为历史占位，性能统计使用基线模块。

## 6. 人工审核与下一步学习

每次运行生成同名 `.reviews.json` 模板，绑定题集摘要与原始结果摘要。对照 `week1.json` 的问题/参考答案、原始结果的 `retrieved` 与 `answer`，填 true / false / null，并写判断理由。null 表示未审核或不适用，不能填成 false 或计入已审核分母。`hit_at_4` 仅正题；`answer_correct` 仅已生成的正题；`abstention_correct` 仅已生成的负题；`citation_supported` 仅带引用的回答。

```powershell
.\.venv\Scripts\python.exe eval/run_baseline.py --review-report eval/results/week1_live_new.json --reviews eval/results/week1_live_new.reviews.json --output eval/results/week1_reviewed_new.json
```

汇总审核不访问外部服务，也不重复生成。当前不接自动 LLM judge，避免把同模型判断包装成人工金标准；若以后接入，必须另记 judge 模型、提示词版本、费用，并用人工样本校准。

审核文件可增加 `reviewer.kind = "ai"` 和审核方法、日期。此时结果写入 `semantic_ai_review`，真人 `semantic_manual` 的审核数保持 0；旧文件未声明类型时兼容为 human。无引用的回答必须将 `citation_supported` 设为 null；正/负题各自使用正确的指标字段。

## 7. 本轮收尾命令：只读原始结果，不重复生成

以下输出名是示例，每次使用新路径。收尾时 Milvus 未启动的一轮真实工具 0/4 已保留；用户启动后重跑为 4/4。再次执行工具需要现有服务可连接，不运行 ingest。

```powershell
$env:PYTHONPATH = 'src'

# 阅读指定题号的全部被引证据；未命中题和无引用题显示全部候选
.\.venv\Scripts\python.exe eval/inspect_week1.py Q13 H04 M05

# 复用已完成的56题AI复核，不调用judge或生成API
.\.venv\Scripts\python.exe eval/run_baseline.py --review-report eval/results/week1_live_20261031.json --reviews eval/reports/week1_ai_reviews_20261031.json --output eval/results/week1_reviewed_new.json

# 用有来源、查询日期和适用时段的公开价格，离线重算费用
.\.venv\Scripts\python.exe scripts/cost_analysis.py --report eval/results/week1_live_20261031.json --pricing eval/experiments/deepseek_v4_pro_pricing_20261031.json --output eval/results/week1_costs_new.json

# 默认8个受控场景；显式加 --live-local 才额外调用4次本地检索工具
.\.venv\Scripts\python.exe eval/run_tool_baseline.py --output eval/results/week1_tools_offline_new.json
.\.venv\Scripts\python.exe eval/run_tool_baseline.py --live-local --output eval/results/week1_tools_live_new.json

# 测试不使用真实密钥；防止失败回溯意外打印外部配置
$env:DEEPSEEK_API_KEY = ''
.\.venv\Scripts\python.exe -m pytest --basetemp=.pytest-week1-completion-new-tmp --tb=short
```

不要将离线价格选项传给 `--review-report`：费用分析单独输出、绑定原始报告摘要，避免审核汇总悄悄忽略价格或改变原始结果。AI 判分不是调用同一生成模型自动匹配参考答案，而是本轮执行者逐题阅读证据后写下的版本化评审；仍不是独立真人金标准。

建议学习顺序：

1. 先读 Q01 与 H01 的源锚点和前四条候选，手算 Hit / 已标注 Recall，再核对 JSON。
2. 再读 M01 等组合题，区分“找到部分答案”和“整题证据充分”，观察按层汇总为何比单一总分有用。
3. 审核 N01 与 B01 的拒答，确认有拒答短语时是否仍编造确定事实。
4. 对比单次回答 Token 与 `agent.py`、`workflow.py` 中额外调用的位置，理解完整任务为何更贵。
5. 人工复核后另建一组未用于调优的真实题，再开始第二周单变量实验。固定模型、题集、资料版本和检索参数，一次只改 chunk、阈值或 prompt 中的一项。
