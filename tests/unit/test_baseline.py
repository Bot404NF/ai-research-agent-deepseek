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
