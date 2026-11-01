"""真实研究演示案例：两个独立进程验证计划持久化和继续执行，共用原费用账本。"""

import argparse
import http.client
import json
import os
import subprocess
import sys
import threading
from dataclasses import replace
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from research_agent import workflow
from research_agent.config import PROJECT_ROOT, get_settings
from research_agent.evaluation.acceptance import digest
from research_agent.evaluation.performance import Budget, model_factory
from research_agent.observability.runtime import ObservedModel
from research_agent.security import protect_external_call
from research_agent.storage import write_json_atomic

QUESTIONS = [
    "《安亭志》的清代编纂者与现代标点者分别是谁？请区分不同角色。",
    "孙岱《安亭人物志》的初稿有几卷，陈树德编入《安亭志》后合为几卷？不要混淆人物志与全志。",
    "《安亭志》的现代整理本有哪些明确的标点、出版机构和出版年月记载？不同版本分别说明。",
]
PLAN = {
    "scope": "依据现有上海地方志资料核对安亭志的编纂与现代整理，不使用模型常识补足缺证。",
    "expected_output": "逐项结论、原文来源、版本差异及待核对事项。",
    "questions": [
        {"question": q, "search_query": q[:80], "needs_current": False, "news": False}
        for q in QUESTIONS
    ],
}


def settle(service, task_id):
    if service.worker:
        service.worker.join(timeout=240)
        if service.worker.is_alive():
            raise TimeoutError("验收任务仍在执行，不启动其他进程操作该目录。")
    return service.get(task_id)


def http_check(settings, task):
    """只读检查新服务能打开页面、读取同一任务并下载相同报告。"""
    from research_agent.web_server import create_handler

    server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(settings))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    result = {}
    try:
        for key, path in (
            ("page", "/"),
            ("task", f"/api/research/tasks/{task['id']}"),
            ("download", f"/api/research/tasks/{task['id']}/report"),
        ):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            try:
                connection.request("GET", path)
                response = connection.getresponse()
                body = response.read().decode("utf-8")
                result[key + "_ok"] = response.status == 200
                if key == "download":
                    result["download_matches"] = body == task.get("report")
            finally:
                connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    return result


@protect_external_call
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=("create", "resume"))
    parser.add_argument(
        "--candidate", action="store_true", help="只在隔离任务启用候选，不改实际默认"
    )
    args = parser.parse_args()
    runtime = PROJECT_ROOT / "eval/runtime" / args.output.stem
    if args.phase is None:
        if args.output.exists() or runtime.exists():
            parser.error("结果或隔离任务目录已存在，拒绝覆盖。")
        report = {
            "created_at": datetime.now(UTC).isoformat(),
            "status": "running",
            "goal": "核对安亭志的编纂、人物志卷数及现代整理版本，逐项引用证据。",
            "scope": "真实模型与既有索引；手工编辑固定测试计划。重启验证使用两个独立Python进程，不重启个人服务。",
            "code_sha256": {
                n: digest(PROJECT_ROOT / f"src/research_agent/{n}.py")
                for n in (
                    "workflow",
                    "research_graph",
                    "agent",
                    "retrieval",
                    "grounded_retrieval",
                    "evidence_policy",
                    "config",
                    "observability/runtime",
                )
            },
        }
        write_json_atomic(args.output, report)
        for phase in ("create", "resume"):
            subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--output",
                    str(args.output),
                    "--phase",
                    phase,
                ]
                + (["--candidate"] if args.candidate else []),
                check=True,
            )
            current = json.loads(args.output.read_text(encoding="utf-8"))
            if phase == "create" and current.get("create_status") != "awaiting_confirmation":
                current["status"] = "create_failed"
                write_json_atomic(args.output, current)
                raise RuntimeError("计划阶段未正常暂停，不进入恢复步骤。")
        return
    report = json.loads(args.output.read_text(encoding="utf-8"))
    settings = replace(get_settings(), conversation_dir=runtime / "conversations")
    if args.candidate:
        settings = replace(settings, grounded_answers=True)
    if settings.chat_model != "deepseek-flash":
        parser.error("费用配置仅适用于deepseek-flash。")
    budget = Budget(PROJECT_ROOT / "eval/results/week4_budget_20261031.json")
    calls = []
    service = workflow.ResearchWorkflow(settings, runtime / "research_tasks")
    web_calls = []

    def forbidden_web(*args, **kwargs):
        web_calls.append(True)
        raise AssertionError("未授权的演示不得搜索网络。")

    with (
        patch.object(workflow, "_model", lambda s: ObservedModel(model_factory(s, budget, calls))),
        patch.object(workflow, "search_web", forbidden_web),
    ):
        if args.phase == "create":
            task = service.create(report["goal"], ask_web_consent=True)
            task = settle(service, task["id"])
            report.update(
                create_pid=os.getpid(),
                task_id=task["id"],
                create_status=task["status"],
                generated_plan=task.get("plan"),
            )
            if task["status"] == "awaiting_confirmation" and task.get("plan"):
                service.update_plan(task["id"], PLAN)
                report["edited_plan"] = service.get(task["id"])["plan"]
        else:
            task = service.get(report["task_id"])
            report.update(
                resume_pid=os.getpid(),
                plan_survived_restart=task.get("plan") == report.get("edited_plan"),
            )
            service.action(task["id"], "confirm")
            task = settle(service, task["id"])
            report["consent_prompt_seen"] = task["status"] == "waiting_web_consent"
            if task["status"] == "waiting_web_consent":
                service.action(task["id"], "web-consent", {"allow_web": False})
                task = settle(service, task["id"])
            report["task"] = task
            report["network_calls"] = len(web_calls)
            report["effective_grounded_answers"] = settings.grounded_answers
            report["checks"] = {
                "separate_processes": report["create_pid"] != report["resume_pid"],
                "plan_persisted": report["plan_survived_restart"],
                "research_completed": task["status"] == "completed",
                "no_network_calls": len(web_calls) == 0,
                "report_saved": bool(task.get("report")),
                "model_outputs_complete": all(
                    c.get("finish_reason") == "stop" for c in report.get("create_calls", []) + calls
                ),
            }
            if task["status"] == "completed":
                before_http_calls = len(calls)
                report["checks"].update(http_check(settings, task))
                report["checks"]["http_get_no_model_calls"] = len(calls) == before_http_calls
            report["status"] = (
                "completed" if all(report["checks"].values()) else "completed_with_errors"
            )
            if task.get("report"):
                args.output.with_suffix(".md").write_text(task["report"], encoding="utf-8")
        report[f"{args.phase}_calls"] = calls
        write_json_atomic(args.output, report)
        print(args.phase, report.get("status"), flush=True)


if __name__ == "__main__":
    main()
