"""报告复验必须保留原始结果与证据，隔离写入并拒绝覆盖。"""

import importlib.util
import json
import sys

import pytest
from langchain_core.messages import AIMessage

from research_agent.config import PROJECT_ROOT


def test_report_replay_preserves_original_and_runs_only_two_nodes(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "report_replay", PROJECT_ROOT / "eval/replay_research_report.py"
    )
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    source = tmp_path / "source.json"
    source.write_bytes((PROJECT_ROOT / "eval/results/interview_demo_20261101.json").read_bytes())
    original = source.read_bytes()
    output = tmp_path / "repaired.json"
    monkeypatch.setattr(replay, "PROJECT_ROOT", tmp_path)
    code = tmp_path / "src/research_agent/workflow.py"
    code.parent.mkdir(parents=True)
    code.write_text("# 复验摘要占位\n", encoding="utf-8")
    prompts = []

    class Model:
        def __init__(self, calls):
            self.calls = calls

        def invoke(self, messages):
            prompts.append(messages)
            self.calls.append({"finish_reason": "stop"})
            return AIMessage(content="不同整理本分别记录，日期见元数据[S1]。")

    monkeypatch.setattr(replay, "model_factory", lambda settings, budget, calls: Model(calls))
    monkeypatch.setattr(sys, "argv", ["replay", "--source", str(source), "--output", str(output)])
    replay.main()
    assert not output.exists()
    monkeypatch.setattr(sys, "argv", sys.argv + ["--run"])
    replay.main()
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "completed" and all(result["checks"].values())
    assert len(prompts) == 2
    assert result["task"]["id"] != result["original_task_id"]
    assert source.read_bytes() == original
    assert result["task"]["evidence"] == json.loads(original)["task"]["evidence"]
    assert json.loads(prompts[0][1].content)["review"] == ""
    assert json.loads(prompts[1][1].content)["review"] == "不同整理本分别记录，日期见元数据[S1]。"
    with pytest.raises(SystemExit):
        replay.main()
