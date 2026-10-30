# 专题研究工作流：接口、恢复与演示

## 执行与持久化

`research_graph.py` 定义 LangGraph StateGraph，`workflow.py` 提供业务节点和后台服务，`ResearchWorkflow` 由网页服务创建一次。SQLite checkpoint 记录图状态与恢复位置；JSON 保留为网页视图和人工决定记录。计划确认及按需联网询问使用原生 `interrupt()`，接口通过 `Command(resume=...)` 回答。所有任务状态通过进程内锁读写，JSON 使用同目录临时文件原子替换；外部调用在锁外执行，不阻塞状态查询或取消请求。任务不依赖 SSE 或浏览器连接，网页每 2 秒读取状态。

任务字段包括 `id`、`goal`、`allow_web`、`web_consent`、`web_consent_request`、`status`、`phase`、`plan`、`confirmed`、`steps`、`evidence`、`review`、`report`、`error`、`cancel_requested`、`updated_at`。计划包含 `scope`、`expected_output` 和 3–5 项 `questions`；每个子问题包含 `question`、`search_query`、`needs_current`、`news`。搜索词最长 80 字符，为搜索服务的语言和日期条件留出空间。

任务状态为 `awaiting_confirmation`、`running`、`waiting_web_consent`、`waiting_continue`、`failed`、`completed`、`cancelled`；阶段 `phase` 区分 `planning`、`plan`、`research`、`review`、`report`、`completed`。创建时后台生成计划，状态为 `running / planning`；成功后等待确认，不自动执行取证。计划生成失败可以重试。

步骤状态为 `pending`、`running`、`succeeded`、`failed`、`skipped`。子问题额外保存 `local_done`、`web_done`、`evidence_labels` 和 `summary`；需要判断本地覆盖时，保存 `local_sufficient` 和 `local_gaps`。每次取得检索、覆盖检查或搜索结果后便保存，避免下游失败导致重新取证。子问题的阶段成果包含“阶段结论”和“待核实”。证据错误不等于无结果：调用异常使任务失败并暂停，空结果允许继续生成明确说明资料缺失的报告。

首次确认后计划锁定，之后可重试失败步骤、继续中断任务或回答联网许可问题，不再编辑计划。重启时将原 `running` 任务标为 `waiting_continue`，未完成步骤回到 `pending`，已保存成果继续使用；已有取消标记的任务直接标为取消。刷新或关闭网页不会取消后台任务。取消在当前外部调用结束后的检查点生效，保留结果，剩余步骤跳过。

网页不再提供联网许可复选框。新建任务使用 `ask_web_consent:true`，初始 `allow_web:false`、`web_consent:unknown`。先检索本地，无证据、覆盖不足或有时效需求时保存本地结果并暂停为 `waiting_web_consent`，通过 `web_consent_request` 提供问题、原因和拟搜索词。此时释放执行槽位，不执行搜索；用户同意后将许可设为 `allowed` 并继续，拒绝则为 `declined` 并仅用本地资料继续，同一任务不重复询问。许可仅适用于当前研究；刷新和服务重启保留等待状态。检索异常仍作为失败，不当成资料缺失；配置关闭搜索时不询问或搜索。搜索不自动入库，普通问答继续按轮授权。

为兼容已有调用，创建接口省略 `ask_web_consent` 时保留原 `allow_web` 行为，旧任务没有新字段时按已有许可执行。保存或确认计划省略 `allow_web` 不覆盖已有许可；显式传布尔值仍兼容旧客户端。网页的确认计划请求不包含授权决定。

报告引用在任务内唯一：资料库 `[S#]`、网络 `[W#]`。来源列表记录已有来源元数据；本地资料保留文件、页码、相关性及获取日期，未结构化提取的发布机构、日期和适用范围提示核对原文；网络元数据沿用搜索模块。移除不存在的标签并提示，但不宣称已经验证引用语义。

## HTTP 接口

所有接口仅服务本地单用户演示，POST 沿用已有同源校验，JSON 请求。状态冲突返回 409，校验失败 400，任务不存在 404，存储失败 500。任务 ID 为 32 位小写十六进制字符。

| 方法与路径 | 请求或返回 |
| --- | --- |
| `POST /api/research/tasks` | `{goal, ask_web_consent:true}`；返回 202 和任务详情，后台生成计划 |
| `GET /api/research/tasks` | `{tasks:[{id,goal,status,phase,updated_at}]}`，按更新时间倒序 |
| `GET /api/research/tasks/{id}` | 完整任务快照，包括当前步骤、证据和成果 |
| `POST /api/research/tasks/{id}/plan` | `{plan}`；仅待确认阶段可保存，旧客户端可选传 `allow_web` |
| `POST /api/research/tasks/{id}/confirm` | 可携带最终 `{plan}`，校验并锁定计划后执行 |
| `POST /api/research/tasks/{id}/web-consent` | `{allow_web:true或false}`；仅等待许可状态可用，保存决定并从检查点继续 |
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
.\.venv\Scripts\python.exe scripts/manual_workflow_demo.py --port 8877 --delay 2
```

打开 `http://127.0.0.1:8877`。该入口以模拟器替换研究模型、本地检索和搜索，证据与真实数据完全隔离，保存到 `work/workflow-demo/research_tasks/`。不要在此入口测试普通问答或资料导入，它们不属于模拟范围。

1. 输入“研究某项技术的应用场景、实施条件与局限”，保持联网关闭，生成计划。检查 3 个子问题，编辑范围及预期成果，保存。
2. 确认计划并执行，等待联网询问后选择“允许本次研究联网”。展开阶段成果和证据快照，观察本地、新旧版本与模拟网络来源。
3. 执行中刷新页面，任务继续；关闭后重新打开页面，使用任务列表恢复查看。
4. 运行期间在终端 `Ctrl+C` 停止模拟服务，以同一命令和目录重启。页面应出现“等待继续”，点击后复用已完成结果继续。
5. 查看证据检查记录：不同版本的20与10分别保留，不能合并。报告明确标注模拟资料，不构成真实结论；下载 Markdown。
6. 新建另一个任务，执行中取消。当前调用结束后状态应为已取消，之后的步骤不再执行。
7. 顶部切换普通问答再返回专题研究，检查原有入口仍可访问。

## 检查与验证边界

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m pytest --basetemp=.pytest-workflow-final-tmp
node --check src/research_agent/static/workflow.js
uvx --from ruff==0.17.2 ruff check src/research_agent/workflow.py tests/integration/test_workflow.py scripts/manual_workflow_demo.py
uvx --from ruff==0.17.2 ruff format --check src/research_agent/workflow.py tests/integration/test_workflow.py scripts/manual_workflow_demo.py
```

离线测试使用模拟模型与取证工具，覆盖授权、计划编辑、失败重试、取消、恢复及真实本地 HTTP 请求。浏览器模拟验收只能证明界面与流程衔接，不能证明真实 DeepSeek 计划质量、检索覆盖、搜索质量、事实准确率、真实服务稳定性或任务费用。历史 RAG 数据继续保留在原验收文档中。

历史验证（2026-10-29）：完整默认测试 **71 passed, 1 deselected**，其中新增工作流测试 19 项；1 项真实嵌入集成测试按默认配置未选择。四个涉及改动的 Python 文件通过 Ruff lint 与 format 检查，新增网页脚本通过 `node --check`，差异通过 `git diff --check`。

浏览器使用上述模拟入口，已验证计划编辑与保存、确认时授权、执行中刷新、第二个子问题执行期间停止服务并重启、从检查点继续并完成报告、下载 Markdown、切换普通问答，以及执行中取消并跳过剩余步骤。没有调用真实 DeepSeek、Milvus 或外部搜索，未做真实费用、端到端质量、并发性能或移动端视觉验收。

JSON 中间成果属于本地私有数据，默认目录已忽略。若自定义 `CONVERSATION_DIR` 到版本库内其他位置，需自行将对应任务目录加入忽略规则。仅支持一个本地服务进程，不提供多进程锁、复杂 DAG、多代理、定时任务、执行后编辑计划或外部发布。

## 真实案例验收

2026-10-30 已使用本地上海地方志完成一次关闭联网搜索的真实《安亭志》专题研究。真实模型、本地检索、HTTP确认与下载、持久化重开通过；内容审核发现跨子题证据遗漏和日期语义错误，研究内容未通过完整验收。原始报告、证据、用量和审核记录见[真实验收报告](research-acceptance-2026-10-30.md)，不能将本案例作为独立准确率或全面质量通过的证明。

## 来源分类调整（2026-10-30）

普通问答、CLI 与专题研究统一使用资料库 `[S#]` 和网络 `[W#]`。资料库跨现有基础、增量及历史独立集合检索，按相关性排序并去重后取 `TOP_K` 条，无需修改现有向量库结构或重新入库。报告来源区按两类分组，日期与版本冲突继续分别呈现。旧任务的已有引用保留原标签并归入资料库，重试或继续时不会重写已保存证据及报告。

本次来源分类调整验证：完整默认测试 73 passed、1 deselected；新增跨集合排序去重、统一来源事件、历史引用兼容与 HTTP SSE 字段检查。13 个改动 Python 文件通过 Ruff lint 和 format 检查；页面内联脚本及 workflow.js 语法检查通过，模拟 DOM 验证来源区仅分资料库和网络。未执行真实模型回答、真实网络搜索或固定评估集质量对比；真实嵌入集成测试仍按默认配置未选择。

## 按需询问联网与输入区对齐（2026-10-30）

问答和研究空白首页共享网格行高、间距及底部留白，标题和输入框对齐；研究首页和计划确认区均移除联网授权复选框。授权暂停发生在搜索之前，本地证据与覆盖检查已落盘，继续不重复检索；拒绝后保留缺口说明，等待期间支持取消。

本次验证：完整默认测试 80 passed、1 deselected；改动 Python 文件通过 Ruff lint 与 format 检查。离线模拟浏览器验证了确认计划后按需询问、刷新保留等待、同意后完成含网络来源的报告，以及拒绝后仅用本地资料完成。正式页面已实测桌面与375px下问答和研究输入框的 x/y/宽/高一致；未调用真实模型或真实搜索。


## LangGraph 迁移与学习

依赖由 uv 锁定为 LangGraph 1.2.18、langgraph-checkpoint-sqlite 3.1.2；不需要 LangSmith 账号或独立数据库服务。运行 `uv sync --locked` 更新虚拟环境。

研究任务目录新增 `checkpoints.sqlite`，可能同时出现 SQLite 日志文件；这些文件与 JSON 一样包含私有研究数据，默认目录已忽略。`thread_id` 使用原任务 ID，服务每次执行以 `durability="sync"` 在节点边界落盘。网页接口、轮询和“资料库 / 网络”来源契约保持兼容，普通问答不迁移。

旧 JSON 任务在下一次确认、继续或重试时惰性初始化图，复用成功步骤、local_done/web_done 和既有证据，不重新入库；已完成任务可继续直接查看。不要单独删除 SQLite 后将其当作正常恢复手段，也不要在执行中单独还原 JSON 文件。SQLite 和 JSON 之间没有跨存储事务，崩溃窗口仍可能重复外部调用。

可执行学习练习、概念与实际节点对应、恢复参数和断言讲解见 [跟着本项目学习 LangGraph](langgraph-learning.md)。新增验证入口：

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m pytest tests/integration/test_langgraph_migration.py tests/integration/test_workflow.py --basetemp=.pytest-langgraph-tmp
.\.venv\Scripts\python.exe examples/learn_langgraph.py start --thread lesson1
.\.venv\Scripts\python.exe examples/learn_langgraph.py inspect --thread lesson1
.\.venv\Scripts\python.exe examples/learn_langgraph.py approve --thread lesson1
```

前述 2026-10-29 测试数字属于迁移前记录，不应视为 LangGraph 版本验收结论。迁移验证使用真实框架和 SQLite，模型及搜索使用模拟器；不会据此宣称真实模型研究质量已提升。

2026-10-30 迁移验证：完整默认测试 **92 passed, 1 deselected**，其中新增迁移测试 12 项；真实嵌入集成测试按默认配置未选择。验证了原有 HTTP 契约、原生计划/联网中断、重建服务后的恢复、失败节点位置、历史 JSON 惰性接入、不同 thread 隔离、五题流程上限、人工决定在中断重放期间保留，以及错误记录去除上游正文。学习练习的批准/拒绝通过独立进程测试，也已手动运行 start → inspect → approve。

四个改动 Python 文件通过 Ruff lint 和 format 检查；现有网页脚本通过 Node 语法检查，diff 无空白错误。网页交互和 HTTP 接口未改变；本次迁移没有重新做浏览器视觉验收，也没有调用真实模型、Milvus 或联网搜索，不改变此前真实研究内容验收的结论。
