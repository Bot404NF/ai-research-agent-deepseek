"""固定质量门槛的开发/留出配对运行；不把部分审核当作整集达标。"""

import argparse
import hashlib
import json
import math
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from research_agent.answer import BASE_SYSTEM_PROMPT as SYSTEM_PROMPT
from research_agent.answer import render_context
from research_agent.config import PROJECT_ROOT, get_settings
from research_agent.evaluation.acceptance import digest
from research_agent.evaluation.baseline import answer_checks, retrieval_metrics, validate_dataset
from research_agent.grounded_retrieval import GROUNDING_GUIDANCE, retrieve_grounded
from research_agent.retrieval import retrieve
from research_agent.security import error_type, protect_external_call
from research_agent.storage import write_json_atomic

ARMS = ("baseline", "candidate")


def generate(question, evidence, settings, *, candidate):
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    if not evidence:
        return {
            "answer": "提供的资料没有足够信息来回答这个问题。",
            "api_attempted": False,
            "usage": None,
            "finish_reason": "local_abstention",
        }
    model = ChatOpenAI(
        model=settings.chat_model,
        api_key=settings.api_key,
        base_url=settings.base_url,
        temperature=0,
        max_tokens=700,
        timeout=60,
        max_retries=0,
        extra_body={"thinking": {"type": "disabled"}},
    )
    response = model.invoke(
        [
            SystemMessage(content=SYSTEM_PROMPT + ("\n" + GROUNDING_GUIDANCE if candidate else "")),
            HumanMessage(content=f"问题：{question}\n\n证据：\n{render_context(evidence)}"),
        ]
    )
    return {
        "answer": str(response.content),
        "usage": response.usage_metadata,
        "api_attempted": True,
        "finish_reason": response.response_metadata.get("finish_reason"),
    }


def run_case(
    case,
    settings,
    *,
    generate_answers=False,
    baseline_fn=retrieve,
    candidate_fn=retrieve_grounded,
    generate_fn=generate,
):
    row = {
        "id": case["id"],
        "question": case["question"],
        "stratum": case["stratum"],
        "should_abstain": case["should_abstain"],
        "arms": {},
    }
    order = ARMS if sum(case["id"].encode()) % 2 else ARMS[::-1]
    for name in order:
        arm = {"status": "error", "api_attempted": False}
        row["arms"][name] = arm
        started = time.perf_counter()
        try:
            evidence = (candidate_fn if name == "candidate" else baseline_fn)(
                case["question"], settings, top_k=4
            )
            arm.update(
                retrieval_seconds=time.perf_counter() - started,
                retrieved=[asdict(e) for e in evidence],
                status="retrieved",
            )
            arm["metrics"] = retrieval_metrics(case, arm["retrieved"])
            if generate_answers:
                arm["api_attempted"] = bool(evidence)
                started = time.perf_counter()
                arm.update(
                    generate_fn(case["question"], evidence, settings, candidate=name == "candidate")
                )
                arm.update(generation_seconds=time.perf_counter() - started, status="ok")
                arm["checks"] = answer_checks(arm["answer"], arm["retrieved"])
        except Exception as exc:  # noqa: BLE001 - 不记录外部异常正文
            arm.update(status="error", error_type=error_type(exc))
    return row


def results_digest(report):
    return hashlib.sha256(
        json.dumps(report["results"], ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def review_template(report):
    return {
        "dataset_sha256": report["dataset_sha256"],
        "results_sha256": results_digest(report),
        "reviewer": {"kind": "human"},
        "reviews": [
            {
                "id": r["id"],
                "arm": a,
                "answer_correct": None,
                "citation_supported": None,
                "abstention_correct": None,
                "reason": "",
            }
            for r in report["results"]
            for a in ARMS
        ],
    }


def wilson(passed, total):
    if not total:
        return None
    z = 1.95996398454
    p = passed / total
    center = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return [max(0, center - half), min(1, center + half)]


def summarize(report, reviews=None):
    reviewed = {}
    originals = {r["id"]: r for r in report["results"]}
    if reviews is not None:
        if reviews.get("dataset_sha256") != report["dataset_sha256"] or reviews.get(
            "results_sha256"
        ) != results_digest(report):
            raise ValueError("审核未绑定本次题集及原始结果。")
        if reviews.get("reviewer", {}).get("kind") not in {"ai", "human"}:
            raise ValueError("必须区分AI与真人审核。")
        for review in reviews["reviews"]:
            key = (review["id"], review["arm"])
            if key in reviewed or key[0] not in originals or key[1] not in ARMS:
                raise ValueError("审核题号或组名不适用或重复。")
            negative = originals[key[0]]["should_abstain"]
            arm = originals[key[0]]["arms"][key[1]]
            for field in ("answer_correct", "citation_supported", "abstention_correct"):
                value = review.get(field)
                if value is None:
                    continue
                if type(value) is not bool or arm["status"] != "ok":
                    raise ValueError("审核只能填布尔值，失败调用不能评为回答成功。")
                if (field == "answer_correct" and negative) or (
                    field == "abstention_correct" and not negative
                ):
                    raise ValueError("正负题审核字段不适用。")
            if (
                any(
                    review.get(f) is not None
                    for f in ("answer_correct", "citation_supported", "abstention_correct")
                )
                and not review.get("reason", "").strip()
            ):
                raise ValueError("审核必须说明证据理由。")
            reviewed[key] = review
    rows = report["results"]
    expected = report.get("validation", {}).get("cases", len(rows))
    complete_run = len(rows) == expected and len(originals) == len(rows)
    result = {
        "reviewer": reviews["reviewer"] if reviews else None,
        "expected_cases": expected,
        "coverage_complete": complete_run,
        "minimum_size_met": expected >= 100,
        "arms": {},
    }
    for name in ARMS:
        result["arms"][name] = {}
        for field, eligible, threshold in (
            ("answer_correct", [r for r in rows if not r["should_abstain"]], 0.85),
            ("citation_supported", [r for r in rows if not r["should_abstain"]], 0.95),
            ("abstention_correct", [r for r in rows if r["should_abstain"]], 0.95),
            ("multi_correct", [r for r in rows if r["stratum"] == "multi_evidence"], 0.80),
        ):
            source_field = "answer_correct" if field == "multi_correct" else field
            values = [reviewed.get((r["id"], name), {}).get(source_field) for r in eligible]
            # 未审核、缺引用、截断和请求失败均不能计通过。
            passes = sum(
                v is True
                and r["arms"][name].get("finish_reason") in {"stop", "local_abstention"}
                and (
                    field != "citation_supported"
                    or (
                        r["arms"][name].get("checks", {}).get("has_citation", False)
                        and not r["arms"][name].get("checks", {}).get("invalid_labels")
                    )
                )
                for r, v in zip(eligible, values, strict=True)
            )
            n = len(eligible)
            complete = n > 0 and all(type(v) is bool for v in values)
            result["arms"][name][field] = {
                "eligible": n,
                "reviewed": sum(type(v) is bool for v in values),
                "passed": passes,
                "rate": passes / n if n else None,
                "wilson_95": wilson(passes, n),
                "target": threshold,
                "met": complete and passes / n >= threshold,
            }
        result["arms"][name]["all_targets_met"] = (
            complete_run
            and expected >= 100
            and all(
                result["arms"][name][f]["met"]
                for f in (
                    "answer_correct",
                    "citation_supported",
                    "abstention_correct",
                    "multi_correct",
                )
            )
        )
    return result


@protect_external_call
def main():
    parser = argparse.ArgumentParser(description="固定门槛配对评测；默认只校验，不访问服务")
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "eval/datasets/unified.json")
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--retrieve", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--review-report", type=Path)
    parser.add_argument("--reviews", type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.output.with_suffix(".reviews.json").exists():
        parser.error("输出已存在，请使用新文件名。")
    if bool(args.review_report) != bool(args.reviews) or (
        args.review_report and (args.generate or args.retrieve)
    ):
        parser.error("审核参数必须成对提供，且不能混用真实运行。")
    if args.review_report:
        report = json.loads(args.review_report.read_text(encoding="utf-8"))
        reviews = json.loads(args.reviews.read_text(encoding="utf-8"))
        report[
            "semantic_ai_review" if reviews["reviewer"]["kind"] == "ai" else "semantic_manual"
        ] = summarize(report, reviews)
        report["review_code_sha256"] = digest(Path(__file__))
        report["reviews_sha256"] = digest(args.reviews)
        write_json_atomic(args.output, report)
        print("审核汇总完成；未访问服务。")
        return
    settings = get_settings()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "status": "validated_only",
        "dataset_sha256": digest(args.dataset),
        "validation": validate_dataset(dataset, settings.data_dir),
        "code_sha256": {
            name: digest(PROJECT_ROOT / f"src/research_agent/{name}.py")
            for name in ("retrieval", "grounded_retrieval", "answer", "evaluation/target")
        },
        "settings": {
            "model": settings.chat_model,
            "embedding": settings.embedding_model,
            "collection": settings.collection_name,
            "threshold": settings.rag_relevance_threshold,
            "top_k": 4,
            "candidate_top_k": 12,
            "max_tokens": 700,
            "max_retries": 0,
        },
        "prompts": {"base": SYSTEM_PROMPT, "candidate_addition": GROUNDING_GUIDANCE},
        "scope": "基础集合单次生成配对；不代表业务默认或完整研究图",
        "results": [],
    }
    write_json_atomic(args.output, report)
    if not (args.generate or args.retrieve):
        print("题集已校验；未访问服务。")
        return
    for case in dataset["cases"]:
        report["results"].append(run_case(case, settings, generate_answers=args.generate))
        report["status"] = "running"
        write_json_atomic(args.output, report)
        print(f"{case['id']} 已保存", flush=True)
    report["status"] = (
        "completed_with_errors"
        if any(a["status"] == "error" for r in report["results"] for a in r["arms"].values())
        else "completed"
    )
    write_json_atomic(args.output, report)
    write_json_atomic(args.output.with_suffix(".reviews.json"), review_template(report))
    print("配对运行完成；质量待审核。")
