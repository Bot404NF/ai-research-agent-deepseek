"""第四周AI审核：与作答共用10元账本，解析失败同样保留用量。"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from review_target import JUDGE_PROMPT, review_one

from research_agent.config import PROJECT_ROOT, get_settings
from research_agent.evaluation.acceptance import digest
from research_agent.evaluation.performance import Budget, model_factory
from research_agent.evaluation.target import review_template, summarize
from research_agent.security import error_type, protect_external_call
from research_agent.storage import write_json_atomic


@protect_external_call
def main():
    parser = argparse.ArgumentParser(description="第四周逐题审核，不重跑回答，共用费用上限")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--ledger", type=Path, default=PROJECT_ROOT / "eval/results/week4_budget_20261031.json"
    )
    args = parser.parse_args()
    if args.output.exists():
        parser.error("审核文件已存在，拒绝覆盖。")
    dataset_path = PROJECT_ROOT / "eval/datasets/unified.json"
    report = json.loads(args.report.read_text(encoding="utf-8"))
    if report["dataset_sha256"] != digest(dataset_path) or report["status"] != "completed":
        parser.error("题集摘要不匹配或业务对照未完成。")
    cases = {c["id"]: c for c in json.loads(dataset_path.read_text(encoding="utf-8"))["cases"]}
    reviews = review_template(report)
    reviews.update(
        created_at=datetime.now(UTC).isoformat(),
        judge_prompt=JUDGE_PROMPT,
        reviewer={
            "kind": "ai",
            "name": "deepseek-flash",
            "method": "真实业务回答逐题审核；非真人审核",
        },
    )
    rows = {r["id"]: r for r in report["results"]}
    budget = Budget(args.ledger)
    settings = get_settings()
    if settings.chat_model != "deepseek-flash":
        parser.error("审核必须使用deepseek-flash。")
    write_json_atomic(args.output, reviews)
    for item in reviews["reviews"]:
        arm = rows[item["id"]]["arms"][item["arm"]]
        calls = []
        if arm["status"] != "ok":
            item.update(judge_status="not_applicable", reason="原业务调用失败，保留未评分。")
        else:
            try:
                model = model_factory(settings, budget, calls)
                model.model = model.model.bind(response_format={"type": "json_object"})
                item.update(review_one(cases[item["id"]], arm, model), judge_status="ok")
            except Exception as exc:  # noqa: BLE001 - 只保存异常类别
                item.update(
                    judge_status="error",
                    judge_error_type=error_type(exc),
                    reason="审核失败，保留用量及未评分状态。",
                )
        item["judge_calls"] = calls
        write_json_atomic(args.output, reviews)
        print(f"{item['id']} {item['arm']} {item['judge_status']}", flush=True)
        if item.get("judge_error_type") == "BudgetStop" or any(
            c["state"] != "settled" for c in calls
        ):
            print("预算或未知用量触发停止，保留未完成审核。")
            return
    reviews["summary"] = summarize(report, reviews)
    write_json_atomic(args.output, reviews)


if __name__ == "__main__":
    main()
