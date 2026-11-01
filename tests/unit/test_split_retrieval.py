"""对照实验不能通过答案泄漏、证据超额或忽略失败提高成绩。"""

import pytest

from research_agent.evaluation.split_retrieval import (
    build_reviews,
    merge_round_robin,
    run_pair,
    split_queries,
    summarize_pairs,
    summarize_reviews,
)
from research_agent.retrieval import Evidence


@pytest.mark.parametrize("question", ["普通问题？", "只有①一题", "①甲②", "②甲①乙", "①甲③乙"])
def test_invalid_or_single_question_keeps_original(question):
    assert split_queries(question) == [question]


def test_split_reads_only_question_and_preserves_each_scope():
    assert split_queries("请分别回答：①《甲志》何年？ ②《乙志》谁编？") == [
        "《甲志》何年？",
        "《乙志》谁编？",
    ]


def test_round_robin_budget_duplicates_and_versions():
    a = Evidence("O8", "甲 乙", "book/a.md", page=1)
    duplicate = Evidence("N9", "甲乙", "book\\a.md", page=1)
    other_version = Evidence("N3", "甲乙", "book/b.md", page=1)
    result = merge_round_robin([[a, duplicate, a], [duplicate, other_version]])
    assert [(e.label, e.source) for e in result] == [("S1", "book/a.md"), ("S2", "book/b.md")]
    groups = [[Evidence("x", str(i), "a") for i in range(4)], [Evidence("x", "乙", "b")]]
    assert [e.text for e in merge_round_robin(groups)] == ["0", "乙", "1", "2"]


def make_case():
    return {
        "id": "T",
        "question": "①问甲？②问乙？",
        "stratum": "multi_evidence",
        "should_abstain": False,
        "gold_groups": [
            {"source": "a", "anchors": ["甲"]},
            {"source": "b", "anchors": ["乙"]},
        ],
    }


def test_pair_improvement_and_repeats_keep_fixed_budget():
    calls = []

    def retrieve(question, settings, top_k):
        calls.append((question, top_k))
        return [Evidence("S1", "乙", "b")] if question == "问乙？" else [Evidence("S1", "甲", "a")]

    row = run_pair(make_case(), None, 3, retrieve)
    assert len(calls) == 9 and {k for _, k in calls} == {4}
    summary = summarize_pairs([row])
    assert summary["whole"]["anchor_hit_at_4"] == 0
    assert summary["split"]["anchor_hit_at_4"] == 1
    assert summary["improved_ids"] == ["T"]
    assert summary["whole"]["generation_attempted_cases"] == 0


def test_subquery_failure_excluded_from_both_paired_denominators():
    def retrieve(question, settings, top_k):
        if question == "问乙？":
            raise RuntimeError("不允许进入结果的第三方异常正文")
        return [Evidence("S1", "甲", "a")]

    row = run_pair(make_case(), None, 2, retrieve)
    summary = summarize_pairs([row])
    assert row["arms"]["split"]["status"] == "error"
    assert "不允许" not in str(row)
    assert summary["paired_positive"] == 0
    assert summary["whole"]["anchor_hit_at_4"] is None
    assert summary["split"]["retrieval_failed"] == 1


def test_generation_failure_preserves_retrieval_but_not_success():
    def generate(question, evidence, settings):
        raise ValueError("不记录异常正文")

    row = run_pair(
        make_case(),
        None,
        1,
        lambda *args, **kwargs: [Evidence("S1", "甲", "a")],
        generate_answers=True,
        generate_fn=generate,
    )
    summary = summarize_pairs([row])
    assert summary["paired_positive"] == 1
    assert summary["whole"]["generation_failed"] == 1
    assert summary["whole"]["usage_observed_calls"] == 0
    assert summary["whole"]["estimated_cost"] is None


def test_negative_not_counted_as_correct_and_unchanged_not_generated():
    case = {**make_case(), "question": "没有编号的负题", "should_abstain": True, "gold_groups": []}
    row = run_pair(case, None, 1, lambda *args, **kwargs: [], generate_answers=True)
    summary = summarize_pairs([row])
    assert summary["positive_cases"] == 0
    assert summary["whole"]["anchor_hit_at_4"] is None
    assert summary["split"]["generation_attempted_cases"] == 0


def test_bound_reviews_do_not_invent_human_scores_or_ignore_mismatch():
    row = run_pair(make_case(), None, 1, lambda *a, **kw: [Evidence("S1", "甲", "a")])
    report = {"dataset_sha256": "fixed", "results": [row]}
    reviews = build_reviews(report)
    reviews["reviewer"] = {"kind": "ai"}
    reviews["reviews"][0].update(hit_at_4=False, reason="第二部分无证据")
    result = summarize_reviews(report, reviews)
    assert result["reviewer"]["kind"] == "ai"
    assert result["by_stratum"]["multi_evidence"]["whole"]["answer_correct"]["eligible"] == 0
    reviews["reviews"][0]["answer_correct"] = True
    with pytest.raises(ValueError, match="未生成"):
        summarize_reviews(report, reviews)
    reviews["results_sha256"] = "wrong"
    with pytest.raises(ValueError, match="未绑定"):
        summarize_reviews(report, reviews)


def test_week2_preserves_frozen_week1_cases():
    import json

    from research_agent.config import PROJECT_ROOT
    from research_agent.evaluation.dataset_hash import matches_text_digest

    root = PROJECT_ROOT / "eval/datasets"
    first = json.loads((root / "week1.json").read_text(encoding="utf-8"))
    second = json.loads((root / "week2.json").read_text(encoding="utf-8"))
    assert second["cases"][:-1] == first["cases"]
    assert matches_text_digest(root / "week1.json", second["parent_dataset_sha256"])
    assert second["cases"][-1]["stratum"] == "authored_probe"
