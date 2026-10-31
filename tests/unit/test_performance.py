"""验证费用上限、流式用量、业务来源范围和本地追问的实际契约。"""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

from research_agent import agent, grounded_retrieval, retrieval
from research_agent.config import get_settings
from research_agent.evaluation.performance import Budget, BudgetStop, MeasuredModel
from research_agent.retrieval import Evidence


def test_budget_persists_before_request_and_blocks_unknown_or_exhausted(tmp_path):
    path = tmp_path / "ledger.json"
    budget = Budget(path, 0.5)
    call = budget.reserve([HumanMessage(content="问题")])
    assert json.loads(path.read_text(encoding="utf-8"))["calls"][0]["state"] == "reserved"
    with pytest.raises(BudgetStop):
        Budget(path, 0.5).reserve([HumanMessage(content="下一问")])
    response = SimpleNamespace(
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 10,
            "input_token_details": {"cache_read": 0},
        },
        response_metadata={"finish_reason": "stop"},
    )
    budget.settle(call, response)
    assert call["normalized_cost_cny"] == pytest.approx(0.00014)
    with pytest.raises(BudgetStop):
        budget.reserve([HumanMessage(content="下一问")])
    with pytest.raises(BudgetStop):
        Budget(tmp_path / "large.json").reserve([HumanMessage(content="字" * 40_000)])


def test_unknown_usage_keeps_reservation(tmp_path):
    budget = Budget(tmp_path / "ledger.json")
    call = budget.reserve([HumanMessage(content="问题")])
    with pytest.raises(BudgetStop):
        budget.settle(call, SimpleNamespace(usage_metadata=None, response_metadata={}))
    assert call["reserved_or_peak_cny"] == 0.5
    assert call["state"] == "unknown_usage"


def test_resuming_keeps_interrupted_charge_in_budget(tmp_path):
    budget = Budget(tmp_path / "ledger.json", 1)
    call = budget.reserve([HumanMessage(content="中断请求")])
    call["state"] = "interrupted_reserved"
    budget.reserve([HumanMessage(content="新请求")])
    assert sum(c["reserved_or_peak_cny"] for c in budget.data["calls"]) == 1


def test_adoption_rejects_quality_drop_unknown_cost_or_smoke_failure(monkeypatch):
    from copy import deepcopy

    from research_agent.evaluation import performance_decision

    scores = {
        f: {"passed": n}
        for f, n in {
            "answer_correct": 74,
            "citation_supported": 80,
            "abstention_correct": 18,
            "multi_correct": 16,
        }.items()
    }
    scores["all_targets_met"] = True
    quality = {"arms": {a: deepcopy(scores) for a in ("baseline", "candidate")}}
    monkeypatch.setattr(performance_decision, "summarize", lambda *_: quality)
    performance = {
        a: {
            "attempted": 100,
            "failed": 0,
            "truncated": 0,
            "normalized_cost_cny": 1 if a == "baseline" else 0.8,
            "total_seconds": {
                "p95_seconds": 4 if a == "baseline" else 3,
                "median_seconds": 2 if a == "baseline" else 1.5,
            },
        }
        for a in ("baseline", "candidate")
    }
    report = {"performance": performance, "code_unchanged": True, "results": []}
    smoke = {"status": "completed", "checks": {"http": True}}
    assert performance_decision.assess(report, {"reviews": []}, smoke)["adopt"]
    quality["arms"]["candidate"]["answer_correct"]["passed"] = 73
    assert not performance_decision.assess(report, {"reviews": []}, smoke)["adopt"]
    quality["arms"]["candidate"]["answer_correct"]["passed"] = 74
    performance["candidate"]["normalized_cost_cny"] = None
    assert not performance_decision.assess(report, {"reviews": []}, smoke)["adopt"]
    performance["candidate"]["normalized_cost_cny"] = 0.8
    smoke["checks"]["http"] = False
    assert not performance_decision.assess(report, {"reviews": []}, smoke)["adopt"]


def test_stream_failure_does_not_release_reserved_money(tmp_path):
    class Model:
        def stream(self, _):
            raise TimeoutError("不可记录的第三方正文")
            yield

    calls = []
    model = MeasuredModel(Model(), Budget(tmp_path / "ledger.json"), calls)
    with pytest.raises(TimeoutError):
        list(model.stream([HumanMessage(content="问题")]))
    assert calls[0]["state"] == "reserved"


def test_local_followups_do_not_call_model(monkeypatch):
    def forbidden(_):
        raise AssertionError("本地追问不得调用模型")

    monkeypatch.setattr(agent, "_model", forbidden)
    settings = replace(get_settings(), followup_mode="local")
    assert (
        len(
            agent._suggest_followups(
                "问题", "回答", [Evidence("S1", "原文", "书.md")], [], settings
            )
        )
        == 3
    )
    assert len(agent._suggest_followups("问题", "缺证", [], [], settings)) == 3


def test_business_scopes_additions_and_relabels_without_merging_versions(tmp_path, monkeypatch):
    data_dir = tmp_path / "documents"
    data_dir.mkdir()
    (data_dir / "甲志.md").write_text("基础原文", encoding="utf-8")
    settings = replace(get_settings(), data_dir=data_dir, grounded_answers=True)
    calls = []

    def search(q, s, top_k, **kwargs):
        calls.append(kwargs)
        return (
            [Evidence("N1", "共同原文", "新增/甲志.pdf")],
            [Evidence("O1", "共同原文", "甲志.md")],
        )

    monkeypatch.setattr(retrieval, "retrieve_versions", search)
    found = grounded_retrieval.retrieve_business("《甲志》记载什么", settings)
    assert len(found) == 2
    assert [e.label for e in found] == ["S1", "S2"]
    assert calls[0]["source_paths"] == ("甲志.md",)
    # 未匹配文件名的书目仍可能由其他志书著录，不能把没有同名文件当作缺证。
    assert len(grounded_retrieval.retrieve_business("《另一部旧志》由谁编", settings)) == 2
    calls.clear()
    grounded_retrieval.retrieve_business("问题", replace(settings, grounded_answers=False))
    assert calls == [{}]
