"""问答代理基础功能的离线测试。"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessageChunk

from research_agent import agent, answer, rag_updates, retrieval, web_search
from research_agent.config import Settings
from research_agent.conversation import ConversationStore
from research_agent.retrieval import Evidence


def make_settings(tmp_path: Path, *, web_search_enabled: bool = True) -> Settings:
    data_dir = tmp_path / "data" / "documents"
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        api_key="test-key",
        chat_model="test-model",
        embedding_model="test-embedding",
        data_dir=data_dir,
        milvus_uri="http://localhost:19530",
        collection_name="old_collection",
        chunk_size=100,
        chunk_overlap=10,
        top_k=2,
        conversation_dir=tmp_path / "conversations",
        max_tool_calls=2,
        web_search_enabled=web_search_enabled,
        additions_collection_name="new_collection",
    )


def test_conversation_store_preserves_full_history_and_limits_context(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "sessions")
    turns = [{"user": f"问题{i}", "assistant": f"回答{i}"} for i in range(25)]
    store.save("session_1", turns)

    restored = ConversationStore(tmp_path / "sessions").load("session_1")
    assert len(restored) == 25
    assert restored[0] == turns[0]
    assert restored[-1] == turns[-1]
    assert store.load_recent("session_1") == turns[-20:]


def test_conversation_store_deletes_session(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "sessions")
    store.save("session_delete", [{"user": "问题", "assistant": "回答"}])

    assert store.delete("session_delete") is True
    assert store.load("session_delete") == []
    assert store.delete("session_delete") is False


@pytest.mark.parametrize("session_id", ["../escape", "", "含中文", "x" * 65])
def test_conversation_store_rejects_invalid_session_id(tmp_path: Path, session_id: str) -> None:
    with pytest.raises(ValueError):
        ConversationStore(tmp_path).load(session_id)


def test_tool_middleware_registers_only_enabled_tools(tmp_path: Path) -> None:
    enabled = agent.ToolExecutionMiddleware(make_settings(tmp_path))
    assert {item.name for item in enabled.tools} == {
        "search_documents",
        "list_documents",
        "list_incoming_files",
        "add_file_to_rag",
        "search_web",
    }
    disabled = agent.ToolExecutionMiddleware(
        make_settings(tmp_path / "disabled", web_search_enabled=False)
    )
    assert "search_web" not in {item.name for item in disabled.tools}


def test_middleware_rejects_unknown_tool_and_contains_tool_errors(tmp_path: Path) -> None:
    middleware = agent.ToolExecutionMiddleware(make_settings(tmp_path))
    assert "白名单" in middleware.execute("delete_everything", {})
    middleware.tool_map["broken"] = SimpleNamespace(invoke=lambda _: 1 / 0)
    result = middleware.execute("broken", {})
    assert "工具执行失败（ZeroDivisionError）" in result
    assert "不要声称操作已经完成" in result


def test_document_tool_returns_unified_library_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        agent,
        "retrieve_library",
        lambda *_: [
            Evidence("S1", "新值为 20", "new.md"),
            Evidence("S2", "旧值为 10", "old.md"),
        ],
    )
    middleware = agent.ToolExecutionMiddleware(make_settings(tmp_path))

    result = middleware.execute("search_documents", {"query": "数值"})
    assert "资料库" in result and "[S1]" in result and "新值为 20" in result
    assert "[S2]" in result and "旧值为 10" in result
    assert [item.label for item in middleware.collected_evidence] == ["S1", "S2"]
    again = middleware.execute("search_documents", {"query": "数值"})
    assert "[S3]" in again and "[S4]" in again


def test_middleware_checks_search_query_and_add_file_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    middleware = agent.ToolExecutionMiddleware(make_settings(tmp_path))
    assert "查询不能为空" in middleware.execute("search_documents", {"query": " "})
    assert "不能超过 1000" in middleware.execute("search_documents", {"query": "q" * 1001})
    assert "关键词不能为空" in middleware.execute("search_web", {"query": " "})
    monkeypatch.setattr(
        agent,
        "index_incoming_file",
        lambda *_: {"added": True, "source": "incoming/new.md", "chunks": 2},
    )
    assert "已解析并加入资料库" in middleware.execute(
        "add_file_to_rag", {"relative_path": "new.md"}
    )


def test_web_search_tool_records_distinct_source_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = web_search.WebEvidence(
        "W1",
        "官方公告",
        "https://example.gov.cn/news",
        "example.gov.cn",
        "发布机构",
        "2026-10-01",
        "2026-10-15",
        "搜索摘要",
        "适用范围",
    )
    monkeypatch.setattr(agent, "run_web_search", lambda *_args, **_kwargs: [result])
    middleware = agent.ToolExecutionMiddleware(make_settings(tmp_path))

    rendered = middleware.execute("search_web", {"query": "当前信息", "news": True})
    assert "[W1]" in rendered and "发布机构" in rendered and "网址" in rendered
    assert middleware.web_evidence[0].url == result.url


def test_versioned_answer_abstains_or_uses_both_evidence_sets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(tmp_path)
    assert "没有足够信息" in answer.answer_versioned_question("问题", [], [], settings)

    class FakeModel:
        def invoke(self, messages):
            self.messages = messages
            return SimpleNamespace(content="资料库答案 [S1]、[S2]")

    fake_model = FakeModel()
    monkeypatch.setattr(answer, "ChatOpenAI", lambda **_: fake_model)
    response = answer.answer_versioned_question(
        "问题",
        [Evidence("N1", "新数据", "new.md")],
        [Evidence("O1", "旧数据", "old.md")],
        settings,
    )
    assert "[S1]" in response and "[S2]" in response
    prompt = str(fake_model.messages)
    assert "新增资料优先" not in prompt and "旧数据" in prompt and "新数据" in prompt


def test_retrieve_versions_uses_new_and_old_collections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(retrieval, "create_embeddings", lambda _: object())
    queried = []

    def fake_retrieve(_question, _settings, collection, _embeddings, _top_k, prefix):
        queried.append((collection, prefix))
        return [Evidence(f"{prefix}1", collection, "source.md")]

    monkeypatch.setattr(retrieval, "_retrieve_with_embeddings", fake_retrieve)
    new_items, old_items = retrieval.retrieve_versions("問題", settings)
    assert queried == [("new_collection", "N"), ("old_collection", "O")]
    assert new_items[0].label == "N1"
    assert old_items[0].label == "O1"


def test_incoming_file_parser_and_listing(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    incoming = rag_updates.incoming_directory(settings)
    incoming.mkdir(parents=True)
    (incoming / "新增.md").write_text("新资料正文", encoding="utf-8")
    (incoming / "ignore.csv").write_text("忽略", encoding="utf-8")

    assert rag_updates.list_incoming_files(settings) == ["新增.md"]
    docs = rag_updates.load_file(incoming / "新增.md", incoming)
    assert docs[0].page_content == "新资料正文"


def test_add_file_to_rag_is_incremental_and_duplicate_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(tmp_path)
    incoming = rag_updates.incoming_directory(settings)
    incoming.mkdir(parents=True)
    (incoming / "new.txt").write_text("这是新增资料。" * 30, encoding="utf-8")
    created = []

    class FakeMilvus:
        def __init__(self, **kwargs):
            created.append(kwargs)

        def add_documents(self, documents, ids):
            assert documents and len(documents) == len(ids)

    monkeypatch.setattr(rag_updates, "Milvus", FakeMilvus)
    monkeypatch.setattr(rag_updates, "create_embeddings", lambda _: "fake-embedding")

    first = rag_updates.add_file_to_rag("new.txt", settings)
    second = rag_updates.add_file_to_rag("new.txt", settings)
    manifest = json.loads(rag_updates._manifest_path(settings).read_text(encoding="utf-8"))
    assert first["added"] is True
    assert second["added"] is False
    assert len(created) == 1
    assert created[0]["collection_name"] == "new_collection"
    assert created[0]["drop_old"] is False
    assert len(manifest["completed"]) == 1


def test_delete_database_removes_only_its_vectors_and_uploaded_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(tmp_path)
    target = rag_updates.create_database("目标目录", settings)
    other = rag_updates.create_database("保留目录", settings)
    path = tmp_path / "data" / ".rag_databases.json"
    entries = json.loads(path.read_text(encoding="utf-8"))
    entries[0]["completed"] = {
        "a" * 64: {
            "source": "目标目录/a.md",
            "chunks": 2,
            "collection": "new_collection",
            "id_prefix": "A" * 48,
        }
    }
    entries[1]["completed"] = {
        "b" * 64: {
            "source": "保留目录/b.md",
            "chunks": 1,
            "collection": "new_collection",
            "id_prefix": "B" * 48,
        }
    }
    path.write_text(json.dumps(entries), encoding="utf-8")
    target_uploads = rag_updates.incoming_directory(settings) / target["id"]
    other_uploads = rag_updates.incoming_directory(settings) / other["id"]
    target_uploads.mkdir(parents=True, exist_ok=True)
    other_uploads.mkdir(parents=True, exist_ok=True)
    (target_uploads / "a.md").write_text("目标文件", encoding="utf-8")
    (other_uploads / "b.md").write_text("保留文件", encoding="utf-8")
    calls = {"deleted": [], "dropped": []}

    class FakeMilvusClient:
        def __init__(self, **_kwargs):
            pass

        def has_collection(self, collection_name):
            return collection_name == "new_collection"

        def delete(self, collection_name, ids):
            calls["deleted"].append((collection_name, ids))

        def close(self):
            pass

    monkeypatch.setattr("pymilvus.MilvusClient", FakeMilvusClient)

    assert rag_updates.delete_database(target["id"], settings) is True
    assert calls["deleted"] == [
        ("new_collection", [f"{'A' * 48}-00000000", f"{'A' * 48}-00000001"])
    ]
    assert not target_uploads.exists()
    assert (other_uploads / "b.md").exists()
    remaining = json.loads(path.read_text(encoding="utf-8"))
    assert [item["id"] for item in remaining] == [other["id"]]


def test_delete_shanghai_directory_removes_source_and_index_but_keeps_other_additions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(tmp_path)
    (settings.data_dir / "地方志.md").write_text("原始资料", encoding="utf-8")
    initial_uploads = rag_updates.incoming_directory(settings) / "initial"
    initial_uploads.mkdir(parents=True)
    (initial_uploads / "补充.md").write_text("补充资料", encoding="utf-8")
    rag_updates._write_manifest(
        settings,
        {
            "completed": {
                "c" * 64: {
                    "source": "上海地方志/补充.md",
                    "chunks": 1,
                    "folder_id": "initial",
                    "collection": "new_collection",
                    "id_prefix": "C" * 48,
                },
                "d" * 64: {
                    "source": "历史补充.md",
                    "chunks": 1,
                    "folder_id": "legacy-additions",
                    "collection": "new_collection",
                    "id_prefix": "D" * 48,
                },
            },
            "pending": {},
        },
    )
    calls = {"deleted": [], "dropped": []}

    class FakeMilvusClient:
        def __init__(self, **_kwargs):
            pass

        def has_collection(self, collection_name):
            return collection_name in {"old_collection", "new_collection"}

        def delete(self, collection_name, ids):
            calls["deleted"].append((collection_name, ids))

        def drop_collection(self, collection_name):
            calls["dropped"].append(collection_name)

        def close(self):
            pass

    monkeypatch.setattr("pymilvus.MilvusClient", FakeMilvusClient)

    assert rag_updates.delete_database("initial", settings) is True
    assert calls["dropped"] == ["old_collection"]
    assert calls["deleted"] == [("new_collection", [f"{'C' * 48}-00000000"])]
    assert not settings.data_dir.exists()
    assert not initial_uploads.exists()
    remaining = rag_updates._read_manifest(settings)
    assert list(remaining["completed"]) == ["d" * 64]
    assert "initial" not in [item["id"] for item in rag_updates.list_databases(settings)]


@pytest.mark.parametrize("path", ["../outside.txt", "C:/outside.txt", "bad.csv"])
def test_add_file_to_rag_rejects_paths_outside_or_unsupported(tmp_path: Path, path: str) -> None:
    settings = make_settings(tmp_path)
    with pytest.raises((ValueError, FileNotFoundError)):
        rag_updates.add_file_to_rag(path, settings)


def test_streaming_agent_emits_tokens_suggestions_and_saves_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(tmp_path)
    store = ConversationStore(settings.conversation_dir)

    class FakeModel:
        def bind_tools(self, _tools):
            return self

        def stream(self, _messages):
            yield AIMessageChunk(content="你好")
            yield AIMessageChunk(content="，世界")

    monkeypatch.setattr(agent, "_model", lambda _: FakeModel())
    monkeypatch.setattr(
        agent,
        "retrieve_library",
        lambda *_: [Evidence("S1", "测试证据", "test.md")],
    )
    monkeypatch.setattr(
        agent, "_suggest_followups", lambda *_: ("继续了解来源？", "还有哪些数据？")
    )
    events = list(agent.stream_answer_turn("打个招呼", settings, "test", store))

    assert "".join(item.text for item in events if item.kind == "token") == "你好，世界"
    suggestions = next(item for item in events if item.kind == "suggestions")
    assert suggestions.suggestions == ("继续了解来源？", "还有哪些数据？")
    assert store.load("test")[0]["user"] == "打个招呼"
    assert "可继续追问" in store.load("test")[0]["assistant"]
    sources = next(item for item in events if item.kind == "sources")
    assert sources.library_sources == ("[S1] test.md",)
    assert sources.web_sources == ()


def install_search_transport(monkeypatch, handler):
    import httpx

    original = httpx.Client
    monkeypatch.setattr(
        web_search.httpx,
        "Client",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)),
    )


def test_deepseek_search_and_fetch(tmp_path, monkeypatch):
    import httpx

    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer test-key"
        if request.url.path == "/v1/tools/search":
            body = json.loads(request.content)
            assert body["need_content"] is True
            assert body["query"] == "测试 lang:zh"
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"title": "普通", "url": "https://example.com/x", "summary": "摘要"},
                        {
                            "title": "官方",
                            "url": "https://a.gov.cn/x",
                            "content": "官方正文",
                            "date": "2026-10-01",
                        },
                        {"title": "重复", "url": "https://a.gov.cn/x"},
                        {"url": "javascript:alert(1)"},
                    ]
                },
            )
        assert request.url.path == "/v1/tools/fetch"
        assert json.loads(request.content) == {"url": "https://example.com/x"}
        return httpx.Response(200, json={"content": "抓取正文"})

    install_search_transport(monkeypatch, handler)
    results = web_search.search_web("测试", settings=make_settings(tmp_path))
    assert [x.title for x in results] == ["官方", "普通"]
    assert "官方正文" in results[0].snippet
    assert "抓取正文" in results[1].snippet
    assert results[0].published_date == "2026-10-01"
    assert results[0].publisher == "搜索结果未注明发布机构"
    assert [x.label for x in results] == ["W1", "W2"]
    assert len(calls) == 2


def test_deepseek_news_and_fetch_failure(tmp_path, monkeypatch):
    from dataclasses import replace

    import httpx

    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/v1/tools/search":
            assert " after:" in json.loads(request.content)["query"]
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"title": "新闻", "url": "https://news.cn/a", "summary": "新闻摘要"},
                    ]
                },
            )
        return httpx.Response(404, json={"error": "不可访问"})

    install_search_transport(monkeypatch, handler)
    settings = replace(make_settings(tmp_path), base_url="https://api.deepseek.com/v1")
    results = web_search.search_web("新闻", news=True, settings=settings)
    assert "正文抓取失败" in results[0].snippet
    assert "新闻摘要" in results[0].snippet
    assert len(calls) == 2


@pytest.mark.parametrize("status", [401, 402, 429, 500])
def test_deepseek_search_http_errors(tmp_path, monkeypatch, status):
    import httpx

    install_search_transport(
        monkeypatch, lambda request: httpx.Response(status, json={"error": "不应泄露的服务端信息"})
    )
    with pytest.raises(RuntimeError, match=f"HTTP {status}") as exc:
        web_search.search_web("测试", settings=make_settings(tmp_path))
    assert "不应泄露" not in str(exc.value)


@pytest.mark.parametrize("payload", [{"results": []}, {"results": None}, {"results": [None]}])
def test_deepseek_empty_and_invalid_results(tmp_path, monkeypatch, payload):
    import httpx

    install_search_transport(monkeypatch, lambda request: httpx.Response(200, json=payload))
    if payload == {"results": []}:
        assert web_search.search_web("测试", settings=make_settings(tmp_path)) == []
    else:
        with pytest.raises(RuntimeError, match="格式无效"):
            web_search.search_web("测试", settings=make_settings(tmp_path))


def test_deepseek_rejects_invalid_query_and_missing_key(tmp_path):
    from dataclasses import replace

    settings = make_settings(tmp_path)
    for query in (" ", "字" * 128):
        with pytest.raises(ValueError):
            web_search.search_web(query, settings=settings)
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        web_search.search_web("测试", settings=replace(settings, api_key=""))


def test_deepseek_timeout_is_not_empty_result(tmp_path, monkeypatch):
    import httpx

    def handler(request):
        raise httpx.ReadTimeout("超时", request=request)

    install_search_transport(monkeypatch, handler)
    with pytest.raises(RuntimeError, match="连接失败"):
        web_search.search_web("测试", settings=make_settings(tmp_path))
