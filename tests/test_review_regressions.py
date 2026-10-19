"""模型复用、原子写入和目录检索的离线回归检查。"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_agent import embeddings, rag_updates, retrieval
from research_agent.config import Settings
from research_agent.storage import write_json_atomic
from research_agent.agent import contextual_retrieval_query


def test_followup_retrieval_keeps_previous_subject_without_affecting_new_questions():
    turns = [{"user": "《安亭志》的现代标点者是谁？", "assistant": "朱瑞熙[O2]"}]
    query = contextual_retrieval_query("刚才回答的现代标点者叫什么名字？", turns)
    assert "安亭志" in query and "刚才" in query
    assert contextual_retrieval_query("《南翔镇志》由谁编纂？", turns) == "《南翔镇志》由谁编纂？"
    assert contextual_retrieval_query("刚才是谁？", []) == "刚才是谁？"
    assert len(contextual_retrieval_query("刚才是谁？", [{"user": "甲" * 2000}])) <= 1000


def test_followup_stream_removes_historical_labels_and_retrieves_current_evidence(tmp_path, monkeypatch):
    from langchain_core.messages import AIMessageChunk, AIMessage
    from research_agent import agent
    from research_agent.conversation import ConversationStore

    settings = Settings("", "chat", "embed", tmp_path, "unused", "old", 800, 120, 4)
    store = ConversationStore(tmp_path / "history")
    store.save("followup", [{"user": "安亭志的标点者是谁？", "assistant": "朱瑞熙[O99]"}])
    queries = []

    def search(query, _settings):
        queries.append(query)
        return [], [retrieval.Evidence("O1", "《安亭志》由朱瑞熙标点。", "安亭志.md")]

    class Model:
        def bind_tools(self, _):
            return self

        def stream(self, messages):
            old_answers = [m for m in messages if isinstance(m, AIMessage) and m.content]
            assert old_answers and "[O99]" not in old_answers[0].content
            yield AIMessageChunk(content="朱瑞熙[O1]")

    monkeypatch.setattr(agent, "retrieve_versions", search)
    monkeypatch.setattr(agent, "_model", lambda _: Model())
    monkeypatch.setattr(agent, "_suggest_followups", lambda *_: ())
    events = list(agent.stream_answer_turn("刚才的标点者叫什么？", settings, "followup", store))
    assert "安亭志" in queries[0]
    assert any(e.kind == "token" and "[O1]" in e.text for e in events)
    assert len(store.load("followup")) == 2


def test_concurrent_embedding_initialization_and_device_change(monkeypatch):
    created = []

    def factory(**kwargs):
        instance = SimpleNamespace(**kwargs)
        created.append(instance)
        return instance

    monkeypatch.setattr(embeddings, "HuggingFaceEmbeddings", factory)
    monkeypatch.setattr(embeddings.torch.cuda, "is_available", lambda: True)
    monkeypatch.setenv("BGE_M3_DEVICE", "auto")
    embeddings._cached_embeddings.cache_clear()
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(embeddings.create_embeddings, ["test-model"] * 16))
        assert len(created) == 1
        assert all(item is results[0] for item in results)
        assert results[0].model_kwargs == {"device": "cuda"}
        monkeypatch.setenv("BGE_M3_DEVICE", "cpu")
        assert embeddings.create_embeddings("test-model").model_kwargs == {"device": "cpu"}
        assert len(created) == 2
    finally:
        embeddings._cached_embeddings.cache_clear()


def test_failed_atomic_replace_preserves_previous_json(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    write_json_atomic(path, {"value": "原值"})

    def fail_replace(*_):
        raise OSError("模拟替换失败")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError):
        write_json_atomic(path, {"value": "新值"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"value": "原值"}
    assert list(tmp_path.glob("*.tmp")) == []


def test_concurrent_catalog_updates_preserve_all_directories(tmp_path):
    settings = Settings("", "chat", "embed", tmp_path / "documents", "unused", "old", 100, 10, 2)
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda index: rag_updates.create_database(f"目录{index}", settings), range(16)))
    assert len(rag_updates.list_databases(settings)) == 17


def test_retrieval_keeps_first_directory_when_initial_is_deleted(tmp_path, monkeypatch):
    settings = Settings("", "chat", "embed", tmp_path, "unused", "old", 100, 10, 2)
    monkeypatch.setattr(retrieval, "create_embeddings", lambda _: object())
    monkeypatch.setattr(retrieval, "list_databases", lambda _: [
        {"id": "folder-a", "name": "甲库", "collection": "collection_a"},
        {"id": "folder-b", "name": "乙库", "collection": "collection_b"},
    ])

    def retrieve_fake(_question, _settings, collection, *_args):
        return [retrieval.Evidence("N1", collection, collection)]

    monkeypatch.setattr(retrieval, "_retrieve_with_embeddings", retrieve_fake)
    new, _old = retrieval.retrieve_versions("测试问题", settings)
    assert "collection_a" in {item.source for item in new}
    assert "collection_b" in {item.source for item in new}
    assert len({item.label for item in new}) == len(new)


def test_shared_collection_statistics_are_requested_once(tmp_path, monkeypatch):
    settings = Settings("", "chat", "embed", tmp_path / "documents", "unused", "old", 100, 10, 2)
    rag_updates.create_database("目录甲", settings)
    rag_updates.create_database("目录乙", settings)
    counts = []

    class FakeClient:
        def __init__(self, **_):
            pass

        def list_collections(self):
            return [settings.additions_collection_name]

        def get_collection_stats(self, collection_name):
            counts.append(collection_name)
            return {"row_count": 12}

        def close(self):
            pass

    monkeypatch.setattr("pymilvus.MilvusClient", FakeClient)
    catalog = rag_updates.database_details(settings)
    assert counts == [settings.additions_collection_name]
    assert [item["chunks"] for item in catalog if item["id"] != "initial"] == [12, 12]
