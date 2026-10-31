"""通过本机HTTP验证网页后端、流式代理和会话恢复；不导入资料。"""

import json
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from research_agent.config import PROJECT_ROOT, get_settings
from research_agent.security import protect_external_call
from research_agent.storage import write_json_atomic
from research_agent.web_server import create_handler


@protect_external_call
def main() -> None:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    directory = PROJECT_ROOT / "eval/runtime" / run_id
    settings = replace(get_settings(), conversation_dir=directory, web_search_enabled=False)
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "scope": "本机HTTP和完整流式代理；禁用联网；独立测试会话；非浏览器视觉验收",
        "chat_model": settings.chat_model,
        "checks": [],
        "chat": [],
    }
    destination = PROJECT_ROOT / "eval/results" / f"smoke_{run_id}.json"
    server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(settings))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        for path in [
            "/",
            "/api/knowledge-status",
            "/api/files",
            "/api/databases",
            "/api/conversations",
        ]:
            with urlopen(origin + path, timeout=15) as response:
                body = response.read()
                item = {"path": path, "status": response.status, "passed": response.status == 200}
                if path == "/api/knowledge-status":
                    payload = json.loads(body)
                    item["connected"] = payload["connected"]
                    item["passed"] = item["passed"] and payload["connected"]
                report["checks"].append(item)
        # 确认无效请求不会触发模型。
        bad = Request(
            origin + "/api/chat", data=b"{}", headers={"Content-Type": "application/json"}
        )
        try:
            urlopen(bad, timeout=10)
            report["checks"].append({"path": "无效聊天参数", "passed": False})
        except HTTPError as exc:
            report["checks"].append(
                {"path": "无效聊天参数", "status": exc.code, "passed": exc.code == 400}
            )
        for session, question in [
            ("positive", "《安亭志》的清代编纂者和现代标点者分别是谁？"),
            ("positive", "刚才回答的现代标点者叫什么名字？"),
            ("negative", "玄鹭量子档案馆ZX2099的馆长是谁？"),
        ]:
            started = time.perf_counter()
            events = []
            first = None
            item = {"session": session, "question": question}
            report["chat"].append(item)
            try:
                request = Request(
                    origin + "/api/chat",
                    data=json.dumps(
                        {"question": question, "session_id": session, "decline_web_search": True},
                        ensure_ascii=False,
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Origin": origin},
                )
                with urlopen(request, timeout=180) as response:
                    for line in response:
                        if not line.startswith(b"data: "):
                            continue
                        event = json.loads(line[6:])
                        events.append(event)
                        if event["kind"] == "token" and first is None:
                            first = time.perf_counter() - started
                with urlopen(origin + "/api/conversations/" + session, timeout=10) as response:
                    turns = json.loads(response.read())["turns"]
                passed = (
                    first is not None
                    and not any(e["kind"] == "error" for e in events)
                    and bool(turns and turns[-1]["user"] == question)
                )
                item.update(
                    status="ok" if passed else "failed",
                    events=events,
                    first_content_seconds=first,
                    elapsed_seconds=time.perf_counter() - started,
                    saved_turns=len(turns),
                    persisted=bool(turns and turns[-1]["user"] == question),
                )
            except Exception as exc:  # noqa: BLE001 - 冒烟检查记录失败类型并继续执行清理
                item.update(status="error", error_type=type(exc).__name__, events=events)
            write_json_atomic(destination, report)
            print(f"完整链路：{session}，{item['status']}。", flush=True)
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
        write_json_atomic(destination, report)
    print(f"HTTP验收记录：{destination}")


if __name__ == "__main__":
    main()
