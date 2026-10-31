"""复用已保存证据重跑检查与报告；保留旧结果，不重新检索或宣称完整流程复验。"""

import argparse
import hashlib
import json
import uuid
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from research_agent import workflow
from research_agent.config import PROJECT_ROOT, get_settings
from research_agent.evaluation.acceptance import digest
from research_agent.evaluation.performance import Budget, model_factory
from research_agent.security import error_type, protect_external_call
from research_agent.storage import write_json_atomic


def evidence_digest(evidence):
    return hashlib.sha256(
        json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


@protect_external_call
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run", action="store_true", help="显式调用两次模型，计入原10元账本")
    args = parser.parse_args()
    runtime = PROJECT_ROOT / "eval/runtime" / args.output.stem
    if args.output.exists() or args.output.with_suffix(".md").exists() or runtime.exists():
        parser.error("输出或隔离目录已存在，拒绝覆盖。")
    source_hash = digest(args.source)
    original = json.loads(args.source.read_text(encoding="utf-8"))
    task = deepcopy(original["task"])
    if not task.get("evidence") or task.get("status") != "completed":
        parser.error("需要含原始证据的已完成演示记录。")
    budget = Budget(PROJECT_ROOT / "eval/results/week4_budget_20261031.json")
    remaining = budget.data["limit_cny"] - sum(
        c["reserved_or_peak_cny"] for c in budget.data["calls"]
    )
    if remaining < 1:
        parser.error("余额不足以预留两次请求，不追加预算。")
    if not args.run:
        print("输入有效；仅重跑检查与报告，预留上限1元；尚未调用模型或写出结果。")
        return
    settings = replace(get_settings(), conversation_dir=runtime / "conversations")
    if settings.chat_model != "deepseek-flash":
        parser.error("原费用账本仅适用于deepseek-flash。")
    service = workflow.ResearchWorkflow(settings, runtime / "research_tasks")
    task.update(id=uuid.uuid4().hex, review="", report="", cancel_requested=False)
    service._save(task)
    evidence_hash = evidence_digest(task["evidence"])
    calls = []
    result = {
        "status": "running",
        "mode": "saved_evidence_review_and_report_only",
        "source": args.source.as_posix(),
        "source_sha256": source_hash,
        "workflow_sha256": digest(PROJECT_ROOT / "src/research_agent/workflow.py"),
        "original_task_id": original["task"]["id"],
        "evidence_sha256": evidence_hash,
        "calls": calls,
    }
    write_json_atomic(args.output, result)
    try:
        with patch.object(workflow, "_model", lambda s: model_factory(s, budget, calls)):
            for name, index in (
                ("review", len(task["plan"]["questions"])),
                ("report", len(task["plan"]["questions"]) + 1),
            ):
                task = service._graph_step(name, {"task": task, "index": index})["task"]
        result["checks"] = {
            "original_unchanged": digest(args.source) == source_hash,
            "evidence_unchanged": evidence_digest(task["evidence"]) == evidence_hash,
            "two_complete_calls": len(calls) == 2
            and all(c.get("finish_reason") == "stop" for c in calls),
        }
        task["status"] = "completed"
        service._save(task)
        args.output.with_suffix(".md").write_text(task["report"], encoding="utf-8")
        result["status"] = (
            "completed" if all(result["checks"].values()) else "completed_with_errors"
        )
    except Exception as exc:
        result.update(status="failed", error_type=getattr(exc, "error_type", error_type(exc)))
        raise
    finally:
        result["task"] = task
        write_json_atomic(args.output, result)
    print(result["status"])


if __name__ == "__main__":
    main()
