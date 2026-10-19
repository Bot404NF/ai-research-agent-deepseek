"""本地网页演示服务。"""

import json
import re
import threading
import webbrowser
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, urlparse

from pymilvus import MilvusClient

from research_agent.agent import stream_answer_turn
from research_agent.config import Settings
from research_agent.conversation import ConversationStore
from research_agent.rag_updates import MAX_FILE_SIZE, add_file_to_rag, create_database, database_details, delete_database, list_databases, list_incoming_files


STATIC_PAGE = Path(__file__).parent / "static" / "index.html"
MAX_UPLOAD_REQUEST_SIZE = MAX_FILE_SIZE + 1024 * 1024
SESSION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _knowledge_status(settings: Settings) -> dict:
    """检查 Milvus 可达性及两个资料集合是否存在。"""
    client = None
    try:
        client = MilvusClient(uri=settings.milvus_uri, timeout=3)
        collections = set(client.list_collections())
        databases = list_databases(settings)
        return {
            "connected": True,
            "uri": settings.milvus_uri,
            "collections": {
                "new": settings.additions_collection_name in collections,
                "old": settings.collection_name in collections,
                "databases": any(
                    item["collection"] in collections
                    for item in databases if item["id"] != "initial"
                ),
            },
        }
    except Exception as exc:
        return {
            "connected": False,
            "uri": settings.milvus_uri,
            "collections": {"new": False, "old": False, "databases": False},
            "error": type(exc).__name__,
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
        raise ValueError("文件名无效。")
    root = directory.resolve()
    destination = root.joinpath(*relative.parts)
    if not destination.resolve().is_relative_to(root):
        raise ValueError("文件夹路径无效。")
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
        raise ValueError("上传请求格式无效，请选择文件后重试。")
    parts = []
    for part in message.iter_parts():
        filename = part.get_filename()
        if not filename:
            continue
        payload = part.get_payload(decode=True) or b""
        parts.append((filename, payload))
    if not parts:
        raise ValueError("请求中没有找到文件。")
    return parts


def create_handler(settings: Settings):
    class ResearchAgentHandler(BaseHTTPRequestHandler):
        server_version = "ResearchAgentDemo/1.0"

        def log_message(self, format: str, *args) -> None:
            # 访问日志保留方法和状态码，不记录请求正文或会话内容。
            super().log_message(format, *args)

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
                raise ValueError("请求长度无效。") from exc
            if length <= 0 or length > MAX_UPLOAD_REQUEST_SIZE:
                raise ValueError("请求为空或超过大小限制。")
            try:
                data = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValueError("请求内容不是有效 JSON。") from exc
            if not isinstance(data, dict):
                raise ValueError("请求内容格式无效。")
            return data

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
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
            elif path == "/api/conversations":
                store = ConversationStore(settings.conversation_dir)
                self._send_json(200, {"conversations": store.list_sessions()})
            elif path.startswith("/api/conversations/"):
                session_id = path.removeprefix("/api/conversations/")
                if not SESSION_PATTERN.fullmatch(session_id):
                    self._send_json(400, {"error": "会话 ID 格式无效。"})
                    return
                try:
                    turns = ConversationStore(settings.conversation_dir).load(session_id)
                except (OSError, ValueError, json.JSONDecodeError):
                    self._send_json(400, {"error": "无法读取该历史对话。"})
                    return
                self._send_json(200, {"id": session_id, "turns": turns})
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            origin = self.headers.get("Origin")
            if origin and origin.rstrip("/") != f"http://{self.headers.get('Host', '')}".rstrip("/"):
                self._send_json(403, {"error": "只接受来自本地演示网页的请求。"})
                return
            path = urlparse(self.path).path
            if path == "/api/chat":
                self._chat()
            elif path == "/api/upload":
                self._upload()
            elif path == "/api/index":
                self._index()
            elif path == "/api/databases":
                self._create_database()
            else:
                self.send_error(404)

        def do_DELETE(self) -> None:
            origin = self.headers.get("Origin")
            if origin and origin.rstrip("/") != f"http://{self.headers.get('Host', '')}".rstrip("/"):
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
                except OSError:
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
                    self._send_json(400, {"error": str(exc)})
                except Exception as exc:
                    self._send_json(500, {"error": f"删除目录失败（{type(exc).__name__}），目录记录已保留。"})
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
                    raise ValueError("问题不能为空且不能超过 10000 个字符。")
                if not isinstance(session_id, str) or not SESSION_PATTERN.fullmatch(session_id):
                    raise ValueError("会话 ID 格式无效，请刷新网页后重试。")
                if not isinstance(allow_web_search, bool) or not isinstance(decline_web_search, bool):
                    raise ValueError("联网搜索许可格式无效。")
                if allow_web_search and decline_web_search:
                    raise ValueError("联网搜索许可状态冲突。")
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
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
                        "new_sources": event.new_sources,
                        "old_sources": event.old_sources,
                        "web_sources": event.web_sources,
                    }
                    self.wfile.write(b"data: " + _json_bytes(payload) + b"\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:
                try:
                    error = _json_bytes({"kind": "error", "message": f"本轮处理失败（{type(exc).__name__}）。请检查服务配置后重试。"})
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
                    raise ValueError("上传请求为空或超过 100 MB 限制。")
                content_type = self.headers.get("Content-Type", "")
                if not content_type.lower().startswith("multipart/form-data;"):
                    raise ValueError("请选择文件后再上传。")
                parts = _uploaded_parts(self.rfile.read(length), content_type)
                database_id = parse_qs(urlparse(self.path).query).get("database_id", [""])[0]
                known_ids = {item["id"] for item in list_databases(settings)}
                if database_id and database_id not in known_ids:
                    raise ValueError("所选资料库不存在。")
                incoming = settings.data_dir.parent / "incoming" / database_id if database_id and database_id != "legacy-additions" else settings.data_dir.parent / "incoming"
                total = 0
                validated = []
                for filename, payload in parts:
                    suffix = Path(filename.replace("\\", "/")).suffix.lower()
                    if suffix not in {".txt", ".md", ".pdf"}:
                        raise ValueError(f"不支持 {filename}；请选择 TXT、Markdown 或 PDF。")
                    if len(payload) > MAX_FILE_SIZE:
                        raise ValueError(f"{filename} 超过 100 MB 限制。")
                    total += len(payload)
                    if total > MAX_FILE_SIZE:
                        raise ValueError("单次上传总大小不能超过 100 MB。")
                    if not payload:
                        raise ValueError(f"{filename} 是空文件。")
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
                self._send_json(400, {"error": str(exc)})

        def _index(self) -> None:
            try:
                payload = self._read_json()
                relative_path = payload.get("path")
                if not isinstance(relative_path, str) or not relative_path.strip():
                    raise ValueError("请选择一个待处理文件。")
                database_id = payload.get("database_id")
                if database_id == "legacy-additions":
                    database_id = None
                data_version = payload.get("data_version", "supplement")
                if data_version not in {"original", "supplement"}:
                    raise ValueError("资料标签无效。")
                replace_hash = payload.get("replace_hash")
                if replace_hash is not None and (not isinstance(replace_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", replace_hash)):
                    raise ValueError("更新文件标识无效，请刷新管理页后重试。")
                result = add_file_to_rag(relative_path, settings, database_id, replace_hash, data_version)
                message = (
                    f"{result['source']} 已存在于当前新增资料索引（{result['chunks']} 个文本块）。"
                    if not result["added"]
                    else f"已安全更新 {result['source']}（{result['chunks']} 个文本块）；新索引写入成功后才移除了该文件的旧文本块。"
                    if result.get("updated")
                    else f"已将 {result['source']} 索引到当前新增资料库（{result['chunks']} 个文本块）；原有资料索引未改动。"
                )
                self._send_json(200, {"added": result["added"], "message": message})
            except (ValueError, FileNotFoundError, RuntimeError) as exc:
                self._send_json(400, {"error": str(exc)})
            except Exception as exc:
                self._send_json(500, {"error": f"索引操作未完成（{type(exc).__name__}）。旧索引保留；请查看索引状态后重试。"})

        def _create_database(self) -> None:
            try:
                payload = self._read_json()
                name = payload.get("name")
                if not isinstance(name, str):
                    raise ValueError("请填写资料库名称。")
                database = create_database(name, settings)
                self._send_json(201, {"database": database})
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})

        def log_error(self, format: str, *args) -> None:
            super().log_error(format, *args)

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
