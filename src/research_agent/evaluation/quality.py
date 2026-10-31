"""第三周质量实验：共享候选池的来源排序，以及固定证据的提示词对照。"""

import argparse
import hashlib
import json
import re
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from research_agent.answer import BASE_SYSTEM_PROMPT as SYSTEM_PROMPT
from research_agent.answer import render_context
from research_agent.config import PROJECT_ROOT, get_settings
from research_agent.evaluation.acceptance import digest, distribution
from research_agent.evaluation.baseline import (
    answer_checks,
    average,
    retrieval_metrics,
    validate_dataset,
)
from research_agent.evaluation.split_retrieval import merge_round_robin, split_queries
from research_agent.security import error_type, protect_external_call
from research_agent.storage import write_json_atomic

QUALITY_GUIDANCE = """回答前逐项核对问题所问的对象、范围和证据，不输出内部推理过程：
1. 区分全书与分卷、原稿与编入后的卷数、最初成稿与后续修订。数字必须属于问题所问的对象，不能用附近另一对象的数字替代。
2. 区分编纂、增订、标点、主编、编修组、出版社等角色。结合文件名及正文的年份、出版信息确认版本；证据未明确的角色不要补全。
3. 同一对象有不同版本或来源异说时，分别注明来源和适用范围，不拼接成一个结论。资料无法辨明时明确说明。
4. 逐项回答全部子问题。每项能确认的事实紧邻附上直接支持它的标签；一个标签不能替不相关的事实背书。不要仅在段末附上标签清单。
5. 某项缺证时明确说明哪项无法确认，可以回答有证据的部分。相关地名、书名或相邻事件不能证明所问事实；不要为满足用户要求猜测答案。"""

# 固定开发回归子集；其余单来源题仅检索，不宣称已验证其生成质量。
SINGLE_GENERATION_IDS = frozenset({"Q11", "H01", "H04", "H07", "H08", "H11", "H14", "H17"})
ARMS = ("plain", "scoped", "constrained")


def compact(value):
    return re.sub(r"[\W_]+", "", value.casefold())


def rank_scope(query, candidates):
    """只用问题里的书名/章节和候选元数据；相同优先级保持原向量顺序。"""
    titles = [compact(t) for t in re.findall(r"《([^》]+)》", query)]
    sections = [word for word in ("前言", "凡例", "发刊词", "版权页", "题名页") if word in query]
    if not titles and not sections:
        return list(candidates)

    def priority(item):
        # 仅比较文件名，避免资料集父目录的名称造成所有候选误命中。
        filename = compact(item.source.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0])
        return (
            any(title and title in filename for title in titles),
            any(section in item.text for section in sections),
        )

    return sorted(candidates, key=priority, reverse=True)


def select_evidence(queries, groups, scoped):
    ranked = [rank_scope(q, g) if scoped else g for q, g in zip(queries, groups, strict=True)]
    return merge_round_robin(ranked, top_k=4)


def should_generate(case):
    return (
        case["id"] in SINGLE_GENERATION_IDS
        or case["should_abstain"]
        or len(split_queries(case["question"])) > 1
    )


def generate_answer(question, evidence, settings, constrained=False):
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

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
            SystemMessage(content=SYSTEM_PROMPT + ("\n" + QUALITY_GUIDANCE if constrained else "")),
            HumanMessage(content=f"问题：{question}\n\n证据：\n{render_context(evidence)}"),
        ]
    )
    return {
        "answer": str(response.content),
        "usage": response.usage_metadata,
        "api_attempted": True,
        "finish_reason": response.response_metadata.get("finish_reason"),
    }


def run_case(case, settings, repeats, retrieve_fn, *, generate=False, generate_fn=generate_answer):
    queries = split_queries(case["question"])
    row = {
        "id": case["id"],
        "question": case["question"],
        "stratum": case["stratum"],
        "should_abstain": case["should_abstain"],
        "queries": queries,
        "status": "ok",
        "retrieval_seconds": [],
        "arms": {},
    }
    try:
        for _ in range(repeats):
            started = time.perf_counter()
            groups = [retrieve_fn(q, settings, top_k=12) for q in queries]
            row["retrieval_seconds"].append(time.perf_counter() - started)
        row["candidate_groups"] = [[asdict(e) for e in group] for group in groups]
        selected = {}
        for name in ARMS:
            started = time.perf_counter()
            evidence = select_evidence(queries, groups, scoped=name != "plain")
            selected[name] = evidence
            arm = {
                "retrieved": [asdict(e) for e in evidence],
                "selection_seconds": time.perf_counter() - started,
            }
            arm["metrics"] = retrieval_metrics(case, arm["retrieved"])
            row["arms"][name] = arm
    except Exception as exc:  # noqa: BLE001 - 不保存外部异常正文
        row.update(status="error", error_type=error_type(exc))
        return row
    if generate and should_generate(case):
        # 按题号轮换请求顺序，避免总让某个提示词享受后发缓存。
        offset = sum(case["id"].encode()) % 3
        for name in ARMS[offset:] + ARMS[:offset]:
            arm = row["arms"][name]
            started = time.perf_counter()
            arm.update(generation_status="error", api_attempted=bool(selected[name]))
            try:
                arm.update(
                    generate_fn(
                        case["question"],
                        selected[name],
                        settings,
                        constrained=name == "constrained",
                    )
                )
                arm["generation_status"] = "ok"
                arm["checks"] = answer_checks(arm["answer"], arm["retrieved"])
            except Exception as exc:  # noqa: BLE001
                arm["error_type"] = error_type(exc)
            arm["generation_seconds"] = time.perf_counter() - started
    return row


def summarize(rows):
    successful = [r for r in rows if r["status"] == "ok"]
    positive = [r for r in successful if not r["should_abstain"]]
    result = {
        "attempted": len(rows),
        "retrieval_failed": len(rows) - len(successful),
        "positive_denominator": len(positive),
        "shared_retrieval_latency": distribution(
            [t for r in successful for t in r["retrieval_seconds"]]
        ),
    }
    for name in ARMS:
        arms = [r["arms"][name] for r in successful]
        usage = [a["usage"] for a in arms if a.get("usage")]
        result[name] = {
            "hit_at_4": average([r["arms"][name]["metrics"]["anchor_hit_at_4"] for r in positive]),
            "recall_at_4": average(
                [r["arms"][name]["metrics"]["annotated_recall_at_4"] for r in positive]
            ),
            "generation_succeeded": sum(a.get("generation_status") == "ok" for a in arms),
            "generation_failed": sum(a.get("generation_status") == "error" for a in arms),
            "usage_observed_calls": len(usage),
            "input_tokens": sum(u["input_tokens"] for u in usage) if usage else None,
            "output_tokens": sum(u["output_tokens"] for u in usage) if usage else None,
            "estimated_cost": None,
            "generation_latency": distribution(
                [a["generation_seconds"] for a in arms if a.get("generation_status") == "ok"]
            ),
        }
    result["improved_ids"] = [
        r["id"]
        for r in positive
        if not r["arms"]["plain"]["metrics"]["anchor_hit_at_4"]
        and r["arms"]["scoped"]["metrics"]["anchor_hit_at_4"]
    ]
    result["regressed_ids"] = [
        r["id"]
        for r in positive
        if r["arms"]["plain"]["metrics"]["anchor_hit_at_4"]
        and not r["arms"]["scoped"]["metrics"]["anchor_hit_at_4"]
    ]
    return result


def build_reviews(report):
    return {
        "dataset_sha256": report["dataset_sha256"],
        "results_sha256": hashlib.sha256(
            json.dumps(report["results"], sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest(),
        "reviewer": {"kind": "human"},
        "reviews": [
            {
                "id": r["id"],
                "arm": name,
                "answer_correct": None,
                "citation_supported": None,
                "abstention_correct": None,
                "reason": "",
            }
            for r in report["results"]
            for name, arm in r["arms"].items()
            if arm.get("generation_status") == "ok"
        ],
    }


def summarize_reviews(report, reviews):
    """审核绑定实际输出；空白、缺证、无引用和AI审核分别统计。"""
    template = build_reviews(report)
    if any(reviews.get(k) != template[k] for k in ("dataset_sha256", "results_sha256")):
        raise ValueError("审核未绑定当前结果。")
    if reviews.get("reviewer", {}).get("kind") not in {"ai", "human"}:
        raise ValueError("审核者必须标明ai或human。")
    allowed = {(r["id"], r["arm"]) for r in template["reviews"]}
    originals = {r["id"]: r for r in report["results"]}
    fields = ("answer_correct", "citation_supported", "abstention_correct")
    checked = {}
    for review in reviews["reviews"]:
        key = (review["id"], review["arm"])
        if key not in allowed or key in checked:
            raise ValueError("审核题号或组名不适用或重复。")
        row = originals[key[0]]
        arm = row["arms"][key[1]]
        for field in fields:
            value = review.get(field)
            if value is None:
                continue
            if type(value) is not bool:
                raise ValueError("审核值只能为布尔值或null。")
            if (field == "answer_correct" and row["should_abstain"]) or (
                field == "abstention_correct" and not row["should_abstain"]
            ):
                raise ValueError("正负题审核字段不适用。")
            if field == "citation_supported" and not arm["checks"]["has_citation"]:
                raise ValueError("无引用回答的引用支持应为null。")
        if any(review.get(f) is not None for f in fields) and not review.get("reason", "").strip():
            raise ValueError("审核必须说明理由。")
        checked[key] = review

    def aggregate(stratum=None):
        result = {}
        for name in ARMS:
            result[name] = {}
            for field in fields:
                keys = [
                    key
                    for key in allowed
                    if key[1] == name
                    and (stratum is None or originals[key[0]]["stratum"] == stratum)
                    and (
                        (field == "answer_correct" and not originals[key[0]]["should_abstain"])
                        or (field == "abstention_correct" and originals[key[0]]["should_abstain"])
                        or (
                            field == "citation_supported"
                            and originals[key[0]]["arms"][name]["checks"]["has_citation"]
                        )
                    )
                ]
                values = [
                    checked[k][field]
                    for k in keys
                    if k in checked and type(checked[k].get(field)) is bool
                ]
                result[name][field] = {
                    "eligible": len(keys),
                    "reviewed": len(values),
                    "passed": sum(values),
                    "rate": average(values),
                }
        return result

    pairs = {}
    for control, treatment in (("plain", "scoped"), ("scoped", "constrained")):
        pairs[f"{control}_to_{treatment}"] = {}
        for field in fields:
            ids = [
                key
                for key in originals
                if type(checked.get((key, control), {}).get(field)) is bool
                and type(checked.get((key, treatment), {}).get(field)) is bool
            ]
            pairs[f"{control}_to_{treatment}"][field] = {
                "paired_reviewed": len(ids),
                "improved_ids": [
                    key
                    for key in ids
                    if not checked[(key, control)][field] and checked[(key, treatment)][field]
                ],
                "regressed_ids": [
                    key
                    for key in ids
                    if checked[(key, control)][field] and not checked[(key, treatment)][field]
                ],
            }
    return {
        "reviewer": reviews["reviewer"],
        "summary": aggregate(),
        "paired_changes": pairs,
        "by_stratum": {s: aggregate(s) for s in sorted({r["stratum"] for r in report["results"]})},
    }


@protect_external_call
def main():
    parser = argparse.ArgumentParser(description="第三周质量实验；默认仅校验")
    parser.add_argument("--retrieve", action="store_true")
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--review-report", type=Path, help="只读汇总已有结果，不调用服务")
    parser.add_argument("--reviews", type=Path)
    args = parser.parse_args()
    if (
        args.repeats < 1
        or args.output.exists()
        or args.output.with_suffix(".reviews.json").exists()
    ):
        parser.error("重复次数必须为正，输出与审核路径必须尚不存在。")
    if bool(args.review_report) != bool(args.reviews) or (
        args.review_report and (args.retrieve or args.generate)
    ):
        parser.error("审核必须同时指定报告与审核文件，不能混用真实运行。")
    if args.review_report:
        report = json.loads(args.review_report.read_text(encoding="utf-8"))
        reviews = json.loads(args.reviews.read_text(encoding="utf-8"))
        field = (
            "semantic_ai_review"
            if reviews.get("reviewer", {}).get("kind") == "ai"
            else "semantic_manual"
        )
        report[field] = summarize_reviews(report, reviews)
        report["reviews_sha256"] = digest(args.reviews)
        report["review_code_sha256"] = digest(Path(__file__))
        write_json_atomic(args.output, report)
        print("审核汇总已保存；未调用外部服务。")
        return
    settings = get_settings()
    path = PROJECT_ROOT / "eval/datasets/week2.json"
    dataset = json.loads(path.read_text(encoding="utf-8"))
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "status": "validated_only",
        "dataset_sha256": digest(path),
        "validation": validate_dataset(dataset, settings.data_dir),
        "code_sha256": {
            name: digest(PROJECT_ROOT / f"src/research_agent/{name}.py")
            for name in (
                "evaluation/quality",
                "retrieval",
                "answer",
                "evaluation/baseline",
                "evaluation/split_retrieval",
            )
        },
        "settings": {
            "model": settings.chat_model,
            "embedding": settings.embedding_model,
            "collection": settings.collection_name,
            "threshold": settings.rag_relevance_threshold,
            "candidate_top_k": 12,
            "final_top_k": 4,
            "repeats": args.repeats,
            "temperature": 0,
            "max_tokens": 700,
            "max_retries": 0,
        },
        "prompts": {"base": SYSTEM_PROMPT, "addition": QUALITY_GUIDANCE},
        "generation_ids": [c["id"] for c in dataset["cases"] if should_generate(c)],
        "scope": "公开开发回归；共享分题候选池；不代表独立验收、业务默认或完整研究图",
        "results": [],
    }
    write_json_atomic(args.output, report)
    if not (args.retrieve or args.generate):
        print("题集校验完成；未访问外部服务。")
        return
    from research_agent.retrieval import retrieve

    started = time.perf_counter()
    try:
        retrieve(dataset["cases"][0]["question"], settings, top_k=12)
    except Exception as exc:  # noqa: BLE001
        report.update(status="blocked", error_type=error_type(exc))
        write_json_atomic(args.output, report)
        print(f"预热受阻：{error_type(exc)}")
        return
    report["cold_import_and_query_seconds"] = time.perf_counter() - started
    for case in dataset["cases"]:
        report["results"].append(
            run_case(case, settings, args.repeats, retrieve, generate=args.generate)
        )
        report.update(status="running", summary=summarize(report["results"]))
        write_json_atomic(args.output, report)
        print(f"{case['id']} 已保存", flush=True)
    report["by_stratum"] = {
        s: summarize([r for r in report["results"] if r["stratum"] == s])
        for s in sorted({c["stratum"] for c in dataset["cases"]})
    }
    failed = any(
        r["status"] != "ok"
        or any(a.get("generation_status") == "error" for a in r["arms"].values())
        for r in report["results"]
    )
    report["status"] = "completed_with_errors" if failed else "completed"
    write_json_atomic(args.output, report)
    write_json_atomic(args.output.with_suffix(".reviews.json"), build_reviews(report))
    print("实验完成；锚点与标签检查不能代替语义审核。")
