"""质量实验应隔离变量，保留缺证和失败。"""

import pytest

from research_agent.evaluation.quality import (
    build_reviews,
    rank_scope,
    run_case,
    summarize,
    summarize_reviews,
)
from research_agent.retrieval import Evidence


def case():
    return {
        "id": "M99",
        "question": "①《甲志》前言谁编？②《乙志》多少卷？",
        "stratum": "multi_evidence",
        "should_abstain": False,
        "gold_groups": [
            {"source": "甲志.md", "anchors": ["甲"]},
            {"source": "乙志.md", "anchors": ["乙"]},
        ],
    }


def test_scope_uses_filename_not_parent_or_gold_and_keeps_fallback():
    wrong = Evidence("S1", "前言甲", "甲志/乙志.md")
    right = Evidence("S2", "甲", "资料/甲 志.md")
    assert rank_scope("《甲志》前言谁编", [wrong, right]) == [right, wrong]
    assert rank_scope("未知问题", [wrong, right]) == [wrong, right]
    assert rank_scope("《不存在》谁编", [wrong, right]) == [wrong, right]


def test_stage_b_uses_identical_evidence_and_only_changes_prompt():
    calls, generated = [], []

    def retrieve(q, settings, top_k):
        calls.append((q, top_k))
        return [Evidence("X", "甲", "甲志.md"), Evidence("Y", "乙", "乙志.md")]

    def generate(q, evidence, settings, constrained):
        generated.append((evidence, constrained))
        return {"answer": "甲[S1]", "usage": None, "api_attempted": True}

    row = run_case(case(), None, 3, retrieve, generate=True, generate_fn=generate)
    assert len(calls) == 6 and {k for _, k in calls} == {12}
    assert row["arms"]["scoped"]["retrieved"] == row["arms"]["constrained"]["retrieved"]
    assert len(generated) == 3 and sum(c for _, c in generated) == 1
    assert all(len(a["retrieved"]) <= 4 for a in row["arms"].values())


def test_failed_subquery_has_no_quality_scores_or_exception_body():
    def retrieve(q, settings, top_k):
        raise RuntimeError("不应保存的异常正文")

    row = run_case(case(), None, 1, retrieve)
    assert "不应保存" not in str(row)
    result = summarize([row])
    assert result["retrieval_failed"] == 1
    assert result["plain"]["hit_at_4"] is None


def test_generation_failure_preserves_retrieval_and_negative_denominator():
    def generate(*args, **kwargs):
        raise ValueError("不应保存")

    negative = {**case(), "should_abstain": True, "gold_groups": []}
    row = run_case(
        negative,
        None,
        1,
        lambda *a, **kw: [Evidence("S1", "干扰", "a")],
        generate=True,
        generate_fn=generate,
    )
    result = summarize([row])
    assert result["positive_denominator"] == 0
    assert result["constrained"]["generation_failed"] == 1
    assert result["constrained"]["estimated_cost"] is None


def test_review_binding_applicability_and_missing_scores():
    row = run_case(
        case(),
        None,
        1,
        lambda *a, **kw: [Evidence("S1", "甲", "甲志.md")],
        generate=True,
        generate_fn=lambda *a, **kw: {"answer": "甲[S1]"},
    )
    report = {"dataset_sha256": "fixed", "results": [row]}
    reviews = build_reviews(report)
    reviews["reviewer"] = {"kind": "ai"}
    result = summarize_reviews(report, reviews)
    assert result["summary"]["scoped"]["answer_correct"] == {
        "eligible": 1,
        "reviewed": 0,
        "passed": 0,
        "rate": None,
    }
    reviews["reviews"][0].update(answer_correct=True, reason="按证据核对")
    assert summarize_reviews(report, reviews)["reviewer"]["kind"] == "ai"
    reviews["reviews"][0]["abstention_correct"] = True
    with pytest.raises(ValueError, match="不适用"):
        summarize_reviews(report, reviews)
    reviews["results_sha256"] = "changed"
    with pytest.raises(ValueError, match="未绑定"):
        summarize_reviews(report, reviews)


def test_frozen_generation_scope_and_duplicates_do_not_hide_versions():
    import json

    from research_agent.config import PROJECT_ROOT
    from research_agent.evaluation.quality import select_evidence, should_generate

    dataset = json.loads((PROJECT_ROOT / "eval/datasets/week2.json").read_text(encoding="utf-8"))
    assert sum(should_generate(c) for c in dataset["cases"]) == 25
    a = Evidence("S1", "相同正文", "甲志/甲志.md")
    b = Evidence("S2", "相同正文", "另一版/甲志.md")
    result = select_evidence(["《甲志》"], [[a, a, b]], True)
    assert [e.source for e in result] == [a.source, b.source]
