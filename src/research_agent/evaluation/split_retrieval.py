"""第二周分题检索对照：只读基础集合，不改变业务默认策略。"""

import argparse
import hashlib
import json
import re
import subprocess
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from research_agent.config import PROJECT_ROOT, get_settings
from research_agent.evaluation.acceptance import digest, distribution
from research_agent.evaluation.baseline import (
    answer_checks,
    average,
    generate,
    retrieval_metrics,
    validate_dataset,
)
from research_agent.security import error_type, protect_external_call
from research_agent.storage import write_json_atomic


def split_queries(question: str) -> list[str]:
    """只接受从①开始的连续编号；无标记或格式不完整时保留整题。"""
    markers = list(re.finditer(r"[①②③④]", question))
    if len(markers) < 2 or [m.group() for m in markers] != list("①②③④"[: len(markers)]):
        return [question]
    queries = [
        question[m.end() : markers[i + 1].start() if i + 1 < len(markers) else None].strip()
        for i, m in enumerate(markers)
    ]
    return queries if all(queries) else [question]


def merge_round_robin(groups: list[list], top_k: int = 4) -> list:
    """各子题轮流占用证据预算；相同来源、页码、正文去重，保留版本差异。"""
    if top_k < 1:
        raise ValueError("证据预算必须大于零。")
    found, seen = [], set()
    for rank in range(max((len(group) for group in groups), default=0)):
        for group in groups:
            if rank >= len(group):
                continue
            item = group[rank]
            key = (item.source.replace("\\", "/"), item.page, re.sub(r"\s+", "", item.text))
            if key in seen:
                continue
            seen.add(key)
            found.append(replace(item, label=f"S{len(found) + 1}"))
            if len(found) == top_k:
                return found
    return found


def retrieve_arm(question, settings, strategy, retrieve_fn):
    queries = split_queries(question) if strategy == "split" else [question]
    groups = [retrieve_fn(query, settings, top_k=4) for query in queries]
    evidence = merge_round_robin(groups) if len(queries) > 1 else groups[0]
    return evidence, queries


def run_pair(case, settings, repeats, retrieve_fn, *, generate_answers=False, generate_fn=generate):
    """配对运行；轮换顺序减小缓存偏差；任一子查询失败使该组失败。"""
    row = {
        "id": case["id"],
        "question": case["question"],
        "stratum": case["stratum"],
        "should_abstain": case["should_abstain"],
        "query_changed": len(split_queries(case["question"])) > 1,
        "arms": {name: {"status": "ok", "retrieval_seconds": []} for name in ("whole", "split")},
    }
    last_evidence = {}
    for repeat in range(repeats):
        order = ("whole", "split") if repeat % 2 == 0 else ("split", "whole")
        for name in order:
            arm = row["arms"][name]
            if arm["status"] == "error":
                continue
            started = time.perf_counter()
            try:
                evidence, queries = retrieve_arm(case["question"], settings, name, retrieve_fn)
                arm["retrieval_seconds"].append(time.perf_counter() - started)
                arm.update(
                    queries=queries,
                    queries_per_repeat=len(queries),
                    retrieved=[asdict(e) for e in evidence],
                )
                last_evidence[name] = evidence
            except Exception as exc:  # noqa: BLE001 - 不记录第三方异常正文
                arm.update(status="error", error_type=error_type(exc))
    for name, arm in row["arms"].items():
        if arm["status"] != "ok":
            continue
        arm["metrics"] = retrieval_metrics(case, arm["retrieved"])
        if generate_answers and row["query_changed"]:
            started = time.perf_counter()
            arm["generation_status"] = "error"
            arm["api_attempted"] = bool(last_evidence[name])
            try:
                arm.update(generate_fn(case["question"], last_evidence[name], settings))
                arm["generation_status"] = "ok"
                arm["checks"] = answer_checks(arm["answer"], arm["retrieved"])
            except Exception as exc:  # noqa: BLE001
                arm["generation_error_type"] = error_type(exc)
            arm["generation_seconds"] = time.perf_counter() - started
    return row


def summarize_pairs(rows):
    eligible = [r for r in rows if not r["should_abstain"]]
    paired = [r for r in eligible if all(a["status"] == "ok" for a in r["arms"].values())]
    result = {
        "attempted": len(rows),
        "positive_cases": len(eligible),
        "paired_positive": len(paired),
    }
    for name in ("whole", "split"):
        arms = [r["arms"][name] for r in rows]
        positive = [r["arms"][name] for r in paired]
        generated = [a for a in arms if a.get("generation_status") == "ok"]
        observed = [a["usage"] for a in generated if a.get("usage") is not None]
        result[name] = {
            "retrieval_failed": sum(a["status"] != "ok" for a in arms),
            "anchor_hit_at_4": average([a["metrics"]["anchor_hit_at_4"] for a in positive]),
            "annotated_recall_at_4_macro": average(
                [a["metrics"]["annotated_recall_at_4"] for a in positive]
            ),
            "retrieval_latency": distribution(
                [t for a in arms if a["status"] == "ok" for t in a["retrieval_seconds"]]
            ),
            "generation_attempted_cases": sum("generation_status" in a for a in arms),
            "generation_failed": sum(a.get("generation_status") == "error" for a in arms),
            "usage_observed_calls": len(observed),
            "observed_input_tokens": sum(u["input_tokens"] for u in observed) if observed else None,
            "observed_output_tokens": sum(u["output_tokens"] for u in observed)
            if observed
            else None,
            "estimated_cost": None,
        }
    result["improved_ids"] = [
        r["id"]
        for r in paired
        if not r["arms"]["whole"]["metrics"]["anchor_hit_at_4"]
        and r["arms"]["split"]["metrics"]["anchor_hit_at_4"]
    ]
    result["regressed_ids"] = [
        r["id"]
        for r in paired
        if r["arms"]["whole"]["metrics"]["anchor_hit_at_4"]
        and not r["arms"]["split"]["metrics"]["anchor_hit_at_4"]
    ]
    return result


def build_reviews(report):
    binding = hashlib.sha256(
        json.dumps(report["results"], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return {
        "dataset_sha256": report["dataset_sha256"],
        "results_sha256": binding,
        "reviewer": {"kind": "human"},
        "reviews": [
            {
                "id": row["id"],
                "arm": name,
                "hit_at_4": None,
                "answer_correct": None,
                "citation_supported": None,
                "reason": "",
            }
            for row in report["results"]
            if row["query_changed"]
            for name, arm in row["arms"].items()
            if arm["status"] == "ok"
        ],
    }


def summarize_reviews(report, reviews):
    """绑定原始结果，区分AI/真人，只统计适用且已审核的字段。"""
    template = build_reviews(report)
    if any(reviews.get(k) != template[k] for k in ("dataset_sha256", "results_sha256")):
        raise ValueError("审核未绑定当前题集与原始结果。")
    kind = reviews.get("reviewer", {}).get("kind")
    if kind not in {"ai", "human"}:
        raise ValueError("必须明确审核者为ai或human。")
    allowed = {(r["id"], r["arm"]) for r in template["reviews"]}
    seen = set()
    for review in reviews["reviews"]:
        key = (review["id"], review["arm"])
        if key not in allowed or key in seen:
            raise ValueError("审核题号/组名不适用或重复。")
        seen.add(key)
        original = next(r for r in report["results"] if r["id"] == review["id"])
        arm = original["arms"][review["arm"]]
        for field in ("hit_at_4", "answer_correct", "citation_supported"):
            value = review.get(field)
            if value is not None and type(value) is not bool:
                raise ValueError("审核值只能为true、false或null。")
            if field != "hit_at_4" and value is not None and "answer" not in arm:
                raise ValueError("未生成回答，不能审核答案或引用。")
            if (
                field == "citation_supported"
                and value is not None
                and not arm["checks"]["has_citation"]
            ):
                raise ValueError("无引用回答不能计引用支持。")
        if (
            any(
                review.get(k) is not None
                for k in ("hit_at_4", "answer_correct", "citation_supported")
            )
            and not review.get("reason", "").strip()
        ):
            raise ValueError("已审核项必须说明理由。")
    result = {"reviewer": reviews["reviewer"], "by_stratum": {}}
    for stratum in sorted({r["stratum"] for r in report["results"] if r["query_changed"]}):
        result["by_stratum"][stratum] = {}
        for name in ("whole", "split"):
            originals = {
                r["id"]: r["arms"][name]
                for r in report["results"]
                if r["stratum"] == stratum
                and r["query_changed"]
                and r["arms"][name]["status"] == "ok"
            }
            fields = {}
            for field in ("hit_at_4", "answer_correct", "citation_supported"):
                ids = {
                    key
                    for key, arm in originals.items()
                    if field == "hit_at_4"
                    or (
                        "answer" in arm
                        and (field != "citation_supported" or arm["checks"]["has_citation"])
                    )
                }
                values = [
                    r[field]
                    for r in reviews["reviews"]
                    if r["id"] in ids and r["arm"] == name and type(r.get(field)) is bool
                ]
                fields[field] = {
                    "eligible": len(ids),
                    "reviewed": len(values),
                    "rate": average(values),
                }
            result["by_stratum"][stratum][name] = fields
    return result


@protect_external_call
def main():
    parser = argparse.ArgumentParser(description="第二周单变量对照；默认仅校验，不访问服务")
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "eval/datasets/week2.json")
    parser.add_argument("--retrieve", action="store_true")
    parser.add_argument("--generate", action="store_true", help="组合题两组都生成，会产生 API 用量")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--review-report", type=Path, help="离线汇总已有配对结果")
    parser.add_argument("--reviews", type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.output.with_suffix(".reviews.json").exists():
        parser.error("结果或审核文件已存在，请使用新路径。")
    if args.repeats < 1:
        parser.error("重复次数必须大于零。")
    if bool(args.review_report) != bool(args.reviews) or (
        args.review_report and (args.retrieve or args.generate)
    ):
        parser.error("审核汇总必须同时指定报告和审核文件，不能混用真实运行。")
    if args.review_report:
        report = json.loads(args.review_report.read_text(encoding="utf-8"))
        reviews = json.loads(args.reviews.read_text(encoding="utf-8"))
        report[
            "semantic_ai_review"
            if reviews.get("reviewer", {}).get("kind") == "ai"
            else "semantic_manual"
        ] = summarize_reviews(report, reviews)
        report["reviews_sha256"] = digest(args.reviews)
        report["review_summary_code_sha256"] = digest(Path(__file__))
        write_json_atomic(args.output, report)
        print("审核汇总已保存；未调用外部服务。")
        return
    settings = get_settings()
    if args.generate and not settings.api_key:
        parser.error("生成需要配置 API 密钥。")
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    checked = validate_dataset(dataset, settings.data_dir)
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "status": "validated_only",
        "dataset_sha256": digest(args.dataset),
        "git_head": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip(),
        "code_sha256": {
            name: digest(PROJECT_ROOT / f"src/research_agent/{name}.py")
            for name in ("retrieval", "answer", "evaluation/baseline", "evaluation/split_retrieval")
        },
        "scope": "基础集合只读配对实验；公开回归与新编开发题分列；不代表独立验收或业务全链路",
        "settings": {
            "collection": settings.collection_name,
            "embedding": settings.embedding_model,
            "threshold": settings.rag_relevance_threshold,
            "final_top_k": 4,
            "per_query_top_k": 4,
            "repeats": args.repeats,
            "model": settings.chat_model,
            "generation_max_tokens": 700,
            "generation_max_retries": 0,
            "splitter": "连续①②③④；取各子题原文；不读取参考答案或锚点",
            "merge": "按子题轮流选择、去重并重新编号",
        },
        "dataset_validation": checked,
        "results": [],
    }
    write_json_atomic(args.output, report)
    if not (args.retrieve or args.generate):
        print("题集校验通过；未访问外部服务。")
        return
    started = time.perf_counter()
    try:
        from research_agent.retrieval import retrieve

        retrieve(dataset["cases"][0]["question"], settings, top_k=4)
    except Exception as exc:  # noqa: BLE001
        report.update(status="blocked", blocker_type=error_type(exc))
        write_json_atomic(args.output, report)
        print(f"检索受阻：{error_type(exc)}；未生成指标。")
        return
    report["cold_import_and_first_query_seconds"] = time.perf_counter() - started
    for case in dataset["cases"]:
        report["results"].append(
            run_pair(case, settings, args.repeats, retrieve, generate_answers=args.generate)
        )
        report["status"] = "running"
        report["summary"] = summarize_pairs(report["results"])
        write_json_atomic(args.output, report)
        print(f"{case['id']} 已保存", flush=True)
    report["by_stratum"] = {
        name: summarize_pairs([r for r in report["results"] if r["stratum"] == name])
        for name in sorted({r["stratum"] for r in report["results"]})
    }
    failed = any(
        a["status"] != "ok" or a.get("generation_status") == "error"
        for row in report["results"]
        for a in row["arms"].values()
    )
    report["status"] = "completed_with_errors" if failed else "completed"
    write_json_atomic(args.output, report)
    write_json_atomic(args.output.with_suffix(".reviews.json"), build_reviews(report))
    print("配对实验完成；自动锚点指标不等于语义正确率。")
