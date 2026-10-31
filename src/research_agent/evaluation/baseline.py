"""第一周只读分层基线：自动代理指标与人工语义指标分开记录。"""

import argparse
import hashlib
import json
import math
import platform
import re
import subprocess
import time
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from research_agent.config import PROJECT_ROOT, get_settings
from research_agent.evaluation.acceptance import digest, distribution
from research_agent.evaluation.retrieval import normalize
from research_agent.security import protect_external_call
from research_agent.storage import write_json_atomic


def validate_dataset(dataset: dict, data_dir: Path) -> dict:
    """校验原文摘要、锚点存在及合成实体缺失；不调用模型或向量库。"""
    cases = dataset["cases"]
    if not cases or len({r["id"] for r in cases}) != len(cases):
        raise ValueError("题集为空或 ID 重复。")
    sources = {}
    negatives = []
    for row in cases:
        if not row["should_abstain"] and not row["gold_groups"]:
            raise ValueError(f"缺少正样本证据：{row['id']}")
        for group in row["gold_groups"]:
            source = group["source"]
            path = (data_dir / source).resolve()
            if not path.is_relative_to(data_dir.resolve()):
                raise ValueError("证据路径超出资料目录。")
            if source not in sources:
                sources[source] = (digest(path), normalize(path.read_text(encoding="utf-8")))
            sha, text = sources[source]
            if sha != group["source_sha256"] or not group["anchors"]:
                raise ValueError(f"原文版本变化或空标注：{row['id']}")
            if any(not normalize(a) or normalize(a) not in text for a in group["anchors"]):
                raise ValueError(f"锚点未在原文找到：{row['id']}")
        if row["should_abstain"]:
            negatives.append(row["absence_check"]["entity"])
    files = list(data_dir.rglob("*.md"))
    for path in files:
        text = normalize(path.read_text(encoding="utf-8"))
        if any(normalize(entity) in text for entity in negatives):
            raise ValueError("合成负样本实体已出现在当前 Markdown 资料中。")
    return {
        "cases": len(cases),
        "source_files": len(sources),
        "absence_scanned_markdown": len(files),
    }


def retrieval_metrics(case: dict, evidence: list[dict]) -> dict:
    """每个 group 为一个子问题；Hit 要求同条证据包含该组全部锚点。"""
    if case["should_abstain"]:
        return {"anchor_hit_at_4": None, "annotated_recall_at_4": None}
    hits, covered, total = [], 0, 0
    for group in case["gold_groups"]:
        candidates = [
            normalize(e["text"])
            for e in evidence[:4]
            if e["source"].replace("\\", "/") == group["source"].replace("\\", "/")
        ]
        anchors = [normalize(a) for a in group["anchors"]]
        hits.append(any(all(a in text for a in anchors) for text in candidates))
        covered += sum(any(a in text for text in candidates) for a in anchors)
        total += len(anchors)
    return {"anchor_hit_at_4": all(hits), "annotated_recall_at_4": covered / total}


def answer_checks(raw: str, evidence: list[dict]) -> dict:
    # 检查原始输出，避免清洗无效标签后掩盖生成错误。
    cited = re.findall(r"\[(?:S|W|N|O)\d+\]", raw)
    valid = {f"[{e['label']}]" for e in evidence}
    return {
        "citation_occurrences": len(cited),
        "valid_citation_occurrences": sum(c in valid for c in cited),
        "invalid_labels": sorted(set(cited) - valid),
        "has_citation": bool(cited),
        "abstention_phrase_candidate": bool(
            re.search("资料.{0,8}(不足|没有足够)|无法确认|不能确认|无法回答", raw)
        ),
        "semantic_review": "pending_human_review",
    }


def token_cost(usage: dict | None, pricing: dict | None) -> dict:
    """按服务端实际 usage 计数；缓存拆分未知时不猜测费用。"""
    result = {"input_tokens": None, "output_tokens": None, "estimated_cost": None}
    if usage is None:
        return result
    inp, out = usage.get("input_tokens"), usage.get("output_tokens")
    result.update(input_tokens=inp, output_tokens=out)
    if inp is None or out is None or pricing is None:
        return result
    cached = usage.get("input_token_details", {}).get("cache_read")
    if cached is None or not 0 <= cached <= inp:
        return result
    result["estimated_cost"] = (
        (inp - cached) * pricing["input_miss_per_million"]
        + cached * pricing["input_hit_per_million"]
        + out * pricing["output_per_million"]
    ) / 1_000_000
    return result


def average(values: list) -> float | None:
    return sum(values) / len(values) if values else None


def summarize(
    rows: list[dict],
    reviews: list[dict] = (),
    *,
    stratify: bool = True,
    reviewer_kind: str = "human",
) -> dict:
    if reviewer_kind not in {"human", "ai"}:
        raise ValueError("审核者类型必须为 human 或 ai。")
    successful = [r for r in rows if r["status"] == "ok"]
    retrieved = [r for r in rows if "metrics" in r and "retrieval_seconds" in r]
    generated = [r for r in successful if "answer" in r]
    eligible = [r for r in retrieved if r["metrics"]["anchor_hit_at_4"] is not None]
    citations = sum(r["checks"]["citation_occurrences"] for r in generated)
    valid = sum(r["checks"]["valid_citation_occurrences"] for r in generated)
    calls = [r for r in rows if r.get("api_attempted")]
    costs = [r["tokens"]["estimated_cost"] for r in calls if r.get("tokens")]
    semantic = {}
    applicable = {
        "hit_at_4": {r["id"] for r in retrieved if not r["should_abstain"]},
        "answer_correct": {r["id"] for r in generated if not r["should_abstain"]},
        "citation_supported": {r["id"] for r in generated if r["checks"]["has_citation"]},
        "abstention_correct": {r["id"] for r in generated if r["should_abstain"]},
    }
    for field, ids in applicable.items():
        scores = [r[field] for r in reviews if r["id"] in ids and isinstance(r.get(field), bool)]
        semantic[field] = {"eligible": len(ids), "reviewed": len(scores), "rate": average(scores)}
    result = {
        "attempted": len(rows),
        "succeeded": len(successful),
        "failed": len(rows) - len(successful),
        "retrieval_metric_denominator": len(eligible),
        "anchor_hit_at_4": average([r["metrics"]["anchor_hit_at_4"] for r in eligible]),
        "annotated_recall_at_4_macro": average(
            [r["metrics"]["annotated_recall_at_4"] for r in eligible]
        ),
        "citation_label_validity": {
            "occurrences": citations,
            "rate": valid / citations if citations else None,
        },
        "answers_with_citation": sum(r["checks"]["has_citation"] for r in generated),
        "generated_answers": len(generated),
        "semantic_manual": semantic
        if reviewer_kind == "human"
        else {key: {**value, "reviewed": 0, "rate": None} for key, value in semantic.items()},
        "api_attempted": len(calls),
        "usage_observed_calls": sum(r.get("usage") is not None for r in calls),
        "observed_input_tokens": sum(r.get("tokens", {}).get("input_tokens") or 0 for r in calls)
        if any(r.get("usage") is not None for r in calls)
        else None,
        "observed_output_tokens": sum(r.get("tokens", {}).get("output_tokens") or 0 for r in calls)
        if any(r.get("usage") is not None for r in calls)
        else None,
        "estimated_total_cost": sum(costs)
        if calls and len(costs) == len(calls) and all(c is not None for c in costs)
        else None,
        "cost_scope": "仅本运行器单次回答 API；不含搜索、追问、研究图、基础设施；失败请求用量可能未知",
        "latency": {
            "retrieval": distribution([v for r in retrieved for v in r["retrieval_seconds"]]),
            "generation": distribution(
                [r["generation_seconds"] for r in generated if r.get("api_attempted")]
            ),
            "end_to_end": distribution([r["end_to_end_seconds"] for r in generated]),
            "failure_elapsed": distribution(
                [r["elapsed_seconds"] for r in rows if r["status"] == "error"]
            ),
        },
    }
    if reviewer_kind == "ai":
        result["semantic_ai_review"] = semantic
    if stratify:
        result["by_stratum"] = {
            name: summarize(
                [r for r in rows if r["stratum"] == name],
                reviews,
                stratify=False,
                reviewer_kind=reviewer_kind,
            )
            for name in sorted({r["stratum"] for r in rows})
        }
    return result


def load_reviews(path: Path, report: dict) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("reviewer", {}).get("kind", "human") not in {"human", "ai"}:
        raise ValueError("审核者类型必须为 human 或 ai。")
    binding = hashlib.sha256(
        json.dumps(report["results"], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    if (
        data.get("results_sha256") != binding
        or data.get("dataset_sha256") != report["dataset_sha256"]
    ):
        raise ValueError("人工审核未绑定本次原始结果和题集。")
    ids = {r["id"] for r in report["results"] if "metrics" in r}
    rows = data["reviews"]
    if len({r["id"] for r in rows}) != len(rows) or any(r["id"] not in ids for r in rows):
        raise ValueError("审核 ID 重复或不属于本次成功结果。")
    for row in rows:
        for key in ("hit_at_4", "answer_correct", "citation_supported", "abstention_correct"):
            if row.get(key) is not None and not isinstance(row[key], bool):
                raise ValueError("人工审核只能填 true、false 或 null。")
        original = next(r for r in report["results"] if r["id"] == row["id"])
        if original["should_abstain"] and row.get("hit_at_4") is not None:
            raise ValueError("无答案题不参与 Hit@4 分母。")
        if "answer" not in original and any(
            row.get(k) is not None
            for k in ("answer_correct", "citation_supported", "abstention_correct")
        ):
            raise ValueError("未生成回答的结果不能审核回答质量。")
        if original["should_abstain"] and row.get("answer_correct") is not None:
            raise ValueError("无答案题应填写拒答评测，不计可回答题答案正确率。")
        if not original["should_abstain"] and row.get("abstention_correct") is not None:
            raise ValueError("正样本不参与无答案拒答率。")
        if (
            not original.get("checks", {}).get("has_citation")
            and row.get("citation_supported") is not None
        ):
            raise ValueError("没有引用标签时，引用支持应为 null，覆盖缺失另计。")
        if any(
            isinstance(row.get(k), bool)
            for k in ("hit_at_4", "answer_correct", "citation_supported", "abstention_correct")
        ) and not row.get("reason"):
            raise ValueError("人工审核必须写明判断理由。")
    return rows


def generate(question: str, evidence: list, settings) -> dict:
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    from research_agent.answer import BASE_SYSTEM_PROMPT as SYSTEM_PROMPT
    from research_agent.answer import render_context

    if not evidence:
        return {
            "answer": "提供的资料没有足够信息来回答这个问题。",
            "usage": None,
            "api_attempted": False,
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
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=f"问题：{question}\n\n证据：\n{render_context(evidence)}"),
        ]
    )
    return {
        "answer": str(response.content),
        "usage": response.usage_metadata,
        "api_attempted": True,
        "finish_reason": response.response_metadata.get("finish_reason"),
    }


@protect_external_call
def main() -> None:
    parser = argparse.ArgumentParser(description="第一周基线；默认只校验，显式选择真实检索/生成")
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "eval/datasets/week1.json")
    parser.add_argument("--retrieve", action="store_true")
    parser.add_argument("--generate", action="store_true", help="真实单次回答，会产生 API 用量")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--pricing", type=Path, help="用户核实的带日期和来源的每百万 Token 价格 JSON"
    )
    parser.add_argument("--review-report", type=Path, help="只汇总已有结果，不调用外部服务")
    parser.add_argument("--reviews", type=Path)
    args = parser.parse_args()
    if args.reviews and not args.review_report:
        parser.error("--reviews 必须与 --review-report 一起使用。")
    if args.review_report and (args.retrieve or args.generate):
        parser.error("审核汇总不能与真实运行选项混用。")
    if args.review_report and args.pricing:
        parser.error("审核汇总不接受价格；离线重算费用请使用 scripts/cost_analysis.py。")
    if args.output.exists():
        parser.error("输出已存在，请使用新路径，避免覆盖历史证据。")
    if not args.review_report and args.output.with_suffix(".reviews.json").exists():
        parser.error("同名审核模板已存在，请使用新输出路径。")
    if args.repeats < 1 or (args.limit is not None and args.limit < 1):
        parser.error("重复次数和题数必须大于零。")
    if args.review_report:
        report = json.loads(args.review_report.read_text(encoding="utf-8"))
        reviews = load_reviews(args.reviews, report) if args.reviews else []
        reviewer = (
            json.loads(args.reviews.read_text(encoding="utf-8")).get("reviewer", {"kind": "human"})
            if args.reviews
            else {"kind": "human"}
        )
        report["summary"] = summarize(report["results"], reviews, reviewer_kind=reviewer["kind"])
        report["reviewer"] = reviewer
        report["ai_reviews" if reviewer["kind"] == "ai" else "manual_reviews"] = reviews
        report["summary_code_sha256"] = digest(Path(__file__))
        write_json_atomic(args.output, report)
        return
    settings = get_settings()
    if args.generate and not settings.api_key:
        parser.error("真实生成需要已配置 API 密钥。")
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    checked = validate_dataset(dataset, settings.data_dir)
    cases = dataset["cases"][: args.limit]
    pricing = json.loads(args.pricing.read_text(encoding="utf-8")) if args.pricing else None
    if pricing:
        if (
            not all(pricing.get(k) for k in ("source_url", "currency", "model"))
            or not (pricing.get("effective_date") or pricing.get("access_date"))
            or pricing["model"] != settings.chat_model
        ):
            parser.error("价格必须包含来源、日期、币种和匹配的模型。")
        for key in ("input_miss_per_million", "input_hit_per_million", "output_per_million"):
            if (
                isinstance(pricing.get(key), bool)
                or not isinstance(pricing.get(key), (int, float))
                or not math.isfinite(pricing[key])
                or pricing[key] < 0
            ):
                parser.error("价格必须为非负有限数值。")
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "schema_version": 1,
        "status": "validated_only",
        "git_head": revision,
        "dataset_sha256": digest(args.dataset),
        "code_sha256": {
            name: digest(PROJECT_ROOT / f"src/research_agent/{name}.py")
            for name in ("retrieval", "answer", "evaluation/baseline")
        },
        "environment": {
            "python": platform.python_version(),
            "packages": {n: version(n) for n in ("langgraph", "langchain-openai", "torch")},
        },
        "scope": "基础集合，只读、单用户顺序检索+单次回答；不代表完整 Agent 或 LangGraph 研究效果",
        "settings": {
            "collection": settings.collection_name,
            "top_k": 4,
            "threshold": settings.rag_relevance_threshold,
            "model": settings.chat_model,
            "embedding": settings.embedding_model,
            "repeats": args.repeats,
            "generation_max_tokens": 700,
            "generation_max_retries": 0,
        },
        "dataset_validation": checked,
        "selected_cases": len(cases),
        "strata": dict(Counter(r["stratum"] for r in cases)),
        "pricing": pricing,
        "results": [],
    }
    write_json_atomic(args.output, report)
    if args.retrieve or args.generate:
        cold = time.perf_counter()
        try:
            from research_agent.retrieval import retrieve

            retrieve(cases[0]["question"], settings, top_k=4)
            report["cold_import_and_first_query_seconds"] = time.perf_counter() - cold
        except Exception as exc:  # noqa: BLE001
            report.update(status="blocked", blocker_type=type(exc).__name__)
            write_json_atomic(args.output, report)
            print(f"外部检索阻碍：{type(exc).__name__}；未生成指标。")
            return
        for case in cases:
            started = time.perf_counter()
            row = {
                "id": case["id"],
                "stratum": case["stratum"],
                "should_abstain": case["should_abstain"],
                "status": "error",
            }
            try:
                samples = []
                for _ in range(args.repeats):
                    tick = time.perf_counter()
                    evidence = retrieve(case["question"], settings, top_k=4)
                    samples.append(time.perf_counter() - tick)
                row.update(retrieved=[asdict(e) for e in evidence], retrieval_seconds=samples)
                row["metrics"] = retrieval_metrics(case, row["retrieved"])
                if args.generate:
                    tick = time.perf_counter()
                    row["api_attempted"] = bool(evidence)
                    row.update(generate(case["question"], evidence, settings))
                    row["generation_seconds"] = time.perf_counter() - tick
                    # 一个实际请求为最后一次检索+生成，不包含额外重复测量。
                    row["end_to_end_seconds"] = samples[-1] + row["generation_seconds"]
                    row["checks"] = answer_checks(row["answer"], row["retrieved"])
                    row["tokens"] = token_cost(row["usage"], pricing)
                row["status"] = "ok"
            except Exception as exc:  # noqa: BLE001
                row.update(
                    error_type=type(exc).__name__, elapsed_seconds=time.perf_counter() - started
                )
            report["results"].append(row)
            report["status"] = "running"
            report["summary"] = summarize(report["results"])
            write_json_atomic(args.output, report)
            print(f"{case['id']}：{row['status']}", flush=True)
        report["status"] = "completed_with_errors" if report["summary"]["failed"] else "completed"
    report["summary"] = summarize(report["results"])
    write_json_atomic(args.output, report)
    binding = hashlib.sha256(
        json.dumps(report["results"], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    review_path = args.output.with_suffix(".reviews.json")
    if review_path.exists():
        raise ValueError("审核模板已存在，不覆盖已有审核。")
    write_json_atomic(
        review_path,
        {
            "dataset_sha256": report["dataset_sha256"],
            "results_sha256": binding,
            "reviews": [
                {
                    "id": r["id"],
                    "hit_at_4": None,
                    "answer_correct": None,
                    "citation_supported": None,
                    "abstention_correct": None,
                    "reason": "",
                }
                for r in report["results"]
                if "metrics" in r
            ],
        },
    )
    print(f"基线已保存：{args.output}")


if __name__ == "__main__":
    main()
