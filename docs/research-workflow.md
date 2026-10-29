# 专题研究工作流：接口、恢复与演示

## 执行与持久化

`workflow.py` 负责计划、分题取证、证据检查和报告汇总，`ResearchWorkflow` 由网页服务创建一次。所有任务状态通过进程内锁读写，JSON 使用同目录临时文件原子替换；外部调用在锁外执行，不阻塞状态查询或取消请求。任务不依赖 SSE 或浏览器连接，网页每 2 秒读取状态。

任务字段包括 `id`、`goal`、`allow_web`、`status`、`phase`、`plan`、`confirmed`、`steps`、`evidence`、`review`、`report`、`error`、`cancel_requested`、`updated_at`。计划包含 `scope`、`expected_output` 和 3–5 项 `questions`；每个子问题包含 `question`、`search_query`、`needs_current`、`news`。搜索词最长 80 字符，为搜索服务的语言和日期条件留出空间。

任务状态为 `awaiting_confirmation`、`running`、`waiting_continue`、`failed`、`completed`、`cancelled`；阶段 `phase` 区分 `planning`、`plan`、`research`、`review`、`report`、`completed`。创建时后台生成计划，状态为 `running / planning`；成功后等待确认，不自动执行取证。计划生成失败可以重试。

步骤状态为 `pending`、`running`、`succeeded`、`failed`、`skipped`。子问题额外保存 `local_done`、`web_done`、`evidence_labels` 和 `summary`；需要判断本地覆盖时，保存 `local_sufficient` 和 `local_gaps`。每次取得检索、覆盖检查或搜索结果后便保存，避免下游失败导致重新取证。子问题的阶段成果包含“阶段结论”和“待核实”。证据错误不等于无结果：调用异常使任务失败并暂停，空结果允许继续生成明确说明资料缺失的报告。

首次确认后计划锁定，之后只允许重试失败步骤或继续中断任务。重启时将原 `running` 任务标为 `waiting_continue`，未完成步骤回到 `pending`，已保存成果继续使用；已有取消标记的任务直接标为取消。刷新或关闭网页不会取消后台任务。取消在当前外部调用结束后的检查点生效，保留结果，剩余步骤跳过。

许可仅绑定当前研究任务，默认关闭。开启时仍先检索本地，随后针对无证据、覆盖不足或时效需求执行一次搜索；关闭时不调用联网工具。搜索使用现有 DeepSeek Search / Fetch、中文优先排序及条数和正文限制，不自动入库。原普通问答继续按轮授权，接口不变。

报告引用在任务内唯一：新资料 `[N#]`、旧资料 `[O#]`、网络 `[W#]`。来源列表记录已有来源元数据；本地资料保留文件、页码、相关性及获取日期，未结构化提取的发布机构、日期和适用范围提示核对原文；网络元数据沿用搜索模块。移除不存在的标签并提示，但不宣称已经验证引用语义。

## HTTP 接口

所有接口仅服务本地单用户演示，POST 沿用已有同源校验，JSON 请求。状态冲突返回 409，校验失败 400，任务不存在 404，存储失败 500。任务 ID 为 32 位小写十六进制字符。

| 方法与路径 | 请求或返回 |
| --- | --- |
| `POST /api/research/tasks` | `{goal, allow_web:false}`；返回 202 和任务详情，后台生成计划 |
| `GET /api/research/tasks` | `{tasks:[{id,goal,status,phase,updated_at}]}`，按更新时间倒序 |
| `GET /api/research/tasks/{id}` | 完整任务快照，包括当前步骤、证据和成果 |
| `POST /api/research/tasks/{id}/plan` | `{plan,allow_web}`；仅待确认阶段可保存 |
| `POST /api/research/tasks/{id}/confirm` | 可携带最终 `{plan,allow_web}`，校验并锁定计划后执行 |
| `POST /api/research/tasks/{id}/continue` | `{}`；仅 `waiting_continue` 可执行 |
| `POST /api/research/tasks/{id}/retry` | `{}`；仅 `failed` 可执行，保留已完成成果 |
| `POST /api/research/tasks/{id}/cancel` | `{}`；运行中设置取消标记，其余未结束任务立即取消 |
| `GET /api/research/tasks/{id}/report` | 仅完成后返回 Markdown 附件，其他状态 409 |

同时只接受一个后台研究任务，不排队；重复确认或忙碌期间启动其他任务返回 409。计划编辑和执行确认使用统一校验，至少 3 题、最多 5 题，不允许重复子问题。

## 演示脚本

真实服务使用原来的 `web` 命令；会调用付费模型，联网另需明确授权。本次新增工作流不沿用历史 RAG 评估的准确率、响应时间或成功率结论。

无需模型、Milvus 或外部搜索的可复现入口：

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe tests/manual_workflow_demo.py --port 8877 --delay 2
```

打开 `http://127.0.0.1:8877`。该入口以模拟器替换研究模型、本地检索和搜索，证据与真实数据完全隔离，保存到 `work/workflow-demo/research_tasks/`。不要在此入口测试普通问答或资料导入，它们不属于模拟范围。

1. 输入“研究某项技术的应用场景、实施条件与局限”，保持联网关闭，生成计划。检查 3 个子问题，编辑范围及预期成果，保存。
2. 在确认阶段勾选任务级联网许可，确认执行。展开阶段成果和证据快照，观察本地、新旧版本与模拟网络来源。
3. 执行中刷新页面，任务继续；关闭后重新打开页面，使用任务列表恢复查看。
4. 运行期间在终端 `Ctrl+C` 停止模拟服务，以同一命令和目录重启。页面应出现“等待继续”，点击后复用已完成结果继续。
5. 查看证据检查记录：新版20与旧版10分别保留，不能合并。报告明确标注模拟资料，不构成真实结论；下载 Markdown。
6. 新建另一个任务，执行中取消。当前调用结束后状态应为已取消，之后的步骤不再执行。
7. 顶部切换普通问答再返回专题研究，检查原有入口仍可访问。

## 检查与验证边界

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m pytest --basetemp=.pytest-workflow-final-tmp
node --check src/research_agent/static/workflow.js
uvx --from ruff==0.17.2 ruff check src/research_agent/workflow.py tests/test_workflow.py tests/manual_workflow_demo.py
uvx --from ruff==0.17.2 ruff format --check src/research_agent/workflow.py tests/test_workflow.py tests/manual_workflow_demo.py
```

离线测试使用模拟模型与取证工具，覆盖授权、计划编辑、失败重试、取消、恢复及真实本地 HTTP 请求。浏览器模拟验收只能证明界面与流程衔接，不能证明真实 DeepSeek 计划质量、检索覆盖、搜索质量、事实准确率、真实服务稳定性或任务费用。历史 RAG 数据继续保留在原验收文档中。

2026-10-29 实施验证：完整默认测试 **71 passed, 1 deselected**，其中新增工作流测试 19 项；1 项真实嵌入集成测试按默认配置未选择。四个涉及改动的 Python 文件通过 Ruff lint 与 format 检查，新增网页脚本通过 `node --check`，差异通过 `git diff --check`。

浏览器使用上述模拟入口，已验证计划编辑与保存、确认时授权、执行中刷新、第二个子问题执行期间停止服务并重启、从检查点继续并完成报告、下载 Markdown、切换普通问答，以及执行中取消并跳过剩余步骤。没有调用真实 DeepSeek、Milvus 或外部搜索，未做真实费用、端到端质量、并发性能或移动端视觉验收。

JSON 中间成果属于本地私有数据，默认目录已忽略。若自定义 `CONVERSATION_DIR` 到版本库内其他位置，需自行将对应任务目录加入忽略规则。仅支持一个本地服务进程，不提供多进程锁、复杂 DAG、多代理、定时任务、执行后编辑计划或外部发布。

## 真实案例验收

2026-10-30 已使用本地上海地方志完成一次关闭联网搜索的真实《安亭志》专题研究。真实模型、本地检索、HTTP确认与下载、持久化重开通过；内容审核发现跨子题证据遗漏和日期语义错误，研究内容未通过完整验收。原始报告、证据、用量和审核记录见[真实验收报告](research-acceptance-2026-10-30.md)，不能将本案例作为独立准确率或全面质量通过的证明。
