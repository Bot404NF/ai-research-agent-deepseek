"""直接检查真实 LangGraph checkpoint；模型、检索与搜索仍使用离线替身。"""

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from test_workflow import PLAN, planned, settle
from test_workflow import engine as engine  # noqa: PLC0414 - 显式导出 pytest fixture，避免参数遮蔽导入误报

from research_agent import workflow
from research_agent.research_graph import build_research_graph


def snapshot(service, task_id):
    with SqliteSaver.from_conn_string(str(service.checkpoint_path)) as saver:
        return build_research_graph(service, saver).get_state(
            {"configurable": {"thread_id": task_id}}
        )


def test_plan_is_a_native_interrupt_and_survives_new_service(engine, monkeypatch):
    task = planned(engine)
    saved = snapshot(engine, task["id"])
    assert saved.next == ("approval",)
    assert saved.tasks[0].interrupts[0].value["kind"] == "plan_confirmation"
    assert saved.tasks[0].error is None
    assert task["engine"] == "langgraph" and engine.checkpoint_path.is_file()
    replacement = workflow.ResearchWorkflow(engine.settings)
    monkeypatch.setattr(replacement, "_generate", engine._generate)
    edited = deepcopy(PLAN)
    edited["scope"] = "从 SQLite 中断恢复后采用用户修改的范围。"
    replacement.update_plan(task["id"], edited)
    replacement.action(task["id"], "confirm")
    done = settle(replacement, task["id"])
    assert done["status"] == "completed" and "用户修改的范围" in done["report"]
    assert snapshot(replacement, task["id"]).next == ()


@pytest.mark.parametrize("allowed", [True, False])
def test_web_interrupt_resumes_after_restart_without_retrieval_replay(engine, monkeypatch, allowed):
    task = engine.create("重启后回答联网询问", ask_web_consent=True)
    settle(engine, task["id"])
    engine.action(task["id"], "confirm")
    waiting = settle(engine, task["id"])
    assert waiting["status"] == "waiting_web_consent"
    saved = snapshot(engine, task["id"])
    assert saved.next == ("web_consent",)
    assert saved.tasks[0].interrupts[0].value["kind"] == "web_consent"
    replacement = workflow.ResearchWorkflow(engine.settings)
    monkeypatch.setattr(replacement, "_generate", engine._generate)
    monkeypatch.setattr(
        workflow, "retrieve_library", lambda *_: pytest.fail("中断恢复不应重新检索")
    )
    replacement.action(task["id"], "web-consent", {"allow_web": allowed})
    done = settle(replacement, task["id"])
    assert done["status"] == "completed" and done["allow_web"] is allowed
    assert done["evidence"][:6] == waiting["evidence"]


def test_failed_node_cursor_is_preserved_and_raw_error_is_not_in_checkpoint(engine, monkeypatch):
    task = planned(engine)
    generate = engine._generate

    def fail_summary(instruction, context):
        if "阶段结论" in instruction:
            raise RuntimeError("模拟上游敏感响应，不得进入 checkpoint")
        return generate(instruction, context)

    monkeypatch.setattr(engine, "_generate", fail_summary)
    engine.action(task["id"], "confirm")
    failed = settle(engine, task["id"])
    saved = snapshot(engine, task["id"])
    assert failed["status"] == "failed" and saved.next == ("summarize",)
    assert saved.values["task"]["steps"][0]["local_done"]
    assert "敏感响应" not in saved.tasks[0].error
    assert "RuntimeError" in saved.tasks[0].error


def test_legacy_json_without_graph_checkpoint_resumes_without_reindex(engine, monkeypatch):
    task = planned(engine)
    engine.action(task["id"], "confirm")
    legacy = settle(engine, task["id"])
    legacy_id = "a" * 32
    legacy.update(id=legacy_id, status="running", phase="report", report="")
    legacy.pop("engine", None)
    legacy["steps"][-1]["status"] = "running"
    engine._save(legacy)
    replacement = workflow.ResearchWorkflow(engine.settings)
    monkeypatch.setattr(replacement, "_generate", engine._generate)
    monkeypatch.setattr(workflow, "retrieve_library", lambda *_: pytest.fail("不得重建旧任务证据"))
    replacement.action(legacy_id, "continue")
    done = settle(replacement, legacy_id)
    assert done["status"] == "completed" and done["evidence"] == legacy["evidence"]
    assert snapshot(replacement, legacy_id).values["task"]["engine"] == "langgraph"


def test_checkpoint_threads_are_isolated(engine):
    first, second = planned(engine), planned(engine)
    engine.action(first["id"], "confirm")
    assert settle(engine, first["id"])["status"] == "completed"
    assert snapshot(engine, second["id"]).next == ("approval",)
    assert engine.get(second["id"])["status"] == "awaiting_confirmation"


def test_five_question_graph_stays_within_the_configured_step_limit(engine):
    task = planned(engine)
    plan = deepcopy(PLAN)
    for index in (3, 4):
        plan["questions"].append({"question": f"额外问题{index}", "search_query": f"关键词{index}"})
    engine.action(task["id"], "confirm", {"plan": plan})
    done = settle(engine, task["id"])
    assert done["status"] == "completed" and len(done["steps"]) == 7
    assert len(done["evidence"]) == 10


@pytest.mark.parametrize("action,expected", [("approve", True), ("reject", False)])
def test_learning_example_runs_in_separate_processes(tmp_path, action, expected):
    script = Path(__file__).resolve().parents[2] / "examples" / "learn_langgraph.py"

    def run(command):
        result = subprocess.run(
            [sys.executable, str(script), command, "--directory", str(tmp_path)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={
                **os.environ,
                "PYTHONIOENCODING": "utf-8",
            },
        )
        return json.loads(result.stdout)

    started = run("start")
    assert started["next"] == ["approve"] and started["interrupts"]
    assert run("inspect")["state"] == started["state"]
    completed = run(action)
    assert completed["next"] == [] and completed["interrupts"] == []
    assert completed["state"]["approved"] is expected


def test_approval_replay_does_not_overwrite_persisted_user_decision(engine, monkeypatch):
    task = planned(engine)
    original_interrupt = workflow.interrupt
    edited = deepcopy(PLAN)
    edited["scope"] = "人工修改的范围必须在恢复期间一直保留。"

    def checked_interrupt(payload):
        if payload["kind"] == "plan_confirmation":
            disk = engine.get(task["id"])
            assert disk["confirmed"] and disk["plan"]["scope"] == edited["scope"]
            assert disk["status"] == "running"
        return original_interrupt(payload)

    monkeypatch.setattr(workflow, "interrupt", checked_interrupt)
    engine.action(task["id"], "confirm", {"plan": edited})
    assert settle(engine, task["id"])["status"] == "completed"


@pytest.mark.parametrize("allowed", [True, False])
def test_consent_replay_keeps_the_saved_answer_until_command_is_consumed(
    engine, monkeypatch, allowed
):
    task = engine.create("恢复许可决定", ask_web_consent=True)
    settle(engine, task["id"])
    engine.action(task["id"], "confirm")
    assert settle(engine, task["id"])["status"] == "waiting_web_consent"
    original_interrupt = workflow.interrupt

    def checked_interrupt(payload):
        if payload["kind"] == "web_consent":
            disk = engine.get(task["id"])
            assert disk["web_consent"] == ("allowed" if allowed else "declined")
            assert disk["allow_web"] is allowed and disk["status"] == "running"
        return original_interrupt(payload)

    monkeypatch.setattr(workflow, "interrupt", checked_interrupt)
    engine.action(task["id"], "web-consent", {"allow_web": allowed})
    assert settle(engine, task["id"])["status"] == "completed"
