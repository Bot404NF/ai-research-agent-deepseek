"""只读业务链路验证：统一题集检索及可选的CLI/HTTP/研究真实冒烟。"""

import argparse
import contextlib
import http.client
import io
import json
import sys
import threading
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

from research_agent.config import PROJECT_ROOT, get_settings
from research_agent.evaluation.acceptance import digest
from research_agent.evaluation.baseline import retrieval_metrics, validate_dataset
from research_agent.retrieval import retrieve_library
from research_agent.security import error_type, protect_external_call
from research_agent.storage import write_json_atomic


def identity(evidence):
    return [(e["source"].replace("\\", "/"), e["page"], e["text"]) for e in evidence]


def smoke_checks(result):
    chats = result.get("chat", [])
    research = result.get("research", {})
    return {
        "cli_completed": result.get("cli", {}).get("status") == "completed",
        "two_http_chat_turns": len(chats) == 2
        and all(
            r.get("http_status") == 200
            and r.get("answer")
            and not any(e.get("kind") == "error" for e in r.get("events", []))
            for r in chats
        ),
        "history_persisted": result.get("history", {}).get("http_status") == 200
        and result.get("history", {}).get("response", {}).get("total_turns") == 2,
        "research_completed": research.get("status") == "completed",
        "research_no_web": research.get("allow_web") is False
        and all(e.get("label", "").startswith("S") for e in research.get("evidence", [])),
        "report_downloaded": result.get("download", {}).get("http_status") == 200
        and result.get("download", {}).get("same_report") is True,
    }


def smoke(settings, runtime, save):
    from research_agent import cli
    from research_agent.web_server import create_handler

    result = {}
    previous = sys.argv
    captured = io.StringIO()
    try:
        sys.argv = ["research-agent", "ask", "《上海地方志概述》出版于何年何月，由哪家印刷厂印刷？"]
        with contextlib.redirect_stdout(captured):
            cli._main()
        result["cli"] = {"status": "completed", "output": captured.getvalue()}
    except Exception as exc:  # noqa: BLE001
        result["cli"] = {"status": "error", "error_type": error_type(exc)}
    finally:
        sys.argv = previous
    save(result)
    isolated = replace(
        settings, web_search_enabled=False, conversation_dir=runtime / "conversations"
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(isolated))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, path, payload=None):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=120)
        try:
            conn.request(
                method,
                path,
                json.dumps(payload, ensure_ascii=False).encode("utf-8")
                if payload is not None
                else None,
                {"Content-Type": "application/json"},
            )
            response = conn.getresponse()
            return response.status, response.read().decode("utf-8")
        finally:
            conn.close()

    def task_wait(task_id, status, seconds=240):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            code, body = request("GET", f"/api/research/tasks/{task_id}")
            task = json.loads(body)
            ready = task.get("status") == status and (
                status != "awaiting_confirmation" or task.get("plan")
            )
            if code != 200 or ready or task.get("status") in {"failed", "cancelled"}:
                return task
            time.sleep(0.2)
        raise TimeoutError("业务冒烟等待超时。")

    try:
        result["chat"] = []
        for question in (
            "《安亭志》的清代编纂者和现代标点者分别是谁？",
            "刚才回答的现代标点者叫什么名字？",
        ):
            code, body = request(
                "POST",
                "/api/chat",
                {"question": question, "session_id": "business-smoke", "decline_web_search": True},
            )
            events = [
                json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")
            ]
            text = ""
            for event in events:
                if event.get("kind") == "token":
                    text += event.get("text", "")
                elif event.get("kind") == "replace":
                    text = event.get("text", "")
            result["chat"].append(
                {"question": question, "http_status": code, "answer": text, "events": events}
            )
            save(result)
        code, body = request(
            "POST",
            "/api/research/tasks",
            {
                "goal": "核对安亭、南翔与上海地方志概述的编纂及历史信息，逐项引用证据。",
                "allow_web": False,
            },
        )
        task = json.loads(body)
        result["research_create_status"] = code
        if code != 202:
            result["research"] = {"status": "error", "response": task}
            return result
        task_id = task["id"]
        task = task_wait(task_id, "awaiting_confirmation")
        if task.get("status") == "failed":
            result["research"] = task
            return result
        questions = [
            "《上海地方志概述》出版于何年何月，由哪家印刷厂印刷？",
            "《安亭志》的清代编纂者和现代标点者分别是谁？",
            "请分别回答：①《安亭志》前言记载，安亭人民公社何时建立？②《南翔镇志》前言介绍的编纂体例中，哪四类事项概不记载？",
        ]
        plan = {
            "scope": "上海地方志基础及补充资料，只读本地证据，不联网。",
            "expected_output": "逐项给出事实、来源及证据不足之处。",
            "questions": [
                {"question": q, "search_query": q[:80], "needs_current": False, "news": False}
                for q in questions
            ],
        }
        # 这是隔离的验收任务，确认固定测试计划，不操作用户已有任务。
        time.sleep(0.3)
        code, _ = request(
            "POST", f"/api/research/tasks/{task_id}/plan", {"plan": plan, "allow_web": False}
        )
        if code != 200:
            raise RuntimeError("测试计划更新失败。")
        code, _ = request("POST", f"/api/research/tasks/{task_id}/confirm", {})
        if code != 200:
            raise RuntimeError("测试计划确认失败。")
        result["research"] = task_wait(task_id, "completed")
        save(result)
        if result["research"].get("status") == "completed":
            code, body = request("GET", f"/api/research/tasks/{task_id}/report")
            result["download"] = {
                "http_status": code,
                "same_report": body == result["research"]["report"],
            }
        code, body = request("GET", "/api/conversations/business-smoke")
        result["history"] = {"http_status": code, "response": json.loads(body)}
    except Exception as exc:  # noqa: BLE001
        result["error_type"] = error_type(exc)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        save(result)
    return result


@protect_external_call
def main():
    parser = argparse.ArgumentParser(description="默认只校验；真实检索只读；冒烟会产生API用量")
    parser.add_argument("--retrieve", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runtime = PROJECT_ROOT / "eval/runtime" / args.output.stem
    if args.output.exists() or runtime.exists():
        parser.error("结果或隔离目录已存在，请使用新输出名。")
    settings = get_settings()
    dataset_path = PROJECT_ROOT / "eval/datasets/unified.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    previous = json.loads(
        (PROJECT_ROOT / "eval/results/unified_live_20261031.json").read_text(encoding="utf-8")
    )
    if digest(dataset_path) != previous["dataset_sha256"]:
        parser.error("统一题集已改变，不能复用旧候选进行比较。")
    expected = {r["id"]: r["arms"]["candidate"] for r in previous["results"]}
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "status": "validated_only",
        "dataset_sha256": digest(dataset_path),
        "validation": validate_dataset(dataset, settings.data_dir),
        "model": settings.chat_model,
        "scope": "当前业务retrieve_library只读检索；真实冒烟另列，不复用旧答案分数",
        "code_sha256": {
            name: digest(PROJECT_ROOT / f"src/research_agent/{name}.py")
            for name in (
                "retrieval",
                "grounded_retrieval",
                "evidence_policy",
                "answer",
                "agent",
                "workflow",
                "web_server",
            )
        },
        "results": [],
    }

    def save():
        write_json_atomic(args.output, report)

    save()
    if args.retrieve:
        for case in dataset["cases"]:
            row = {"id": case["id"], "status": "error", "should_abstain": case["should_abstain"]}
            started = time.perf_counter()
            try:
                evidence = [asdict(e) for e in retrieve_library(case["question"], settings)]
                row.update(
                    status="ok",
                    retrieved=evidence,
                    metrics=retrieval_metrics(case, evidence),
                    same_as_evaluated_candidate=identity(evidence)
                    == identity(expected[case["id"]]["retrieved"]),
                )
            except Exception as exc:  # noqa: BLE001
                row["error_type"] = error_type(exc)
            row["seconds"] = time.perf_counter() - started
            report["results"].append(row)
            save()
            print(f"{case['id']} 业务检索已保存", flush=True)
    if args.smoke:

        def save_smoke(value):
            report["smoke"] = value
            save()

        smoke(settings, runtime, save_smoke)
        report["smoke_checks"] = smoke_checks(report["smoke"])
    report["code_sha256_at_end"] = {
        name: digest(PROJECT_ROOT / f"src/research_agent/{name}.py")
        for name in report["code_sha256"]
    }
    report["code_unchanged"] = report["code_sha256"] == report["code_sha256_at_end"]
    report["status"] = "completed" if args.retrieve or args.smoke else "validated_only"
    if any(r["status"] == "error" for r in report["results"]) or report.get("smoke", {}).get(
        "error_type"
    ):
        report["status"] = "completed_with_errors"
    if not report["code_unchanged"] or (args.smoke and not all(report["smoke_checks"].values())):
        report["status"] = "completed_with_errors"
    save()
    print("业务链路验证已保存；内容正确性需逐项核对。")


if __name__ == "__main__":
    main()
