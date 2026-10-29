"""通过真实 HTTP、模型和本地检索执行单案例验收；不替换任何取证结果。"""

import argparse
import hashlib
import json
import re
import threading
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from research_agent import workflow
from research_agent.config import PROJECT_ROOT, get_settings
from research_agent.storage import write_json_atomic
from research_agent.web_server import create_handler

GOAL = (
    "研究《安亭志》的编纂、版本与史料价值，仅依据本地上海地方志资料，不联网。"
    "拆为恰好3个子问题：一、清代编纂者及成书刊刻沿革；"
    "二、现代整理本的标点者、出版社和出版时间，分别记录不同版本，不能合并；"
    "三、内容体例与史料价值，给出证据支持的具体例子及使用局限。"
    "不讨论当代安亭发展，不以外部常识补足缺口。"
)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--stage", choices=["plan", "execute"], required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    settings = replace(get_settings(), web_search_enabled=False)
    if not settings.api_key:
        raise SystemExit("缺少模型 API 配置。")
    manifest_path = directory / "manifest.json"
    if args.stage == "plan":
        if manifest_path.exists():
            raise SystemExit("目录已有验收记录，请使用新目录，避免覆盖。")
        manifest = {
            "started_at": datetime.now(UTC).isoformat(),
            "goal": GOAL,
            "allow_web": False,
            "settings": {
                key: getattr(settings, key)
                for key in (
                    "chat_model",
                    "embedding_model",
                    "collection_name",
                    "additions_collection_name",
                    "top_k",
                    "rag_relevance_threshold",
                )
            },
            "code_sha256": {
                name: sha256(PROJECT_ROOT / name)
                for name in (
                    "src/research_agent/workflow.py",
                    "src/research_agent/retrieval.py",
                    "src/research_agent/agent.py",
                    "src/research_agent/web_server.py",
                    "eval/run_research_acceptance.py",
                )
            },
            "criteria": [
                "真实模型生成3题计划，经保存并确认后执行；步骤完成且无错误。",
                "每题执行真实本地检索，保存原文、来源、分数及源文件摘要。",
                "联网关闭，搜索函数调用数为0，证据无W标签。",
                "三个研究维度分别判断充分、部分或不足；不以相关性分数代替审核。",
                "核心事实与所引证据一致，版本差异分别呈现，缺口明确说明。",
                "引用标签均存在；单独审核引用语义，不把格式合法视为事实通过。",
                "真实HTTP下载内容与持久化报告逐字一致，重开任务无额外取证。",
            ],
            "boundary": "已查看相关原文和历史题，属单案例验收；不测独立准确率、浏览器视觉、取消或运行中恢复。",
        }
        write_json_atomic(manifest_path, manifest)
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for name, digest in manifest["code_sha256"].items():
            if sha256(PROJECT_ROOT / name) != digest:
                raise SystemExit(f"冻结后代码变化：{name}")

    trace_path = directory / "trace.json"
    trace = json.loads(trace_path.read_text(encoding="utf-8")) if trace_path.exists() else []

    def record(event):
        event["at"] = datetime.now(UTC).isoformat()
        trace.append(event)
        write_json_atomic(trace_path, trace)

    real_model, real_retrieve = workflow._model, workflow.retrieve_versions

    class RecordedModel:
        def invoke(self, messages):
            start = time.perf_counter()
            try:
                response = real_model(settings).invoke(messages)
            except Exception as exc:
                record(
                    {
                        "kind": "model_error",
                        "type": type(exc).__name__,
                        "seconds": time.perf_counter() - start,
                    }
                )
                raise
            record(
                {
                    "kind": "model",
                    "seconds": time.perf_counter() - start,
                    "messages": [{"type": m.type, "content": m.content} for m in messages],
                    "content": response.content,
                    "response_metadata": response.response_metadata,
                    "usage_metadata": response.usage_metadata,
                }
            )
            return response

    def retrieve(question, current_settings):
        start = time.perf_counter()
        try:
            new, old = real_retrieve(question, current_settings)
        except Exception as exc:
            record(
                {
                    "kind": "retrieval_error",
                    "type": type(exc).__name__,
                    "seconds": time.perf_counter() - start,
                }
            )
            raise
        record(
            {
                "kind": "retrieval",
                "question": question,
                "seconds": time.perf_counter() - start,
                "new": [asdict(e) for e in new],
                "old": [asdict(e) for e in old],
            }
        )
        return new, old

    def blocked_search(*_args, **_kwargs):
        record({"kind": "forbidden_search"})
        raise RuntimeError("本次验收未授权联网搜索。")

    # 仅包裹真实调用记录输入、输出和用量；搜索入口增加拒绝守卫。
    workflow._model = lambda _settings: RecordedModel()
    workflow.retrieve_versions = retrieve
    workflow.search_web = blocked_search
    engine = workflow.ResearchWorkflow(settings, directory / "tasks")
    handler = create_handler(settings, engine)
    handler.log_message = lambda *_args: None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, path, payload=None, expected=200):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=15)
        body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"})
        response = connection.getresponse()
        result = response.read()
        record(
            {
                "kind": "http",
                "method": method,
                "path": path,
                "status": response.status,
                "bytes": len(result),
                "content_type": response.getheader("Content-Type"),
                "disposition": response.getheader("Content-Disposition"),
            }
        )
        connection.close()
        if response.status != expected:
            raise RuntimeError(f"HTTP状态异常：{response.status}，预期{expected}")
        return result

    def settle(task_id):
        last = None
        while engine.worker and engine.worker.is_alive():
            task = engine.get(task_id)
            progress = (
                task["status"],
                task["phase"],
                sum(s["status"] == "succeeded" for s in task["steps"]),
                len(task["evidence"]),
            )
            if progress != last:
                record({"kind": "progress", "state": progress})
                print(json.dumps(progress, ensure_ascii=False), flush=True)
                last = progress
            engine.worker.join(2)
        return json.loads(request("GET", f"/api/research/tasks/{task_id}"))

    try:
        start = time.perf_counter()
        if args.stage == "plan":
            task = json.loads(
                request(
                    "POST", "/api/research/tasks", {"goal": GOAL, "allow_web": False}, expected=202
                )
            )
            manifest["task_id"] = task["id"]
            write_json_atomic(manifest_path, manifest)
            task = settle(task["id"])
            write_json_atomic(directory / "generated_plan.json", task)
            request("GET", f"/api/research/tasks/{task['id']}/report", expected=409)
        else:
            task_id = manifest["task_id"]
            task = engine.get(task_id)
            if task["status"] != "awaiting_confirmation":
                raise RuntimeError("计划必须处于待确认状态；请保留失败记录，不自动重试。")
            request(
                "POST",
                f"/api/research/tasks/{task_id}/plan",
                {"plan": task["plan"], "allow_web": False},
            )
            write_json_atomic(directory / "confirmed_plan.json", engine.get(task_id))
            request("POST", f"/api/research/tasks/{task_id}/confirm", {"allow_web": False})
            task = settle(task_id)
            write_json_atomic(directory / "final_task.json", task)
            if task["status"] == "completed":
                report = request("GET", f"/api/research/tasks/{task_id}/report")
                (directory / "report.md").write_bytes(report)
                request("POST", f"/api/research/tasks/{task_id}/confirm", {}, expected=409)
                request("GET", "/api/research/tasks")
                reopened = workflow.ResearchWorkflow(settings, directory / "tasks").get(task_id)
                labels = {e["label"] for e in task["evidence"]}
                cited = set(re.findall(r"\[([NOW]\d+)\]", task["report"]))
                sources = {}
                for evidence in task["evidence"]:
                    path = settings.data_dir / evidence["source"]
                    sources[evidence["source"]] = sha256(path) if path.is_file() else None
                write_json_atomic(
                    directory / "checks.json",
                    {
                        "status": task["status"],
                        "allow_web": task["allow_web"],
                        "steps_succeeded": all(s["status"] == "succeeded" for s in task["steps"]),
                        "search_calls": sum(e["kind"] == "forbidden_search" for e in trace),
                        "web_evidence": sum(e["label"].startswith("W") for e in task["evidence"]),
                        "invalid_citations": sorted(cited - labels),
                        "download_matches": report.decode() == task["report"],
                        "reopen_matches": reopened == task,
                        "source_sha256": sources,
                        "report_sha256": sha256(directory / "report.md"),
                    },
                )
        record(
            {
                "kind": "stage_finished",
                "stage": args.stage,
                "status": task["status"],
                "seconds": time.perf_counter() - start,
            }
        )
        print(
            json.dumps(
                {"task_id": task["id"], "status": task["status"], "directory": str(directory)},
                ensure_ascii=False,
            ),
            flush=True,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


if __name__ == "__main__":
    main()
