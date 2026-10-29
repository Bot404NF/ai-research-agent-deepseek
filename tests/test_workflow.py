"""研究工作流的离线状态机、恢复与 HTTP 契约测试。"""

import json
import threading
from copy import deepcopy
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from research_agent import workflow
from research_agent.config import Settings
from research_agent.retrieval import Evidence
from research_agent.web_search import WebEvidence

PLAN = {
    "scope": "限定中国市场及明确日期的资料，不混合配置。",
    "expected_output": "有来源的专题报告。",
    "questions": [
        {"question": f"子问题{i}", "search_query": f"官方资料{i}", "needs_current": i == 2}
        for i in range(3)
    ],
}


@pytest.fixture
def engine(tmp_path, monkeypatch):
    settings = Settings(
        "test-key",
        "test-model",
        "test-embed",
        tmp_path / "documents",
        "unused",
        "old",
        800,
        120,
        4,
        conversation_dir=tmp_path / "conversations",
    )
    service = workflow.ResearchWorkflow(settings)
    monkeypatch.setattr(
        workflow,
        "retrieve_versions",
        lambda *_: (
            [Evidence("N1", "新版数值20。", "new.md", 2, 0.9)],
            [Evidence("O1", "旧版数值10。", "old.md", 1, 0.8)],
        ),
    )
    monkeypatch.setattr(
        workflow,
        "search_web",
        lambda *_, **__: [
            WebEvidence(
                "W1",
                "官方公告",
                "https://example.gov.cn/a",
                "example.gov.cn",
                "示例机构",
                "2026-10-01",
                "2026-10-29",
                "最新版本数值为30。",
                "示例配置",
            )
        ],
    )

    def generate(instruction, context):
        if "判断本地证据" in instruction:
            return json.dumps({"sufficient": True, "gaps": "无"})
        if "只输出 JSON" in instruction:
            return json.dumps(PLAN, ensure_ascii=False)
        if "阶段结论" in instruction:
            labels = context["evidence"]
            return f"### 阶段结论\n不同版本存在差异[{labels[0]['label']}]。\n### 待核实\n需核对适用范围。"
        if "检查各子问题" in instruction:
            return "## 证据缺口\n适用范围待核实。\n## 来源冲突\n新版20[N1]与旧版10[O1]分别记录。"
        return "## 主要结论\n新版20[N1]。非法引用[N999]。\n## 分题分析\n各版本分别呈现。\n## 分歧与局限\n需核对适用范围。"

    monkeypatch.setattr(service, "_generate", generate)
    return service


def settle(service, task_id):
    service.worker.join(timeout=10)
    assert not service.worker.is_alive(), "后台任务未在测试期限内结束"
    return service.get(task_id)


def planned(service, allow_web=False):
    task = service.create("研究示例专题", allow_web)
    return settle(service, task["id"])


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(questions=[]),
        lambda p: p.update(scope=""),
        lambda p: p["questions"][0].update(search_query="词" * 81),
        lambda p: p["questions"][0].update(needs_current="true"),
        lambda p: p["questions"][1].update(question=p["questions"][0]["question"]),
    ],
)
def test_invalid_plans(mutation):
    plan = deepcopy(PLAN)
    mutation(plan)
    with pytest.raises(ValueError):
        workflow.validate_plan(plan)


def test_confirmation_required_and_plan_can_be_edited(engine, monkeypatch):
    monkeypatch.setattr(workflow, "retrieve_versions", lambda *_: pytest.fail("确认前不应检索"))
    task = planned(engine)
    assert task["status"] == "awaiting_confirmation"
    assert not task["steps"] and not task["evidence"]
    plan = deepcopy(PLAN)
    plan["questions"].append({"question": "第四个问题", "search_query": "第四个"})
    saved = engine.update_plan(task["id"], plan, True)
    assert len(saved["plan"]["questions"]) == 4
    assert saved["allow_web"] is True
    assert engine.get(task["id"]) == saved


def test_complete_research_citations_sources_and_locked_plan(engine):
    task = planned(engine, True)
    engine.action(task["id"], "confirm", {"allow_web": True})
    task = settle(engine, task["id"])
    assert task["status"] == "completed"
    assert len(task["steps"]) == 5
    assert all(step["status"] == "succeeded" for step in task["steps"])
    assert [item["label"] for item in task["evidence"]] == [
        "N1",
        "O1",
        "N2",
        "O2",
        "N3",
        "O3",
        "W1",
    ]
    assert "[N999]" not in task["report"] and "引用校验提示" in task["report"]
    assert "示例机构" in task["report"] and "2026-10-29" in task["report"]
    assert "第 2 页" in task["report"] and "适用范围" in task["review"]
    with pytest.raises(workflow.WorkflowConflict):
        engine.update_plan(task["id"], PLAN, False)


def test_no_permission_never_searches_and_empty_evidence_is_not_failure(engine, monkeypatch):
    monkeypatch.setattr(workflow, "retrieve_versions", lambda *_: ([], []))
    monkeypatch.setattr(workflow, "search_web", lambda *_, **__: pytest.fail("未授权不应联网"))
    task = planned(engine)
    engine.action(task["id"], "confirm")
    task = settle(engine, task["id"])
    assert task["status"] == "completed" and task["evidence"] == []
    assert "未找到" in task["steps"][0]["summary"]


def test_local_failure_pauses_and_retry_preserves_completed_steps(engine, monkeypatch):
    original = workflow.retrieve_versions
    calls = []
    fail = [True]

    def retrieve(query, settings):
        calls.append(query)
        if query == "子问题1" and fail[0]:
            raise ConnectionError("不可将上游敏感异常正文展示给用户")
        return original(query, settings)

    monkeypatch.setattr(workflow, "retrieve_versions", retrieve)
    task = planned(engine)
    engine.action(task["id"], "confirm")
    failed = settle(engine, task["id"])
    assert failed["status"] == "failed"
    assert failed["steps"][0]["status"] == "succeeded"
    assert failed["steps"][1]["status"] == "failed"
    assert "敏感" not in failed["error"]
    fail[0] = False
    engine.action(task["id"], "retry")
    done = settle(engine, task["id"])
    assert done["status"] == "completed"
    assert calls.count("子问题0") == 1 and calls.count("子问题1") == 2
    assert [item["label"] for item in done["evidence"]] == ["N1", "O1", "N2", "O2", "N3", "O3"]


def test_web_failure_keeps_local_checkpoint_and_retries_search_only(engine, monkeypatch):
    local_calls, web_calls = [], []
    original = workflow.retrieve_versions
    monkeypatch.setattr(
        workflow,
        "retrieve_versions",
        lambda query, settings: local_calls.append(query) or original(query, settings),
    )

    def failing_search(*args, **kwargs):
        web_calls.append(args[0])
        raise RuntimeError("搜索异常")

    monkeypatch.setattr(workflow, "search_web", failing_search)
    task = planned(engine, True)
    engine.action(task["id"], "confirm")
    failed = settle(engine, task["id"])
    assert failed["steps"][2]["local_done"] and not failed["steps"][2]["web_done"]
    assert failed["status"] == "failed" and len(failed["evidence"]) == 6
    monkeypatch.setattr(workflow, "search_web", lambda *_, **__: [])
    engine.action(task["id"], "retry")
    assert settle(engine, task["id"])["status"] == "completed"
    assert local_calls == ["子问题0", "子问题1", "子问题2"]


def test_summary_retry_reuses_both_local_and_web_snapshots(engine, monkeypatch):
    generate = engine._generate
    task = planned(engine, True)
    fail = [True]

    def failing_summary(instruction, context):
        if "阶段结论" in instruction and context["question"]["needs_current"] and fail[0]:
            raise TimeoutError()
        return generate(instruction, context)

    monkeypatch.setattr(engine, "_generate", failing_summary)
    engine.action(task["id"], "confirm")
    failed = settle(engine, task["id"])
    assert failed["steps"][2]["web_done"]
    snapshots = deepcopy(failed["evidence"])
    monkeypatch.setattr(workflow, "retrieve_versions", lambda *_: pytest.fail("不应重复取证"))
    monkeypatch.setattr(workflow, "search_web", lambda *_, **__: pytest.fail("不应重复联网"))
    fail[0] = False
    engine.action(task["id"], "retry")
    done = settle(engine, task["id"])
    assert done["status"] == "completed" and done["evidence"] == snapshots


def test_partial_local_coverage_can_trigger_one_authorized_search_per_question(engine, monkeypatch):
    generate, search = engine._generate, workflow.search_web
    searches = []

    def assess(instruction, context):
        if "判断本地证据" in instruction:
            return json.dumps({"sufficient": False, "gaps": "缺少问题要求的适用范围。"})
        return generate(instruction, context)

    def search_once(query, **kwargs):
        searches.append(query)
        return search(query, **kwargs)

    monkeypatch.setattr(engine, "_generate", assess)
    monkeypatch.setattr(workflow, "search_web", search_once)
    task = planned(engine, True)
    engine.action(task["id"], "confirm")
    done = settle(engine, task["id"])
    assert done["status"] == "completed"
    assert searches == ["官方资料0", "官方资料1", "官方资料2"]
    assert done["steps"][0]["local_gaps"] == "缺少问题要求的适用范围。"
    assert [item["label"] for item in done["evidence"] if item["label"].startswith("W")] == [
        "W1",
        "W2",
        "W3",
    ]


def test_invalid_confirmation_does_not_lock_plan_or_start_worker(engine):
    task = planned(engine)
    with pytest.raises(ValueError):
        engine.action(task["id"], "confirm", {"plan": {}})
    restored = engine.get(task["id"])
    assert restored["status"] == "awaiting_confirmation" and not restored["confirmed"]
    with pytest.raises(workflow.WorkflowConflict):
        engine.action(task["id"], "continue")
    assert engine.active_id is None


def test_disabled_web_configuration_and_task_permission_are_isolated(engine):
    from dataclasses import replace

    disabled = workflow.ResearchWorkflow(
        replace(engine.settings, web_search_enabled=False),
        engine.directory / "disabled",
    )
    with pytest.raises(ValueError, match="关闭联网"):
        disabled.create("研究", True)
    allowed = planned(engine, True)
    another = planned(engine)
    assert engine.get(allowed["id"])["allow_web"] is True
    assert another["allow_web"] is False


def test_restart_respects_pending_cancellation(engine):
    task = planned(engine)
    task.update(status="running", cancel_requested=True)
    task["steps"] = [{"title": "未完成", "status": "running"}]
    engine._save(task)
    restarted = workflow.ResearchWorkflow(engine.settings)
    cancelled = restarted.get(task["id"])
    assert cancelled["status"] == "cancelled" and cancelled["steps"][0]["status"] == "skipped"


def test_restart_restores_unfinished_step_without_repeating_evidence(engine, monkeypatch):
    task = planned(engine)
    engine.action(task["id"], "confirm")
    done = settle(engine, task["id"])
    done.update(status="running", report="")
    done["steps"][-1]["status"] = "running"
    engine._save(done)
    restored = workflow.ResearchWorkflow(engine.settings)
    assert restored.get(task["id"])["status"] == "waiting_continue"
    assert restored.get(task["id"])["steps"][-1]["status"] == "pending"
    monkeypatch.setattr(restored, "_generate", engine._generate)
    monkeypatch.setattr(workflow, "retrieve_versions", lambda *_: pytest.fail("恢复时不应重新检索"))
    restored.action(task["id"], "continue")
    result = settle(restored, task["id"])
    assert result["status"] == "completed" and result["evidence"] == done["evidence"]


def test_plan_failure_retry_and_permission_types(engine, monkeypatch):
    generate = engine._generate
    monkeypatch.setattr(engine, "_generate", lambda *_: "无效 JSON")
    task = planned(engine)
    assert task["status"] == "failed" and not task["confirmed"]
    monkeypatch.setattr(engine, "_generate", generate)
    engine.action(task["id"], "retry")
    assert settle(engine, task["id"])["status"] == "awaiting_confirmation"
    with pytest.raises(ValueError):
        engine.create("研究", "false")
    with pytest.raises(ValueError):
        engine.get("../escape")


def test_duplicate_execution_and_cancel_at_external_boundary(engine, monkeypatch):
    task = planned(engine)
    entered, release = threading.Event(), threading.Event()

    def retrieve(*_):
        entered.set()
        assert release.wait(10)
        return [Evidence("N1", "结果", "source.md")], []

    monkeypatch.setattr(workflow, "retrieve_versions", retrieve)
    engine.action(task["id"], "confirm")
    try:
        assert entered.wait(10)
        assert engine.get(task["id"])["status"] == "running"
        with pytest.raises(workflow.WorkflowConflict):
            engine.action(task["id"], "confirm")
        with pytest.raises(workflow.WorkflowConflict):
            engine.create("第二个任务")
        pending = engine.action(task["id"], "cancel")
        assert pending["cancel_requested"] and pending["status"] == "running"
    finally:
        release.set()
    cancelled = settle(engine, task["id"])
    assert cancelled["status"] == "cancelled"
    assert len(cancelled["evidence"]) == 1 and cancelled["steps"][0]["local_done"]
    assert all(step["status"] == "skipped" for step in cancelled["steps"])


def test_http_task_contract_static_assets_and_report(engine):
    from research_agent.web_server import create_handler

    server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(engine.settings, engine))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, path, payload=None, headers=None):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        body = json.dumps(payload).encode() if payload is not None else None
        connection.request(method, path, body, headers or {})
        response = connection.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        connection.close()
        return result

    try:
        status, body, _ = request("POST", "/api/research/tasks", {"goal": "示例研究"})
        assert status == 202
        task_id = json.loads(body)["id"]
        settle(engine, task_id)
        assert request("GET", f"/api/research/tasks/{task_id}/report")[0] == 409
        assert request("GET", "/api/research/tasks")[0] == 200
        assert request("GET", f"/api/research/tasks/{task_id}")[0] == 200
        assert request("POST", f"/api/research/tasks/{task_id}/plan", {"plan": PLAN})[0] == 200
        assert request("POST", f"/api/research/tasks/{task_id}/confirm", {})[0] == 200
        settle(engine, task_id)
        status, body, headers = request("GET", f"/api/research/tasks/{task_id}/report")
        assert status == 200 and "专题研究报告" in body.decode()
        assert headers["Content-Disposition"].endswith('.md"')
        assert request("POST", f"/api/research/tasks/{task_id}/retry", {})[0] == 409
        assert request("GET", "/api/research/tasks/invalid")[0] == 400
        assert request("GET", "/api/research/tasks/" + "0" * 32)[0] == 404
        assert (
            request(
                "POST", "/api/research/tasks", {"goal": "研究"}, {"Origin": "https://other.example"}
            )[0]
            == 403
        )
        for asset in ("/", "/workflow.js", "/workflow.css"):
            assert request("GET", asset)[0] == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)
