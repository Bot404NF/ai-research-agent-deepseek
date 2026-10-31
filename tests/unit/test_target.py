"""统一验收固定分母，不能把缺审核、无引用或失败计为达标。"""

import json

import pytest

from research_agent.config import PROJECT_ROOT
from research_agent.evaluation.target import review_template, run_case, summarize, wilson
from research_agent.retrieval import Evidence


def test_unified_dataset_has_one_schema_and_fixed_counts():
    data = json.loads((PROJECT_ROOT / "eval/datasets/unified.json").read_text(encoding="utf-8"))
    cases = data["cases"]
    assert [c["id"] for c in cases] == [f"T{i:03}" for i in range(1, 101)]
    assert sum(not c["should_abstain"] for c in cases) == 82
    assert sum(c["stratum"] == "multi_evidence" for c in cases) == 17
    assert not any("provenance" in c or "split" in c for c in cases)


def report():
    case = {
        "id": "T1",
        "question": "《甲志》谁编",
        "stratum": "multi_evidence",
        "should_abstain": False,
        "gold_groups": [{"source": "a", "anchors": ["甲"]}],
        "reference_answer": "不允许送入检索或回答的标准答案",
    }

    def retrieve(question, settings, top_k):
        assert question == case["question"]
        return [Evidence("S1", "甲", "a")]

    row = run_case(
        case,
        None,
        generate_answers=True,
        baseline_fn=retrieve,
        candidate_fn=retrieve,
        generate_fn=lambda question, *args, **kwargs: {"answer": "甲", "finish_reason": "stop"},
    )
    return {"dataset_sha256": "fixed", "results": [row]}


def test_unreviewed_and_uncited_answers_cannot_pass():
    r = report()
    v = review_template(r)
    v["reviewer"] = {"kind": "ai"}
    assert not summarize(r, v)["arms"]["candidate"]["answer_correct"]["met"]
    for review in v["reviews"]:
        review.update(answer_correct=True, citation_supported=True, reason="核心事实有证据但无标签")
    summary = summarize(r, v)
    assert summary["arms"]["candidate"]["answer_correct"]["passed"] == 1
    assert summary["arms"]["candidate"]["citation_supported"]["passed"] == 0
    assert summary["arms"]["candidate"]["citation_supported"]["eligible"] == 1
    assert not summary["arms"]["candidate"]["all_targets_met"]
    v["results_sha256"] = "changed"
    with pytest.raises(ValueError, match="未绑定"):
        summarize(r, v)


def test_truncated_and_failed_calls_remain_in_denominator():
    r = report()
    r["results"][0]["arms"]["candidate"]["finish_reason"] = "length"
    v = review_template(r)
    for review in v["reviews"]:
        review.update(answer_correct=True, reason="截断不得计成功")
    result = summarize(r, v)["arms"]["candidate"]["answer_correct"]
    assert result["eligible"] == 1 and result["passed"] == 0
    r["results"][0]["arms"]["candidate"]["status"] = "error"
    v = review_template(r)
    result = summarize(r, v)["arms"]["candidate"]["answer_correct"]
    assert result["eligible"] == 1 and result["passed"] == 0


def test_wilson_does_not_treat_small_sample_as_certainty():
    assert wilson(0, 0) is None
    low, high = wilson(18, 18)
    assert 0.8 < low < 0.9 and high <= 1


def test_partial_run_cannot_claim_the_hundred_question_target():
    r = report()
    r["validation"] = {"cases": 100}
    result = summarize(r)
    assert not result["coverage_complete"]
    assert not result["arms"]["candidate"]["all_targets_met"]


def test_judge_validates_schema_and_never_promotes_missing_citations():
    import importlib.util
    from types import SimpleNamespace

    spec = importlib.util.spec_from_file_location(
        "target_judge", PROJECT_ROOT / "eval/review_target.py"
    )
    reviewer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reviewer)
    row = report()["results"][0]

    class Judge:
        def invoke(self, messages):
            payload = json.loads(messages[1].content)
            assert "reference_answer" in payload and payload["answer"] == "甲"
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "answer_correct": True,
                        "citation_supported": True,
                        "abstention_correct": None,
                        "reason": "有对应原文",
                        "unsupported_claims": [],
                    }
                ),
                response_metadata={"finish_reason": "stop"},
                usage_metadata=None,
            )

    case = {**row, "reference_answer": "甲"}
    result = reviewer.review_one(case, row["arms"]["candidate"], Judge())
    assert result["answer_correct"] and not result["citation_supported"]
