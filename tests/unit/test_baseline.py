"""验证评测口径，重点覆盖会让成绩虚高或掩盖未知值的边界。"""

import hashlib
import json

import pytest

from research_agent.evaluation.baseline import (
    answer_checks,
    load_reviews,
    retrieval_metrics,
    summarize,
    token_cost,
    validate_dataset,
)


def case():
    return {
        "should_abstain": False,
        "gold_groups": [{"source": "book/a.md", "anchors": ["甲", "乙"]}],
    }


def test_top_four_scope_duplicate_and_partial_coverage():
    evidence = [{"source": "book/a.md", "text": "甲"}] * 4
    evidence.append({"source": "book/a.md", "text": "甲乙"})
    assert retrieval_metrics(case(), evidence) == {
        "anchor_hit_at_4": False,
        "annotated_recall_at_4": 0.5,
    }
    assert (
        retrieval_metrics(case(), [{"source": "wrong.md", "text": "甲乙"}])["annotated_recall_at_4"]
        == 0
    )
    assert (
        retrieval_metrics(case(), [{"source": "book\\a.md", "text": "甲 乙"}])["anchor_hit_at_4"]
        is True
    )


def test_combination_requires_all_groups_and_negative_not_in_denominator():
    row = case()
    row["gold_groups"].append({"source": "b.md", "anchors": ["丙"]})
    got = retrieval_metrics(row, [{"source": "book/a.md", "text": "甲乙"}])
    assert got["anchor_hit_at_4"] is False
    assert got["annotated_recall_at_4"] == pytest.approx(2 / 3)
    assert retrieval_metrics({"should_abstain": True}, [])["anchor_hit_at_4"] is None


def test_raw_citations_are_checked_and_no_semantic_success_is_inferred():
    checks = answer_checks("错误事实[S1][S1][S99]。资料不足。", [{"label": "S1"}])
    assert checks["citation_occurrences"] == 3
    assert checks["valid_citation_occurrences"] == 2
    assert checks["invalid_labels"] == ["[S99]"]
    assert checks["semantic_review"] == "pending_human_review"
    assert answer_checks("没有引用", [])["has_citation"] is False


def test_unknown_usage_and_cache_are_not_zero_cost():
    pricing = {"input_miss_per_million": 2, "input_hit_per_million": 1, "output_per_million": 3}
    assert token_cost(None, pricing)["estimated_cost"] is None
    assert token_cost({"input_tokens": 100, "output_tokens": 50}, pricing)["estimated_cost"] is None
    usage = {"input_tokens": 100, "output_tokens": 50, "input_token_details": {"cache_read": 20}}
    assert token_cost(usage, pricing)["estimated_cost"] == pytest.approx(0.00033)
    assert token_cost(usage, None)["input_tokens"] == 100


def test_empty_and_failed_runs_do_not_claim_quality_or_zero_latency():
    assert summarize([])["anchor_hit_at_4"] is None
    result = summarize(
        [
            {
                "id": "x",
                "stratum": "test",
                "status": "error",
                "elapsed_seconds": 2,
                "api_attempted": True,
            }
        ]
    )
    assert result["failed"] == 1
    assert result["observed_input_tokens"] is None
    assert result["estimated_total_cost"] is None
    assert result["latency"]["generation"]["p95_seconds"] is None
    assert result["latency"]["failure_elapsed"]["count"] == 1
    assert result["semantic_manual"]["answer_correct"]["reviewed"] == 0


def test_source_drift_and_negative_contamination_fail_validation(tmp_path):
    source = tmp_path / "a.md"
    source.write_text("甲乙", encoding="utf-8")
    group = {
        "source": "a.md",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "anchors": ["甲"],
    }
    data = {"cases": [{"id": "a", "should_abstain": False, "gold_groups": [group]}]}
    assert validate_dataset(data, tmp_path)["source_files"] == 1
    source.write_text("内容变化", encoding="utf-8")
    with pytest.raises(ValueError, match="原文版本变化"):
        validate_dataset(data, tmp_path)
    data = {
        "cases": [
            {
                "id": "n",
                "should_abstain": True,
                "gold_groups": [],
                "absence_check": {"entity": "内容变化"},
            }
        ]
    }
    with pytest.raises(ValueError, match="负样本实体"):
        validate_dataset(data, tmp_path)


def test_reviews_are_bound_to_results_and_require_reason(tmp_path):
    results = [{"id": "x", "status": "ok", "should_abstain": False, "metrics": {}}]
    binding = hashlib.sha256(
        json.dumps(results, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    review = {
        "dataset_sha256": "dataset",
        "results_sha256": binding,
        "reviews": [{"id": "x", "hit_at_4": True, "reason": ""}],
    }
    path = tmp_path / "review.json"
    path.write_text(json.dumps(review), encoding="utf-8")
    report = {"dataset_sha256": "dataset", "results": results}
    with pytest.raises(ValueError, match="判断理由"):
        load_reviews(path, report)
    review["reviews"][0]["reason"] = "前四条中存在完整原文支持"
    path.write_text(json.dumps(review), encoding="utf-8")
    assert len(load_reviews(path, report)) == 1
    report["results"] = []
    with pytest.raises(ValueError, match="未绑定"):
        load_reviews(path, report)


def test_generation_failure_preserves_retrieval_measurements():
    result = summarize(
        [
            {
                "id": "x",
                "stratum": "positive",
                "status": "error",
                "should_abstain": False,
                "metrics": {"anchor_hit_at_4": True, "annotated_recall_at_4": 1.0},
                "retrieval_seconds": [0.2],
                "api_attempted": True,
                "elapsed_seconds": 60,
            }
        ]
    )
    assert result["failed"] == 1
    assert result["retrieval_metric_denominator"] == 1
    assert result["anchor_hit_at_4"] == 1
    assert result["latency"]["retrieval"]["count"] == 1
    assert result["generated_answers"] == 0
    assert result["estimated_total_cost"] is None


def test_ai_review_never_counts_as_independent_human_review():
    row = {
        "id": "x",
        "status": "ok",
        "stratum": "positive",
        "should_abstain": False,
        "metrics": {"anchor_hit_at_4": True, "annotated_recall_at_4": 1},
        "retrieval_seconds": [0.1],
    }
    result = summarize([row], [{"id": "x", "hit_at_4": True}], reviewer_kind="ai")
    assert result["semantic_ai_review"]["hit_at_4"]["rate"] == 1
    assert result["semantic_manual"]["hit_at_4"]["reviewed"] == 0
    assert result["semantic_manual"]["hit_at_4"]["rate"] is None
    assert result["by_stratum"]["positive"]["semantic_manual"]["hit_at_4"]["rate"] is None


def test_no_citation_cannot_receive_citation_support_score(tmp_path):
    results = [
        {
            "id": "x",
            "status": "ok",
            "should_abstain": False,
            "metrics": {},
            "answer": "答复",
            "checks": {"has_citation": False},
        }
    ]
    data = {
        "dataset_sha256": "x",
        "results_sha256": hashlib.sha256(
            json.dumps(results, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest(),
        "reviewer": {"kind": "ai"},
        "reviews": [{"id": "x", "citation_supported": True, "reason": "不能虚构引用通过"}],
    }
    path = tmp_path / "reviews.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="引用支持应为 null"):
        load_reviews(path, {"dataset_sha256": "x", "results": results})


def test_offline_cost_preserves_unknown_calls_and_model_scope():
    from research_agent.evaluation.costs import analyze

    pricing = {
        "model": "test",
        "source_url": "https://example.invalid/fixture",
        "access_date": "2026-10-31",
        "currency": "CNY",
        "time_band": "fixture",
        "applicability": "仅测试，非实际价格",
        "input_hit_per_million": 1,
        "input_miss_per_million": 2,
        "output_per_million": 3,
    }
    report = {
        "settings": {"model": "test"},
        "results": [
            {
                "id": "ok",
                "api_attempted": True,
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "input_token_details": {"cache_read": 20},
                },
            },
            {"id": "unknown", "api_attempted": True},
        ],
    }
    result = analyze(report, pricing)
    assert result["priced_calls"] == 1
    assert result["estimated_total_cost"] is None
    assert result["observed_partial_cost"] == pytest.approx(0.00033)
    with pytest.raises(ValueError, match="模型与原始运行不匹配"):
        analyze(report, {**pricing, "model": "other"})


def test_injected_tool_failure_is_not_counted_as_live_success():
    from dataclasses import replace

    from research_agent.config import get_settings
    from research_agent.evaluation.tools import controlled, summarize_tools

    summary = summarize_tools(controlled(replace(get_settings(), api_key="evaluation-test")))
    assert summary["controlled_scenarios"] == summary["controlled_passed"] == 8
    assert summary["controlled_backend_attempts"] == 3
    assert summary["controlled_backend_successes"] == 2
    assert summary["live_tool_success_rate"] is None


def test_week1_ai_reviews_cover_every_applicable_case_and_bind_raw_data():
    from research_agent.config import PROJECT_ROOT

    report = json.loads(
        (PROJECT_ROOT / "eval/results/week1_live_20261031.json").read_text(encoding="utf-8")
    )
    path = PROJECT_ROOT / "eval/reports/week1_ai_reviews_20261031.json"
    reviews = load_reviews(path, report)
    assert {r["id"] for r in reviews} == {r["id"] for r in report["results"]}
    by_id = {r["id"]: r for r in reviews}
    for row in report["results"]:
        review = by_id[row["id"]]
        if row["should_abstain"]:
            assert isinstance(review["abstention_correct"], bool)
        else:
            assert isinstance(review["hit_at_4"], bool)
            assert isinstance(review["answer_correct"], bool)
        if row["checks"]["has_citation"]:
            assert isinstance(review["citation_supported"], bool)


def test_settings_repr_does_not_expose_api_key():
    from dataclasses import replace

    from research_agent.config import get_settings

    settings = replace(get_settings(), api_key="evaluation-secret-sentinel")
    assert "evaluation-secret-sentinel" not in repr(settings)
    assert settings.api_key == "evaluation-secret-sentinel"
