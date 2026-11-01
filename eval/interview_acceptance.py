"""项目收口验收：默认校验，先只读检索，再在原账本内运行业务对照。"""

import argparse
import json
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from research_agent.config import PROJECT_ROOT, get_settings
from research_agent.evaluation.acceptance import digest
from research_agent.evaluation.baseline import retrieval_metrics, validate_dataset
from research_agent.evaluation.performance import Budget, business_turn, performance_summary
from research_agent.evaluation.target import review_template
from research_agent.retrieval import retrieve_library
from research_agent.security import error_type, protect_external_call
from research_agent.storage import write_json_atomic


def source_hashes():
    names = (
        "agent",
        "answer",
        "config",
        "retrieval",
        "grounded_retrieval",
        "evidence_policy",
        "workflow",
        "observability/runtime",
        "evaluation/performance",
    )
    return {name: digest(PROJECT_ROOT / f"src/research_agent/{name}.py") for name in names}


def forecast(budget):
    """用已保存的实际用量保守预估，不把缓存命中或完整运行费用当成保证。"""
    previous = json.loads(
        (PROJECT_ROOT / "eval/results/week4_final_live_20261031.json").read_text(encoding="utf-8")
    )
    reviews = json.loads(
        (PROJECT_ROOT / "eval/reports/week4_final_ai_reviews_20261031.json").read_text(
            encoding="utf-8"
        )
    )
    answer_peak = (
        sum(
            c["reserved_or_peak_cny"]
            for row in previous["results"]
            for c in row["arms"]["baseline"]["calls"]
        )
        * 2
    )
    review_peak = sum(
        c["reserved_or_peak_cny"] for row in reviews["reviews"] for c in row.get("judge_calls", [])
    )
    committed = sum(c["reserved_or_peak_cny"] for c in budget.data["calls"])
    expected = (answer_peak + review_peak) * 1.1 + 0.5
    return {
        "remaining_peak_cny": 10 - committed,
        "projected_peak_with_reserve_cny": expected,
        "sufficient": committed + expected <= 10,
        "basis": "上一轮模型追问两组及逐题审核实际高峰估算，加10%裕量和0.5元请求预留；每次实际请求仍受账本硬限制。",
    }


@protect_external_call
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retrieve", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.with_suffix(".reviews.json").exists():
        parser.error("输出已存在，拒绝覆盖。")
    settings = replace(get_settings(), web_search_enabled=False, followup_mode="model")
    if settings.chat_model != "deepseek-flash":
        parser.error("本轮费用配置仅适用于deepseek-flash，请显式设置匹配模型。")
    dataset_path = PROJECT_ROOT / "eval/datasets/unified.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    before = source_hashes()
    report = {
        "runner_sha256": digest(Path(__file__)),
        "created_at": datetime.now(UTC).isoformat(),
        "status": "validated_only",
        "dataset_sha256": digest(dataset_path),
        "validation": validate_dataset(dataset, settings.data_dir),
        "settings": {
            "model": settings.chat_model,
            "top_k": 4,
            "max_tokens": 4096,
            "max_retries": 0,
            "baseline_grounded": False,
            "candidate_grounded": True,
            "followup_mode": "model",
        },
        "code_sha256": before,
        "scope": "当前默认业务与著录角色候选的统一100题对照；两组都使用模型追问，不做性能因果声明。",
        "results": [],
    }
    budget = Budget(PROJECT_ROOT / "eval/results/week4_budget_20261031.json")
    report["budget_forecast"] = forecast(budget)
    write_json_atomic(args.output, report)
    if not (args.run or args.retrieve):
        print("题集与资料已校验，未调用服务。", report["budget_forecast"])
        return
    if args.run and not report["budget_forecast"]["sufficient"]:
        report["status"] = "insufficient_budget"
        write_json_atomic(args.output, report)
        print("预计完整验收费用超过剩余预算，未发起付费请求。")
        return
    if args.run:
        from research_agent.conversation import ConversationStore

        store = ConversationStore(
            PROJECT_ROOT / "eval/runtime" / args.output.stem / "conversations"
        )
    for index, case in enumerate(dataset["cases"]):
        row = {
            "id": case["id"],
            "question": case["question"],
            "stratum": case["stratum"],
            "should_abstain": case["should_abstain"],
            "arms": {},
        }
        for name in ("baseline", "candidate") if index % 2 else ("candidate", "baseline"):
            arm_settings = replace(settings, grounded_answers=name == "candidate")
            if args.run:
                arm = business_turn(case, arm_settings, budget, store, f"{case['id']}-{name}")
            else:
                started = time.perf_counter()
                try:
                    found = retrieve_library(case["question"], arm_settings, top_k=4)
                    arm = {"status": "retrieved", "retrieved": [asdict(e) for e in found]}
                    arm["metrics"] = retrieval_metrics(case, arm["retrieved"])
                except Exception as exc:  # noqa: BLE001 - 不回显外部错误正文
                    arm = {"status": "error", "error_type": error_type(exc)}
                arm["retrieval_seconds"] = time.perf_counter() - started
            row["arms"][name] = arm
        report["results"].append(row)
        report["status"] = "running"
        write_json_atomic(args.output, report)
        print(f"{case['id']} 已保存", flush=True)
        if args.run and (
            any(
                c["state"] not in {"settled", "interrupted_reserved", "external_reserved"}
                for c in budget.data["calls"]
            )
            or any(a.get("error_type") == "BudgetStop" for a in row["arms"].values())
        ):
            report["status"] = "budget_or_unknown_usage_stopped"
            write_json_atomic(args.output, report)
            return
    report["status"] = (
        "completed"
        if all(a["status"] != "error" for r in report["results"] for a in r["arms"].values())
        else "completed_with_errors"
    )
    report["code_sha256_after"] = source_hashes()
    report["code_unchanged"] = before == report["code_sha256_after"]
    if args.run:
        report["performance"] = performance_summary(report["results"])
        write_json_atomic(args.output.with_suffix(".reviews.json"), review_template(report))
    write_json_atomic(args.output, report)
    print(report["status"], "源码未变", report["code_unchanged"])


if __name__ == "__main__":
    main()
