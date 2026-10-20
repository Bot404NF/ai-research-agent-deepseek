# 中文资料研究助手

从本地网页提出资料问题，获得流式回答和可核对的原文来源，并继续追问或恢复历史会话。系统先检索本地资料；新增文件需明确导入，联网搜索需逐轮同意。

项目使用本地 BGE-M3、Milvus 和 DeepSeek：导入 `.txt`、`.md`、可提取文字的 `.pdf`，保留文件与页码元数据，将检索段落交给模型依据证据回答。

项目已接入 Milvus，并已加入用户提供的上海地方志资料集。资料目录及来源记录约定见 [资料目录说明](data/documents/README.md)；现有Markdown可能存在识别错字和重复，尚未逐页核对扫描原件。

2026-10-19 已完成小样本验收：独立20题 Hit@4 为16/20；60次预热后检索中位数41 ms、P95 49 ms。16次真实证据问答请求成功，4道合成无答案题均拒答。数据仅代表本次单用户样本，不等同于全库准确率或并发能力。

- [完整验收与性能口径](docs/acceptance-2026-10-19.md)
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

编辑 `.env`，设置 `DEEPSEEK_API_KEY`。默认聊天模型为 `deepseek-flash`，向量模型为本地 `BAAI/bge-m3`；`BGE_M3_DEVICE=auto` 会优先使用可用的 NVIDIA GPU，否则回退到 CPU。本项目 Milvus 服务地址默认为 `http://localhost:19531`，可通过 `MILVUS_URI` 修改。

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

启动一个新会话：

```powershell
uv run python -m research_agent.cli chat
```

程序会显示会话 ID。输入 `:q` 结束；下次用相同 ID 恢复：

```powershell
uv run python -m research_agent.cli chat --session 会话ID
```

每个会话最多保留最近 20 轮，默认保存在 `data/conversations/`；可用 `CONVERSATION_DIR` 修改目录。会话记录为本地 JSON 文件，可能包含用户输入和模型回答，请勿将其目录提交到版本控制。

`chat` 会在模型生成回答时逐段显示内容，回答后会结合本轮问题、回答和检索证据生成 3 条追问建议。每轮最多调用 6 次工具，之后模型会根据已有证据整理回答；工具调用和追问建议会增加模型请求次数与 API 用量。按 `Ctrl+C` 可随时结束进程，已完成轮次仍保留；尚未完成回答和建议生成的当前轮不会写入会话记录。

### 解析文件并追加新资料

在 PowerShell 中创建待处理目录并放入 TXT、Markdown 或含可提取文字的 PDF：

```powershell
New-Item -ItemType Directory -Force .\data\incoming
Copy-Item -LiteralPath "C:\你的文件路径\资料.pdf" -Destination .\data\incoming\
uv run python -m research_agent.cli chat
```

然后在 `chat` 中明确要求“把 `资料.pdf` 加入 RAG”。也可以先询问待处理文件列表。代理只会读取这个目录中的文件；扫描版 PDF 暂不支持 OCR，100 MB 以上文件需要拆分。

文件解析后会切分并追加到独立的 `research_docs_additions` 集合，原有 `research_docs` 集合不会被重建或修改。SHA-256 清单用于跳过重复文件。后续问答会分别检索两组资料，以 `[N1]` 引用新增资料、以 `[O1]` 引用原资料；回答先依据新增资料，并在“旧资料对照”部分列出旧资料。若两者不一致，会分别呈现，不合并数值。`ADDITIONS_COLLECTION_NAME` 可更改新增集合名称。原来的 `ingest` 只重建基础资料集合，不会清除此新增集合。

每轮问答先检索新增资料和原有资料。资料库没有检索到相关内容时，网页会询问是否允许本轮联网搜索；点击“联网搜索”后才会访问外部搜索服务，选择“暂不联网”则只说明资料库未找到证据。联网回答使用 `[W1]`，并与新增、旧资料的 `[N#]`、`[O#]` 分别列出。联网来源会列出标题、网站、发布日期、检索日期、适用范围和网址；如果搜索结果未注明发布日期或适用范围，会明确提示核对原文。

RAG 检索会根据相关性分数过滤弱匹配；网页来源区会显示通过门槛的资料分数和页码。默认起始门槛为 `0.55`，可在 `.env` 中设置 `RAG_RELEVANCE_THRESHOLD`（范围 `0`–`1`）；应结合 `eval/` 中的问题集和实际命中情况调整，目前尚未针对该问题集校准。回答里引用的 `[N#]`、`[O#]`、`[W#]` 或 `[S#]` 必须存在于本轮检索结果；程序会移除无效标签并提示无引用的回答。

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

浏览器会打开 `http://127.0.0.1:8765`。网页展示流式多轮问答、追问建议、资料库新旧版本对照、联网搜索许可与来源，以及文件暂存和显式加入新增资料库的流程。历史对话保存在 `data/conversations/` 的本地 JSON 文件中，网页侧栏可按首条提问查看和恢复；每个会话保留最近 20 轮。上传只会将 TXT、Markdown 或 PDF 放到 `data/incoming/`；点击“加入 RAG”后才开始解析和写入，旧资料集合不会被重建。关闭服务请在终端按 `Ctrl+C`；可用 `--port` 指定其他本地端口。

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
│   ├── conversation.py          # 本地会话记忆存取
│   ├── cli.py                   # ingest / ask 命令行入口
│   ├── config.py                # 环境变量和 Milvus 连接设置
│   ├── ingest.py                # 文件读取、文本切分和 Milvus 写入
│   ├── rag_updates.py           # incoming 文件解析、去重和增量写入
│   ├── retrieval.py             # Milvus 相似度检索和来源标记
│   ├── web_search.py            # 中文网页/新闻搜索及来源元数据
│   ├── web_server.py            # 仅监听本机的流式网页演示服务
│   └── static/index.html        # 中文交互演示页面
├── eval/questions.json          # 20道开发集问题
├── eval/holdout_questions.json  # 固定的20道验收题与4道合成无答案题
├── eval/results/                # 真实候选、回答与性能原始记录
├── docker-compose.yml           # Milvus Standalone 服务
└── pyproject.toml
```

## 运行维护与检查

当前项目使用 LangChain 组件，没有使用 LangGraph。多轮代理由 `agent.py` 中的普通 Python 循环协调，`ConversationStore` 用本地 JSON 保存会话；不存在 LangGraph 图节点、状态图或 checkpoint 流程。

BGE-M3 在同一进程中按模型名和实际设备复用，首次请求加载模型，后续检索和导入复用该模型；进程退出后释放。设备仍由 `BGE_M3_DEVICE` 控制，`auto` 优先选择可用 CUDA。网页进程内的资料库修改按顺序执行，清单和会话使用同目录临时文件原子替换。该锁不协调多个独立服务进程，建议本地演示只运行一个服务实例。

默认运行离线测试，不调用 DeepSeek、Milvus 或下载模型：

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m pytest --basetemp=.pytest-review-tmp
```

真实 BGE-M3 集成检查需要显式选择，首次运行可能下载模型：

```powershell
.\.venv\Scripts\python.exe -m pytest -m integration -s tests/test_embedding_integration.py --basetemp=.pytest-integration-tmp
```

2026-10-18 的检查范围、改动及限制见 [项目检查记录](docs/project-review-2026-10-18.md)。

2026-10-19 的检索、回答、引用、性能及HTTP检查见 [验收记录](docs/acceptance-2026-10-19.md)。指代性追问会补充最近一轮用户问题用于检索；历史来源标签不会直接传给下一轮模型。模型请求设置60秒超时与最多1次重试；这不是整轮代理的总时限。

## 说明

- `ingest.py` 加载支持的本地文件，保留相对路径和 PDF 页码，切分文本并写入 Milvus。
- `retrieval.py` 从 Milvus 检索相关文本块，并为回答添加 `[S1]`、`[S2]` 等引用标签。
- `answer.py` 只把问题和检索结果交给模型；证据不足时要求模型说明资料不足，不编造来源。
- `agent.py` 让模型按需检索，并仅在用户明确要求时增量导入文件；它限制工具白名单和单轮调用次数。`conversation.py` 保存最近 20 轮对话。
- 更换向量模型后需重建 Milvus 集合，避免向量维度或距离空间不一致。
- 聊天请求通过 `DEEPSEEK_BASE_URL` 发送，可能产生服务用量费用。首次运行 `uv sync` 和下载 BGE-M3 需要联网。
