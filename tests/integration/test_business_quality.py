"""业务入口使用实际检索编排，只替换向量服务和模型边界。"""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessageChunk

from research_agent import agent, answer, cli, retrieval, workflow
from research_agent.config import get_settings
from research_agent.conversation import ConversationStore
from research_agent.evaluation import target
from research_agent.evidence_policy import GROUNDING_GUIDANCE, split_queries


@pytest.fixture
def library(tmp_path, monkeypatch):
    data_dir = tmp_path / "documents"
    data_dir.mkdir()
    for name in ("甲志.md", "乙志.md"):
        (data_dir / name).write_text(
            "只使用索引中的正文，文件只用于书目范围匹配。", encoding="utf-8"
        )
    settings = replace(
        get_settings(),
        data_dir=data_dir,
        conversation_dir=tmp_path / "conversations",
        grounded_answers=True,
    )
    queries = []

    def versions(query, settings, top_k, **options):
        queries.append((query, top_k, options))
        name = "甲志" if "甲志" in query else "乙志"
        base = retrieval.Evidence(
            "O1", f"{name}清代编者甲，现代标点者乙。", name + ".md", relevance_score=0.8
        )
        addition = retrieval.Evidence(
            "N1", f"{name}另一版本由丙标点。", "补充/" + name + ".md", relevance_score=0.9
        )
        distractor = retrieval.Evidence(
            "N2", "其他书的相似数字。", "其他志.md", relevance_score=0.99
        )
        return [addition, distractor], [base]

    monkeypatch.setattr(retrieval, "retrieve_versions", versions)
    return settings, queries


def test_library_preserves_additions_versions_and_each_subquestion(library):
    settings, queries = library
    evidence = retrieval.retrieve_library("①《甲志》由谁标点？②《乙志》由谁标点？", settings)
    assert len(evidence) == 4 and len(queries) == 2
    assert [e.source for e in evidence] == ["补充/甲志.md", "补充/乙志.md", "甲志.md", "乙志.md"]
    assert [e.label for e in evidence] == ["S1", "S2", "S3", "S4"]
    assert queries[0][2]["source_paths"] == ("甲志.md",)
    assert queries[1][2]["source_paths"] == ("乙志.md",)
    assert all(top_k == 4 and options["candidate_k"] == 12 for _, top_k, options in queries)


def test_cli_ask_uses_business_retrieval_and_prompt(library, monkeypatch, capsys):
    settings, queries = library
    messages = []

    class Model:
        def invoke(self, inputs):
            messages.extend(inputs)
            return SimpleNamespace(content="两版分别由丙[S1]、乙[S2]标点。")

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(answer, "ChatOpenAI", lambda **kwargs: Model())
    monkeypatch.setattr("sys.argv", ["research-agent", "ask", "《甲志》由谁标点？"])
    cli._main()
    assert queries and GROUNDING_GUIDANCE in messages[0].content
    assert "补充/甲志.md" in capsys.readouterr().out


def test_chat_history_uses_same_policy_without_allowing_web(library, monkeypatch, tmp_path):
    settings, queries = library
    store = ConversationStore(settings.conversation_dir)
    prompts = []

    class Model:
        def bind_tools(self, tools):
            return self

        def stream(self, messages):
            prompts.append(messages[0].content)
            yield AIMessageChunk(content="丙[S1]；乙[S2]。")

    monkeypatch.setattr(agent, "_model", lambda _: Model())
    monkeypatch.setattr(agent, "_suggest_followups", lambda *args: ())
    monkeypatch.setattr(
        agent, "run_web_search", lambda *args, **kwargs: pytest.fail("没有授权不得联网搜索")
    )
    for question in ("《甲志》由谁标点？", "刚才的现代标点者是谁？"):
        events = list(agent.stream_answer_turn(question, settings, "quality-session", store))
        assert any(e.kind == "token" for e in events)
    assert len(store.load("quality-session")) == 2
    assert "甲志" in queries[1][0]
    assert all(GROUNDING_GUIDANCE in p for p in prompts)


def test_research_node_and_generation_share_policy(library, monkeypatch):
    settings, queries = library
    service = workflow.ResearchWorkflow(settings, settings.conversation_dir.parent / "tasks")
    monkeypatch.setattr(service, "_checkpoint", lambda task: None)
    task = {
        "steps": [{"local_done": False, "evidence_labels": []}],
        "plan": {"questions": [{"question": "①《甲志》由谁标点？②《乙志》由谁标点？"}]},
        "evidence": [],
    }
    state = service._graph_step("local", {"task": task, "index": 0})
    assert len(queries) == 2 and len(state["task"]["evidence"]) == 4
    assert state["task"]["steps"][0]["local_done"]
    prompts = []

    class Model:
        def invoke(self, messages):
            prompts.append(messages[0].content)
            return SimpleNamespace(content="按版本分别作答[S1][S3]。")

    monkeypatch.setattr(workflow, "_model", lambda _: Model())
    service._generate("汇总证据", {"evidence": state["task"]["evidence"]})
    assert GROUNDING_GUIDANCE in prompts[0]


def test_historical_eval_prompt_does_not_gain_duplicate_guidance():
    assert GROUNDING_GUIDANCE not in answer.BASE_SYSTEM_PROMPT
    assert answer.SYSTEM_PROMPT == answer.BASE_SYSTEM_PROMPT + "\n" + GROUNDING_GUIDANCE
    assert target.SYSTEM_PROMPT == answer.BASE_SYSTEM_PROMPT
    assert agent.AGENT_PROMPT.count(GROUNDING_GUIDANCE) == 1
    assert workflow.GUIDANCE.count(GROUNDING_GUIDANCE) == 1
    followup = "①《甲志》谁编？②《乙志》谁编？\n后续问题：刚才第一本的标点者是谁？"
    assert split_queries(followup) == [followup]


def test_candidate_pool_does_not_widen_selected_folder_prematurely(tmp_path, monkeypatch):
    settings = replace(get_settings(), data_dir=tmp_path, top_k=4, grounded_answers=True)
    monkeypatch.setattr(retrieval, "create_embeddings", lambda _: object())
    monkeypatch.setattr(
        retrieval,
        "list_databases",
        lambda _: [
            {"id": "initial", "name": "基础库", "collection": settings.collection_name},
            {
                "id": "folder-a",
                "name": "指定目录",
                "collection": settings.additions_collection_name,
            },
        ],
    )
    calls = []

    def search(q, settings, collection, embeddings, top_k, prefix, folder_id=None, **kwargs):
        calls.append((collection, top_k, folder_id))
        if collection == settings.additions_collection_name:
            assert folder_id == "folder-a", "已有4条证据，不应仅因候选池是12就扩大目录"
            return [retrieval.Evidence(f"N{i}", f"指定目录证据{i}", "a") for i in range(4)]
        return []

    monkeypatch.setattr(retrieval, "_retrieve_with_embeddings", search)
    retrieval.retrieve_library("指定目录中的资料", settings)
    assert calls == [
        (settings.additions_collection_name, 12, "folder-a"),
        (settings.collection_name, 12, None),
    ]
