# 知研 · 专题研究工作流助手

输入研究目标，确认可编辑的研究计划，由系统逐题收集证据、保存阶段成果、检查资料缺口与冲突，再汇总为可下载的 Markdown 报告。页面采用深色界面，默认进入“问答”，顶部胶囊按钮可切换至“研究”；侧栏依次为资料库、研究任务、历史会话。

工作流采用 LangGraph StateGraph 与本地 SQLite checkpoint，单进程后台执行与网页连接解耦；计划确认和按需联网询问通过 interrupt 暂停，再由网页回答后恢复。本地 BGE-M3 / Milvus 检索和 DeepSeek Search / Fetch 是取证工具，DeepSeek 负责计划、分题分析、证据检查与报告汇总。刷新页面或重启服务后可以继续未完成步骤。

## 完成一次专题研究

1. 运行 `uv run python -m research_agent.cli web`，在网页输入研究目标；无需预先选择联网许可。
2. 生成 3–5 个子问题后，编辑研究范围、预期成果、子问题及搜索词。点击“确认计划并执行”；此后计划锁定，先检索资料库。
3. 查看逐题进度、阶段结论、待核实事项和证据快照。每题先查本地资料；发现资料缺口、时效需求或新闻查询时，系统暂停并询问是否允许本次研究联网；同意后才搜索，拒绝则仅用资料库继续，每题最多一次搜索，同一任务不重复询问。
4. 查看证据检查记录和研究报告，下载 Markdown。不同版本的结论分别呈现，证据不足时明确说明。

每次任务只执行一轮计划，不循环补搜；每题最多一次本地检索和一次搜索（正文抓取复用现有上限）。按需询问联网或已授权的任务，在本地有命中的非时效问题上会先调用模型判断覆盖是否充分。3–5 题的完整任务最多 13 次逻辑模型请求，每次模型请求沿用最多 1 次重试；搜索、抓取及人工重试可能额外计费。该上限不是总耗时保证。

[结合本项目学习 LangGraph](docs/langgraph-learning.md)：从无 API 的暂停/恢复练习开始，再阅读实际研究图和恢复测试。

任务目录中的 `checkpoints.sqlite` 保存 LangGraph 执行历史及私有研究数据，与任务 JSON 一并保留；迁移前的 JSON 任务在下次继续时按已有成果初始化图，不需要重建索引。

任务记录默认位于 `CONVERSATION_DIR` 的同级目录 `research_tasks/`（默认 `data/research_tasks/`），包含目标、许可、证据快照和报告，不应提交到版本控制。每个外部调用结果取得后保存检查点，失败重试复用已保存成果；若调用成功后尚未落盘便退出，恢复时可能再次调用，不能保证付费请求恰好执行一次。

同一服务进程同时只运行一个研究任务，其他启动请求返回状态冲突。关闭网页不停止任务；服务重启后任务显示“等待继续”，需用户主动继续。取消在当前外部调用返回后生效，保留已取得结果。不要同时运行多个服务进程操作同一任务目录。

- [工作流接口、状态与演示脚本](docs/research-workflow.md)
- [网页设计规范：深色对话界面与问答／研究切换](docs/web-design-spec.md)
- [真实专题研究验收：流程通过，内容仍有缺陷](docs/research-acceptance-2026-10-30.md)
- [项目介绍、架构与简历素材](docs/resume-project.md)

## 本地资料与历史检索评估

项目已接入 Milvus，并已加入用户提供的上海地方志资料集。资料目录及来源记录约定见 [资料目录说明](data/documents/README.md)；现有Markdown可能存在识别错字和重复，尚未逐页核对扫描原件。

以下是 2026-10-20 的历史 RAG 评估，不代表新增工作流的端到端效果：当时切换 DeepSeek-V4-Pro 并重跑小样本回归，开发集 Hit@4 为14/20，原验收集复用回归为16/20；16次真实生成全部成功，12道可回答题中10道核心答案一致、1道部分、1道合理拒答，4道合成无答案题均拒答。60次预热检索中位数49 ms，检索加单次回答中位数1.74 s。题集已参与提示词优化，这些结果不代表新独立验收、全库准确率或并发能力。

- [当前模型回归与性能口径](docs/acceptance-2026-10-20.md)
- [项目介绍、架构与简历素材](docs/resume-project.md)
- [评估入口与复现方法](eval/README.md)

已有索引时，在依赖与 `.env` 配置完成后运行 `uv run python -m research_agent.cli web`，打开 `http://127.0.0.1:8765`。首次安装与导入步骤见下文；已有数据无需重复运行 `ingest`。

## 环境要求

- Windows 11、Python 3.12（64 位）和 uv
- Docker Desktop，使用 WSL 2 后端
- 用于生成回答的 DeepSeek API 密钥
- 本地 BGE-M3 向量模型；首次运行会下载模型权重

## 安装依赖

在项目目录运行：

```powershell
uv sync
Copy-Item .env.example .env
```

编辑 `.env`，设置 `DEEPSEEK_API_KEY`。默认聊天模型为 `deepseek-v4-pro`，向量模型为本地 `BAAI/bge-m3`；`BGE_M3_DEVICE=auto` 会优先使用可用的 NVIDIA GPU，否则回退到 CPU。本项目 Milvus 服务地址默认为 `http://localhost:19531`，可通过 `MILVUS_URI` 修改。

## 启动 Milvus

先启动 Docker Desktop，再在项目目录运行：

```powershell
docker compose up -d
docker compose ps
```

项目使用 Milvus Standalone 3.0.2，默认监听 `localhost:19531`，Web 管理页面默认位于 `http://localhost:9092/webui/`。端口与本机已有的 Milvus 实例错开。停止服务但保留数据：

```powershell
docker compose down
```

Milvus 的持久化数据保存在项目的 `data/milvus/`，不应手动删除；古籍源文件放在 `data/documents/`，两者用途不同。

## 导入资料并建立索引

上海地方志 Markdown 资料已放在 `data/documents/上海地方志资料集/`，配套图像与原目录结构一并保留。新增资料可放入 `data/documents/` 下的任意子目录。支持 `.txt`、`.md` 和 `.pdf`，PDF 会保留页码作为来源信息。运行：

```powershell
uv run python -m research_agent.cli ingest
```

每次运行 `ingest` 会重建当前 Milvus 集合后再导入资料。只有确认目录中包含所有要保留的文件时才重新导入。

## 提问

```powershell
uv run python -m research_agent.cli ask "请根据县志资料介绍某县的沿革"
```

## 多轮对话、工具与规划

本节说明保留的普通问答模式，联网按轮授权；专题研究使用上文的任务级授权和独立状态记录。

启动一个新会话：

```powershell
uv run python -m research_agent.cli chat
```

程序会显示会话 ID。输入 `:q` 结束；下次用相同 ID 恢复：

```powershell
uv run python -m research_agent.cli chat --session 会话ID
```

普通问答完整历史保存在 `data/conversations/conversations.sqlite`，不再按轮数截断；可用 `CONVERSATION_DIR` 修改目录。每次仅向模型附带最近 `CHAT_CONTEXT_TURNS` 轮（默认 20，范围 1–100），网页可分页查看更早记录。旧 JSON 自动幂等导入，原文件保留；无效旧记录会提示，之前已被截断的历史无法恢复。数据库和备份包含私有对话，请勿提交到版本控制。详见 [会话存储与迁移](docs/conversation-storage.md)。

`chat` 会在模型生成回答时逐段显示内容，回答后会结合本轮问题、回答和检索证据生成 3 条追问建议。每轮最多调用 6 次工具，之后模型会根据已有证据整理回答；工具调用和追问建议会增加模型请求次数与 API 用量。按 `Ctrl+C` 可随时结束进程，已完成轮次仍保留；尚未完成回答和建议生成的当前轮不会写入会话记录。

### 解析文件并追加新资料

在 PowerShell 中创建待处理目录并放入 TXT、Markdown 或含可提取文字的 PDF：

```powershell
New-Item -ItemType Directory -Force .\data\incoming
Copy-Item -LiteralPath "C:\你的文件路径\资料.pdf" -Destination .\data\incoming\
uv run python -m research_agent.cli chat
```

然后在 `chat` 中明确要求“把 `资料.pdf` 加入 RAG”。也可以先询问待处理文件列表。代理只会读取这个目录中的文件；扫描版 PDF 暂不支持 OCR，100 MB 以上文件需要拆分。

文件解析后会切分并追加到独立的 `research_docs_additions` 集合，原有 `research_docs` 集合不会被重建或修改。SHA-256 清单用于跳过重复文件。后续问答会将所有本地集合视为一个逻辑资料库，统一按相关性排序、去重并取 `TOP_K` 条证据，以 `[S1]` 等标签引用，不按导入先后决定优先级。不同版本或日期的记载有差异时，分别呈现原值与来源，不合并数值。`ADDITIONS_COLLECTION_NAME` 可更改新增集合名称。原来的 `ingest` 只重建基础资料集合，不会清除此新增集合。

每轮问答先检索资料库。资料库没有检索到相关内容时，网页会询问是否允许本轮联网搜索；点击“允许本轮联网”后才会访问外部搜索服务，选择“暂不联网”则只说明资料库未找到证据。联网回答使用 `[W1]`，并与资料库的 `[S#]` 分为“资料库”和“网络”两部分列出。联网来源会列出标题、网站、发布日期、检索日期、适用范围和网址；如果搜索结果未注明发布日期或适用范围，会明确提示核对原文。

RAG 检索会根据相关性分数过滤弱匹配；网页来源区会显示通过门槛的资料分数和页码。默认起始门槛为 `0.55`，可在 `.env` 中设置 `RAG_RELEVANCE_THRESHOLD`（范围 `0`–`1`）；应结合 `eval/` 中的问题集和实际命中情况调整，目前尚未针对该问题集校准。回答里引用的 `[S#]` 或 `[W#]` 必须存在于本轮检索结果；程序会移除无效标签并提示无引用的回答。

联网搜索使用 DeepSeek Search / Fetch API，复用 `DEEPSEEK_API_KEY` 和 `DEEPSEEK_BASE_URL`。先请求 `/v1/tools/search`（`need_content=true`），对缺少正文的入选结果调用 `/v1/tools/fetch`，已有正文时跳过抓取。抓取失败仍可使用摘要，并明确标记未取得正文；搜索失败不会被当成无结果，也不会切回其他搜索服务。正文最多取每条前 6000 字符，网页内容仍视为不可信资料，不能当作指令执行。

通过 `WEB_SEARCH_ENABLED=false` 可关闭联网工具；`WEB_SEARCH_MAX_RESULTS` 控制返回条数（1–10，默认 5），每次搜索最多额外抓取这么多页。搜索优先中文结果，并按政府网站、中国大陆权威媒体、其他 `.cn` 网站排序；不保证每次都有大陆官方来源，也未自动完成发布机构、适用范围和原文真实性核验。新闻使用最近 30 天的日期条件。关键词加上语言和日期条件不能超过 128 字符，超限会拒绝执行。

Search 和 Fetch 按成功且有结果的请求分别计费，模型回答另计 token 费用，详见 [DeepSeek 官方说明](https://api-docs.deepseek.com/zh-cn/guides/search/)（核对日期：2026-10-20）。保留先检索本地 RAG、无证据时询问是否联网的流程；联网结果不会自动入库。

工具中间件会限制工具白名单、查询长度和单轮调用次数，并将工具异常转为受控提示；不会开放任意命令执行或任意文件写入。只有用户明确要求时，代理才会把 `data/incoming/` 中的文件追加到专用新增资料集合。单轮工具调用上限可通过 `MAX_TOOL_CALLS` 调整（程序限制在 1–12 次）。

`ask` 仍是单轮命令。需要上下文记忆时使用 `chat`；不同会话 ID 的历史彼此隔离。

## 网页演示

完成依赖安装、`.env` 配置和 Milvus 启动后，在项目目录运行：

```powershell
uv run python -m research_agent.cli web
```

浏览器会打开 `http://127.0.0.1:8765`。网页展示流式多轮问答、追问建议、资料库与网络来源分类、联网搜索许可与来源，以及文件暂存和显式加入新增资料库的流程。历史对话保存在本地 SQLite，会话侧栏按首条提问展示，支持加载更多会话；打开后先显示最近 20 轮，可继续加载更早对话，数据库保留完整历史。上传只会将 TXT、Markdown 或 PDF 放到 `data/incoming/`；点击“加入 RAG”后才开始解析和写入，旧资料集合不会被重建。关闭服务请在终端按 `Ctrl+C`；可用 `--port` 指定其他本地端口。

网页顶部会每 10 秒检查本地 Milvus 连接状态，并提示新增资料和原有资料集合是否已建立。若连接失败，请先检查 Docker Desktop、`docker compose ps` 和 `.env` 中的 `MILVUS_URI`。

Python 代码可从 `research_agent` 导入公开接口：

```python
from research_agent import (
    add_file_to_rag,
    answer_question,
    answer_versioned_question,
    get_settings,
    load_documents,
    retrieve,
    retrieve_versions,
)
```

## 项目结构

```text
.
├── data/documents/              # 古籍县志源文件：.txt、.md、.pdf
├── data/milvus/                 # Milvus 持久化数据，由 Docker 管理
├── src/research_agent/
│   ├── answer.py                # 基于证据生成回答和来源引用
│   ├── agent.py                 # 多轮问答、流式回答、工具调用和追问建议
│   ├── workflow.py              # 研究服务、业务节点与后台执行器
│   ├── research_graph.py        # LangGraph State、节点与条件边定义
│   ├── evaluation/             # 检索评测、固定题集验收与真实服务冒烟
│   ├── observability/          # 可观测性扩展位置（待实现）
│   ├── conversation.py          # SQLite 完整会话、近期上下文与旧 JSON 迁移
│   ├── cli.py                   # ingest / ask 命令行入口
│   ├── config.py                # 环境变量和 Milvus 连接设置
│   ├── ingest.py                # 文件读取、文本切分和 Milvus 写入
│   ├── rag_updates.py           # incoming 文件解析、去重和增量写入
│   ├── retrieval.py             # Milvus 相似度检索和来源标记
│   ├── web_search.py            # 中文网页/新闻搜索及来源元数据
│   ├── web_server.py            # 仅监听本机的流式网页演示服务
│   └── static/index.html        # 中文交互演示页面
├── eval/datasets/questions.json          # 20道开发集问题
├── eval/datasets/holdout_questions.json  # 固定的20道验收题与4道合成无答案题
├── eval/results/                # 真实候选、回答与性能原始记录
├── eval/experiments/            # 实验计划与记录模板
├── eval/reports/                # 历史总结、人工审核及检查记录
├── tests/                      # unit / integration 与专项测试计划
├── scripts/                    # 模拟演示、性能/成本占位入口
├── docs/                       # 架构、评测、优化、部署及历史验收
├── .github/workflows/ci.yml     # 离线测试与整理模块静态检查
├── docker-compose.yml           # Milvus Standalone 服务
└── pyproject.toml
```

目录迁移、旧命令兼容和未清理项目见 [目录整理记录](docs/directory-layout.md)。新增监控、专项测试和性能/成本脚本为待实现骨架，不代表这些能力已经完成。

## 运行维护与检查

专题研究使用 LangGraph StateGraph 编排，SQLite checkpoint 保存图状态与执行位置，JSON 保留为网页任务视图和历史任务兼容数据。普通问答仍由 `agent.py` 中的 Python 循环协调，`ConversationStore` 保存会话。

BGE-M3 在同一进程中按模型名和实际设备复用，首次请求加载模型，后续检索和导入复用该模型；进程退出后释放。设备仍由 `BGE_M3_DEVICE` 控制，`auto` 优先选择可用 CUDA。网页进程内的资料库修改按顺序执行，资料清单使用同目录临时文件原子替换，普通问答会话使用 SQLite 事务保存完整轮次。该锁不协调多个独立服务进程，建议本地演示只运行一个服务实例。

默认运行离线测试，不调用 DeepSeek、Milvus 或下载模型：

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m pytest --basetemp=.pytest-review-tmp
```

真实 BGE-M3 集成检查需要显式选择，首次运行可能下载模型：

```powershell
.\.venv\Scripts\python.exe -m pytest -m integration -s tests/integration/test_embedding_integration.py --basetemp=.pytest-integration-tmp
```

2026-10-18 的检查范围、改动及限制见 [项目检查记录](docs/project-review-2026-10-18.md)。

当前模型的检索、回答、引用、性能及HTTP检查见 [V4-Pro回归记录](docs/acceptance-2026-10-20.md)，旧模型数据保留在 [2026-10-19验收记录](docs/acceptance-2026-10-19.md)。指代性追问会补充最近一轮用户问题用于检索；历史来源标签不会直接传给下一轮模型。模型请求设置60秒超时与最多1次重试；这不是整轮代理的总时限。

## 说明

- `ingest.py` 加载支持的本地文件，保留相对路径和 PDF 页码，切分文本并写入 Milvus。
- `retrieval.py` 从 Milvus 检索相关文本块，并为回答添加 `[S1]`、`[S2]` 等引用标签。
- `answer.py` 只把问题和检索结果交给模型；证据不足时要求模型说明资料不足，不编造来源。
- `agent.py` 让模型按需检索，并仅在用户明确要求时增量导入文件；它限制工具白名单和单轮调用次数。`conversation.py` 用 SQLite 保存完整对话，模型上下文单独限制最近轮数。
- 更换向量模型后需重建 Milvus 集合，避免向量维度或距离空间不一致。
- 聊天请求通过 `DEEPSEEK_BASE_URL` 发送，可能产生服务用量费用。首次运行 `uv sync` 和下载 BGE-M3 需要联网。

### 资料来源分类与已有数据兼容

资料来源统一分为资料库 `[S#]` 和网络 `[W#]`。基础集合、增量集合和历史独立目录集合仍保持现有物理结构，检索层统一汇总，无需数据迁移、重新向量化或重建索引。原有目录和版本元数据保留，仍用于目录匹配及版本核对，不再作为来源分类。历史已保存的对话和报告保留原文；未完成历史任务已有的 `[N#]`、`[O#]` 引用按资料库处理，继续执行时新增本地证据使用 `[S#]`，避免破坏已有引用。更改不代表检索质量已提升，需另行用固定评估集验证。
