# API Key 防泄露约定与验证

适用日期：2026-10-31。本轮保留 `.env` 与现有应用启动方式，不迁移系统凭据、不安装 Git hook、不改远端分支保护，也不改写历史数据或评测结果。此前密钥已由用户更换。

## 1. 默认测试先隔离再导入

`tests/conftest.py` 在收集项目测试前设置 `PYTHON_DOTENV_DISABLED=1`、固定测试占位密钥和无效的模型服务域名。这里使用当前安装的 python-dotenv 1.2.1 已支持的开关，不需要新增依赖。autouse fixture 在导入测试模块后才执行，不能单独承担这项隔离。

直接运行以下命令即可，无需人工清空真实密钥：

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp=.pytest-security-tmp --junitxml=.security-artifacts/pytest.xml --tb=short
.\.venv\Scripts\python.exe scripts/check_secrets.py --paths .security-artifacts
```

默认还会阻止测试进程的外部域名解析、TCP 连接和 UDP 发送，保留 loopback 以运行已有本机 HTTP 检查。允许外部网络的显式命令为：

```powershell
.\.venv\Scripts\python.exe -m pytest -m integration --allow-external-network -s tests/integration/test_embedding_integration.py
```

该参数只放行网络，仍然禁用 `.env`、覆盖 API Key；不能据此执行真实付费模型验收。需要真实凭据的评测继续使用独立 `eval/` 入口，按已有使用说明显式运行并另存结果。网络限制属于 Python 测试进程防误用，不是操作系统防火墙，也不约束任意外部程序或恶意插件。

## 2. 配置与异常输出

- Settings 的 `api_key`、`base_url` 和 `milvus_uri` 不参与 `repr()`；连接 URL 也可能包含口令和查询参数。
- **`repr=False` 不是加密或序列化保护。** `asdict(settings)`、`settings.__dict__` 和直接读取字段仍包含原值；不得用于日志和错误回显。配置诊断使用 `diagnostic_settings()` 的明确字段允许列表。
- 连接状态中的 URI 仅保留协议、主机和端口，不返回用户信息、路径、查询参数或片段。
- 普通调用与流式调用使用安全异常边界，只保留异常类型及合法 HTTP 状态码。新增 `ExternalServiceError` 继承 RuntimeError，调用参数与正常返回值保持原接口。
- 捕获原异常后，先退出异常处理块再抛出安全异常，避免原异常作为 `__context__` 被保留到回溯或持久化记录。安全异常的 pickle 仅包含安全元数据，不持有响应或原异常对象。
- 应用自身已知的参数校验使用 `PublicInputError`（继承 ValueError），可以保留明确中文提示；第三方 ValueError/RuntimeError 不因类型相同就直接向用户回显。不要将上游异常正文转换成 PublicInputError。
- CLI、HTTP/SSE、研究节点及评测入口不输出第三方响应正文、Authorization 头或原始异常链。JSON/SSE 原字段结构保留。
- HTTP 访问日志只记录受限的方法名与状态码，解析错误只记录事件；不输出完整请求行、URL、查询参数、文件内容或原始日志参数。不得新增 SDK 请求头、客户端对象或环境变量的调试打印，也不要在含真实凭据的运行中启用第三方详细调试日志。

## 3. 扫描与提交检查

扫描器仅使用 Python 标准库。下列命令不读取真实 `.env`：

```powershell
# 默认：Git 跟踪文件的工作区内容，不包括未跟踪文件
.\.venv\Scripts\python.exe scripts/check_secrets.py

# 暂存之后运行：读取 Git index 的实际内容，不以工作区文件代替
.\.venv\Scripts\python.exe scripts/check_secrets.py --staged

# 新文件、私有报告或日志需显式检查；目标必须在仓库内
.\.venv\Scripts\python.exe scripts/check_secrets.py --paths scripts/check_secrets.py src/research_agent/security.py tests/security .security-artifacts
```

检查常见 Key/Token 格式、带引号的凭据赋值、常见环境配置赋值以及 Bearer/Basic Authorization。发现时只输出文件、行号和规则编号，不输出匹配值、原始行、Git 诊断或异常正文。

退出码：0 表示未发现规则命中且无读取错误；1 表示有可疑项；2 表示参数/读取/扫描失败。读取失败不能按扫描通过处理。没有暂存文件时，`--staged` 会报告扫描 0 个文件，不代表全部修改已检查。

真实 `.env`、依赖目录、运行数据库和部分二进制文件不读取；`.env.example` 仍检查。若真实 `.env` 类文件已被跟踪或暂存，直接按文件名报 `FORBIDDEN_SECRET_FILE`，不打开内容。扫描不跟随符号链接，不接受越界目标。

占位值使用具体允许列表，不整目录豁免测试；包括固定测试值、`.env.example` 的示例值和现有 Compose 的公开示例值。允许公开示例值不表示认可弱默认口令。扫描规则不能保证发现所有凭据格式、编码、拼接或混淆后的内容，测试隔离与减少输出仍是主要保护。

CI 在测试前扫描跟踪文件，在生成 JUnit 后再次扫描 `.security-artifacts/`；测试失败时也执行产物扫描。该目录已忽略 Git。目前 CI 不上传报告；将来新增上传步骤时，必须依赖产物扫描成功，不能用 `always()` 绕过扫描失败。CI 检查失败会标红，是否强制阻止合并由仓库现有分支保护决定，本轮不修改远端设置。

## 4. 泄露回归与处理

安全测试使用运行时拼接的合成哨兵，避免将看似真实的完整密钥固定写进仓库。覆盖：

含凭据样例的参数化测试必须显式使用不含数据的 `ids`，不能让 pytest 把原始样例放入测试名称和 JUnit。产物扫描在本轮确实发现了这种元数据泄露路径，现已改为固定场景名，未通过豁免报告或放宽规则绕过。

- 向子进程注入模拟环境密钥与模拟项目 `.env`，在测试收集和执行时验证隔离；即使显式放行网络，也保持测试密钥。
- 故意制造失败并启用 `--showlocals`，检查子进程 pytest 输出及包含失败信息的 JUnit；不检查或读取真实项目 `.env`。
- 配置 repr、允许列表诊断、模型与流式异常、HTTP/SSE/JSON、访问日志、工具、Search/Fetch 状态错误、研究任务 JSON 与 checkpoint、评测失败记录。
- 凭据扫描的命中、占位值、退出码、禁止凭据文件、工作区与暂存区不一致，以及扫描器自身不回显可疑内容。

新增失败路径后，应先用合成哨兵补回归，再使用真实配置运行。发生真实泄露时：停止传播日志，撤销旧密钥并更换；确认影响范围后再处理历史产物或 Git 历史。不要为了“清理”直接删除用户数据或强制重写历史。本轮不访问账户账单或撤销用户凭据，也无法撤回先前聊天中已经出现的内容。

本轮实际测试与检查结果见[验证记录](secret-protection-validation-2026-10-31.md)。
