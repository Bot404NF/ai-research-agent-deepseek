"""本地网页演示服务。"""

import json
import logging
import re
import sqlite3
import threading
import webbrowser
from email import policy
from email.parser import BytesParser
from functools import wraps
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, urlparse

from pymilvus import MilvusClient

from research_agent.agent import stream_answer_turn
from research_agent.config import Settings
from research_agent.conversation import ConversationStore
from research_agent.rag_updates import (
    MAX_FILE_SIZE,
    add_file_to_rag,
    create_database,
    database_details,
    delete_database,
    list_databases,
    list_incoming_files,
)
from research_agent.security import (
    PublicInputError,
    error_type,
    public_endpoint,
    validation_message,
)
from research_agent.workflow import ResearchWorkflow, WorkflowConflict

STATIC_PAGE = Path(__file__).parent / "static" / "index.html"
MAX_UPLOAD_REQUEST_SIZE = MAX_FILE_SIZE + 1024 * 1024
SESSION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _http_boundary(function):
    @wraps(function)
    def wrapped(self):
        try:
            return function(self)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:  # noqa: BLE001 - 防止标准HTTP服务将未知异常和局部变量打印到stderr
            message = f"请求未完成（{error_type(exc)}），请稍后重试。"
        try:
            self._send_json(500, {"error": message})
        except OSError:
            pass

    return wrapped


def _knowledge_status(settings: Settings) -> dict:
    """检查 Milvus 可达性及两个资料集合是否存在。"""
    client = None
    try:
        client = MilvusClient(uri=settings.milvus_uri, timeout=3)
        collections = set(client.list_collections())
        databases = list_databases(settings)
        return {
            "connected": True,
            "uri": public_endpoint(settings.milvus_uri),
            "collections": {
                "new": settings.additions_collection_name in collections,
                "old": settings.collection_name in collections,
                "databases": any(
                    item["collection"] in collections
                    for item in databases
                    if item["id"] != "initial"
                ),
            },
        }
    except Exception as exc:  # noqa: BLE001 - 隔离第三方连接错误，避免泄露响应正文
        return {
            "connected": False,
            "uri": public_endpoint(settings.milvus_uri),
            "collections": {"new": False, "old": False, "databases": False},
            "error": error_type(exc),
        }
    finally:
        if client is not None:
            client.close()


def _unique_destination(directory: Path, filename: str) -> Path:
    relative = PurePosixPath(filename.replace("\\", "/"))
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {".", ".."} or ":" in part for part in relative.parts)
    ):
        raise PublicInputError("文件名无效。")
    root = directory.resolve()
    destination = root.joinpath(*relative.parts)
    if not destination.resolve().is_relative_to(root):
        raise PublicInputError("文件夹路径无效。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        return destination
    stem, suffix = destination.stem, destination.suffix
    index = 2
    while destination.with_name(f"{stem}_{index}{suffix}").exists():
        index += 1
    return destination.with_name(f"{stem}_{index}{suffix}")


def _uploaded_parts(body: bytes, content_type: str) -> list[tuple[str, bytes]]:
    message = BytesParser(policy=policy.default).parsebytes(
        b"MIME-Version: 1.0\r\nContent-Type: "
        + content_type.encode("ascii", errors="strict")
        + b"\r\n\r\n"
        + body
    )
    if not message.is_multipart():
        raise PublicInputError("上传请求格式无效，请选择文件后重试。")
    parts = []
    for part in message.iter_parts():
        filename = part.get_filename()
        if not filename:
            continue
        payload = part.get_payload(decode=True) or b""
        parts.append((filename, payload))
    if not parts:
        raise PublicInputError("请求中没有找到文件。")
    return parts


def create_handler(settings: Settings, workflow: ResearchWorkflow | None = None):
    research = workflow or ResearchWorkflow(settings)

    class ResearchAgentHandler(BaseHTTPRequestHandler):
        server_version = "ResearchAgentDemo/1.0"

        def log_message(self, format: str, *args) -> None:
            # 基类日志可能包含完整请求行或请求头解析错误，不转发 format/args。
            logging.getLogger(__name__).info("http_event")

        def log_request(self, code="-", size="-") -> None:
            method = self.command if self.command in {"GET", "POST", "DELETE", "HEAD"} else "OTHER"
            status = int(code) if str(code).isdigit() else 0
            logging.getLogger(__name__).info("http_request method=%s status=%s", method, status)

        def _send_json(self, status: int, payload: dict) -> None:
            encoded = _json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def _read_json(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise PublicInputError("请求长度无效。") from exc
            if length <= 0 or length > MAX_UPLOAD_REQUEST_SIZE:
                raise PublicInputError("请求为空或超过大小限制。")
            try:
                data = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise PublicInputError("请求内容不是有效 JSON。") from exc
            if not isinstance(data, dict):
                raise PublicInputError("请求内容格式无效。")
            return data

        @_http_boundary
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/research/tasks" or path.startswith("/api/research/tasks/"):
                self._research_get(path)
            elif path in {"/workflow.js", "/workflow.css"}:
                resource = STATIC_PAGE.parent / path.removeprefix("/")
                body = resource.read_bytes()
                self.send_response(200)
                mime = "text/javascript" if path.endswith(".js") else "text/css"
                self.send_header("Content-Type", mime + "; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/":
                try:
                    body = STATIC_PAGE.read_bytes()
                except OSError:
                    self.send_error(500, "网页资源读取失败")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/files":
                database_id = parse_qs(urlparse(self.path).query).get("database_id", [""])[0]
                self._send_json(200, {"files": list_incoming_files(settings, database_id or None)})
            elif path == "/api/databases":
                self._send_json(200, {"databases": list_databases(settings)})
            elif path == "/api/databases/details":
                self._send_json(200, {"databases": database_details(settings)})
            elif path == "/api/knowledge-status":
                self._send_json(200, _knowledge_status(settings))
            elif path == "/api/conversations" or path.startswith("/api/conversations/"):
                self._conversations_get(path)
            else:
                self.send_error(404)

        def _conversations_get(self, path: str) -> None:
            try:
                query = parse_qs(urlparse(self.path).query)
                store = ConversationStore(settings.conversation_dir)
                if path == "/api/conversations":
                    data = store.session_page(
                        limit=int(query.get("limit", ["50"])[0]),
                        offset=int(query.get("offset", ["0"])[0]),
                    )
                else:
                    before = query.get("before", [None])[0]
                    data = store.page(
                        path.removeprefix("/api/conversations/"),
                        limit=int(query.get("limit", ["20"])[0]),
                        before=int(before) if before is not None else None,
                    )
                self._send_json(200, data)
            except ValueError:
                self._send_json(400, {"error": "会话 ID、分页参数或旧会话格式无效，请检查后重试。"})
            except (OSError, sqlite3.Error):
                self._send_json(500, {"error": "无法读取会话数据库，请检查目录权限和存储状态。"})

        @_http_boundary
        def do_POST(self) -> None:
            origin = self.headers.get("Origin")
            if origin and origin.rstrip("/") != f"http://{self.headers.get('Host', '')}".rstrip(
                "/"
            ):
                self._send_json(403, {"error": "只接受来自本地演示网页的请求。"})
                return
            path = urlparse(self.path).path
            if path == "/api/research/tasks" or path.startswith("/api/research/tasks/"):
                self._research_post(path)
            elif path == "/api/chat":
                self._chat()
            elif path == "/api/upload":
                self._upload()
            elif path == "/api/index":
                self._index()
            elif path == "/api/databases":
                self._create_database()
            else:
                self.send_error(404)

        def _research_get(self, path: str) -> None:
            try:
                if path == "/api/research/tasks":
                    self._send_json(200, {"tasks": research.list_tasks()})
                    return
                parts = path.removeprefix("/api/research/tasks/").split("/")
                if len(parts) > 2 or (len(parts) == 2 and parts[1] != "report"):
                    self._send_json(404, {"error": "研究接口不存在。"})
                    return
                task = research.get(parts[0])
                if len(parts) == 1:
                    self._send_json(200, task)
                    return
                if task["status"] != "completed":
                    raise WorkflowConflict("报告尚未完成。")
                body = task["report"].encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.send_header(
                    "Content-Disposition", f'attachment; filename="research-{task["id"]}.md"'
                )
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except FileNotFoundError:
                self._send_json(404, {"error": "研究任务不存在。"})
            except WorkflowConflict as exc:
                self._send_json(409, {"error": validation_message(exc)})
            except ValueError as exc:
                self._send_json(400, {"error": validation_message(exc)})
            except OSError:
                self._send_json(500, {"error": "研究任务读取失败，请检查本地存储。"})

        def _research_post(self, path: str) -> None:
            try:
                payload = self._read_json()
                if path == "/api/research/tasks":
                    task = research.create(
                        payload.get("goal"),
                        payload.get("allow_web", False),
                        ask_web_consent=payload.get("ask_web_consent", False),
                    )
                    self._send_json(202, task)
                    return
                parts = path.removeprefix("/api/research/tasks/").split("/")
                if len(parts) != 2:
                    self._send_json(404, {"error": "研究接口不存在。"})
                    return
                task_id, action = parts
                if action == "plan":
                    task = research.update_plan(
                        task_id, payload.get("plan"), payload.get("allow_web")
                    )
                else:
                    task = research.action(task_id, action, payload)
                self._send_json(200, task)
            except FileNotFoundError:
                self._send_json(404, {"error": "研究任务不存在。"})
            except WorkflowConflict as exc:
                self._send_json(409, {"error": validation_message(exc)})
            except ValueError as exc:
                self._send_json(400, {"error": validation_message(exc)})
            except OSError:
                self._send_json(500, {"error": "研究任务保存失败，请检查本地存储。"})

        @_http_boundary
        def do_DELETE(self) -> None:
            origin = self.headers.get("Origin")
            if origin and origin.rstrip("/") != f"http://{self.headers.get('Host', '')}".rstrip(
                "/"
            ):
                self._send_json(403, {"error": "只接受来自本地演示网页的请求。"})
                return
            path = urlparse(self.path).path
            if path.startswith("/api/conversations/"):
                session_id = path.removeprefix("/api/conversations/")
                if not SESSION_PATTERN.fullmatch(session_id):
                    self._send_json(400, {"error": "会话 ID 格式无效。"})
                    return
                try:
                    deleted = ConversationStore(settings.conversation_dir).delete(session_id)
                    if not deleted:
                        self._send_json(404, {"error": "会话不存在或已删除。"})
                        return
                    self._send_json(200, {"deleted": True})
                except ValueError:
                    self._send_json(400, {"error": "旧会话记录无法读取，原文件已保留。"})
                except (OSError, sqlite3.Error):
                    self._send_json(500, {"error": "删除会话失败，请稍后重试。"})
                return
            if path.startswith("/api/databases/"):
                database_id = path.removeprefix("/api/databases/")
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", database_id):
                    self._send_json(400, {"error": "目录标识格式无效。"})
                    return
                try:
                    deleted = delete_database(database_id, settings)
                    if not deleted:
                        self._send_json(404, {"error": "目录不存在或已删除。"})
                        return
                    self._send_json(200, {"deleted": True})
                except ValueError as exc:
                    self._send_json(400, {"error": validation_message(exc)})
                except Exception as exc:  # noqa: BLE001 - HTTP 边界隔离第三方错误
                    self._send_json(
                        500, {"error": f"删除目录失败（{error_type(exc)}），目录记录已保留。"}
                    )
                return
            self.send_error(404)

        def _chat(self) -> None:
            try:
                payload = self._read_json()
                question = payload.get("question")
                session_id = payload.get("session_id")
                allow_web_search = payload.get("allow_web_search", False)
                decline_web_search = payload.get("decline_web_search", False)
                if not isinstance(question, str) or not question.strip() or len(question) > 10_000:
                    raise PublicInputError("问题不能为空且不能超过 10000 个字符。")
                if not isinstance(session_id, str) or not SESSION_PATTERN.fullmatch(session_id):
                    raise PublicInputError("会话 ID 格式无效，请刷新网页后重试。")
                if not isinstance(allow_web_search, bool) or not isinstance(
                    decline_web_search, bool
                ):
                    raise PublicInputError("联网搜索许可格式无效。")
                if allow_web_search and decline_web_search:
                    raise PublicInputError("联网搜索许可状态冲突。")
            except ValueError as exc:
                self._send_json(400, {"error": validation_message(exc)})
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for event in stream_answer_turn(
                    question.strip(),
                    settings,
                    session_id,
                    allow_web_search=allow_web_search,
                    decline_web_search=decline_web_search,
                ):
                    payload = {
                        "kind": event.kind,
                        "text": event.text,
                        "message": event.text if event.kind == "error" else "",
                        "suggestions": event.suggestions,
                        "library_sources": event.library_sources,
                        "web_sources": event.web_sources,
                    }
                    self.wfile.write(b"data: " + _json_bytes(payload) + b"\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:  # noqa: BLE001 - 流式 HTTP 边界必须发送受控错误
                try:
                    error = _json_bytes(
                        {
                            "kind": "error",
                            "message": f"本轮处理失败（{error_type(exc)}）。请检查服务配置后重试。",
                        }
                    )
                    self.wfile.write(b"data: " + error + b"\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            finally:
                self.close_connection = True

        def _upload(self) -> None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_UPLOAD_REQUEST_SIZE:
                    raise PublicInputError("上传请求为空或超过 100 MB 限制。")
                content_type = self.headers.get("Content-Type", "")
                if not content_type.lower().startswith("multipart/form-data;"):
                    raise PublicInputError("请选择文件后再上传。")
                parts = _uploaded_parts(self.rfile.read(length), content_type)
                database_id = parse_qs(urlparse(self.path).query).get("database_id", [""])[0]
                known_ids = {item["id"] for item in list_databases(settings)}
                if database_id and database_id not in known_ids:
                    raise PublicInputError("所选资料库不存在。")
                incoming = (
                    settings.data_dir.parent / "incoming" / database_id
                    if database_id and database_id != "legacy-additions"
                    else settings.data_dir.parent / "incoming"
                )
                total = 0
                validated = []
                for filename, payload in parts:
                    suffix = Path(filename.replace("\\", "/")).suffix.lower()
                    if suffix not in {".txt", ".md", ".pdf"}:
                        raise PublicInputError(f"不支持 {filename}；请选择 TXT、Markdown 或 PDF。")
                    if len(payload) > MAX_FILE_SIZE:
                        raise PublicInputError(f"{filename} 超过 100 MB 限制。")
                    total += len(payload)
                    if total > MAX_FILE_SIZE:
                        raise PublicInputError("单次上传总大小不能超过 100 MB。")
                    if not payload:
                        raise PublicInputError(f"{filename} 是空文件。")
                    validated.append((filename, payload))
                incoming.mkdir(parents=True, exist_ok=True)
                saved = []
                for filename, payload in validated:
                    destination = _unique_destination(incoming, filename)
                    with destination.open("xb") as file:
                        file.write(payload)
                    saved.append(destination.relative_to(incoming).as_posix())
                self._send_json(200, {"files": saved})
            except (ValueError, OSError, UnicodeError) as exc:
                self._send_json(400, {"error": validation_message(exc)})

        def _index(self) -> None:
            try:
                payload = self._read_json()
                relative_path = payload.get("path")
                if not isinstance(relative_path, str) or not relative_path.strip():
                    raise PublicInputError("请选择一个待处理文件。")
                database_id = payload.get("database_id")
                if database_id == "legacy-additions":
                    database_id = None
                data_version = payload.get("data_version", "supplement")
                if data_version not in {"original", "supplement"}:
                    raise PublicInputError("资料标签无效。")
                replace_hash = payload.get("replace_hash")
                if replace_hash is not None and (
                    not isinstance(replace_hash, str)
                    or not re.fullmatch(r"[a-f0-9]{64}", replace_hash)
                ):
                    raise PublicInputError("更新文件标识无效，请刷新管理页后重试。")
                result = add_file_to_rag(
                    relative_path, settings, database_id, replace_hash, data_version
                )
                message = (
                    f"{result['source']} 已存在于当前新增资料索引（{result['chunks']} 个文本块）。"
                    if not result["added"]
                    else f"已安全更新 {result['source']}（{result['chunks']} 个文本块）；新索引写入成功后才移除了该文件的旧文本块。"
                    if result.get("updated")
                    else f"已将 {result['source']} 索引到当前新增资料库（{result['chunks']} 个文本块）；原有资料索引未改动。"
                )
                self._send_json(200, {"added": result["added"], "message": message})
            except (ValueError, FileNotFoundError, RuntimeError) as exc:
                self._send_json(400, {"error": validation_message(exc)})
            except Exception as exc:  # noqa: BLE001 - 索引失败不能透传上游敏感信息
                self._send_json(
                    500,
                    {
                        "error": f"索引操作未完成（{error_type(exc)}）。旧索引保留；请查看索引状态后重试。"
                    },
                )

        def _create_database(self) -> None:
            try:
                payload = self._read_json()
                name = payload.get("name")
                if not isinstance(name, str):
                    raise PublicInputError("请填写资料库名称。")
                database = create_database(name, settings)
                self._send_json(201, {"database": database})
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": validation_message(exc)})

        def log_error(self, format: str, *args) -> None:
            logging.getLogger(__name__).warning("http_error")

    return ResearchAgentHandler


def serve(settings: Settings, port: int = 8765) -> None:
    """只在本机启动演示网页，避免意外向局域网开放。"""
    server = ThreadingHTTPServer(("127.0.0.1", port), create_handler(settings))
    url = f"http://127.0.0.1:{port}"
    print(f"网页演示已启动：{url}")
    print("按 Ctrl+C 停止服务。")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n网页演示已停止。")
    finally:
        server.server_close()
