"""验证完整会话持久化、独立上下文窗口、历史迁移和分页契约。"""

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage

from research_agent import agent
from research_agent.config import Settings, get_settings
from research_agent.conversation import ConversationStore
from research_agent.retrieval import Evidence


def turns(count):
    return [{"user": f"问题{i}", "assistant": f"回答{i}"} for i in range(count)]


def test_full_history_and_recent_context_are_independent(tmp_path):
    store = ConversationStore(tmp_path)
    history = turns(45)
    store.save("long", history)
    assert store.database_path.is_file()
    assert ConversationStore(tmp_path).load("long") == history
    assert store.load_recent("long") == history[-20:]
    assert store.load_recent("long", 3) == history[-3:]
    with sqlite3.connect(store.database_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 90


def test_snapshot_cannot_erase_older_history(tmp_path):
    store = ConversationStore(tmp_path)
    history = turns(25)
    store.save("session", history)
    store.save("session", history)
    with pytest.raises(ValueError, match="不能缩短"):
        store.save("session", history[-20:])
    assert store.load("session") == history
    store.append_turn("session", "追加", "追加回答")
    assert len(store.load("session")) == 26


def test_json_migration_is_idempotent_preserves_files_and_titles(tmp_path):
    legacy = tmp_path / "legacy.json"
    original = json.dumps(turns(35), ensure_ascii=False).encode("utf-8")
    legacy.write_bytes(original)
    store = ConversationStore(tmp_path)
    assert store.load("legacy") == turns(35)
    store.append_turn("legacy", "新问题", "新回答")
    restored = ConversationStore(tmp_path)
    assert len(restored.load("legacy")) == 36
    assert legacy.read_bytes() == original
    assert restored.list_sessions()[0]["title"] == "问题0"
    assert restored.list_sessions()[0]["turn_count"] == 36


def test_deleted_legacy_session_is_not_imported_again(tmp_path):
    original = tmp_path / "legacy.json"
    original.write_text(json.dumps(turns(3)), encoding="utf-8")
    store = ConversationStore(tmp_path)
    assert store.delete("legacy")
    restored = ConversationStore(tmp_path)
    assert original.exists() and restored.load("legacy") == []
    assert restored.list_sessions() == [] and not restored.delete("legacy")
    with pytest.raises(ValueError, match="已删除"):
        restored.append_turn("legacy", "不得复活", "回答")


def test_invalid_legacy_file_is_reported_without_partial_import(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text(json.dumps([turns(1)[0], {"user": "缺少回答"}]), encoding="utf-8")
    store = ConversationStore(tmp_path)
    assert store.session_page()["migration_warnings"]
    with pytest.raises(ValueError, match="旧会话"):
        store.load("broken")
    assert path.exists()
    path.write_text(json.dumps(turns(2)), encoding="utf-8")
    assert ConversationStore(tmp_path).load("broken") == turns(2)


def test_cursor_paging_has_no_gaps_after_a_new_turn_is_appended(tmp_path):
    store = ConversationStore(tmp_path)
    history = turns(45)
    store.save("history", history)
    first = store.page("history")
    assert first["turns"] == history[-20:] and first["total_turns"] == 45
    assert first["has_more"] and first["next_before"] == 26
    store.append_turn("history", "新轮次", "新回答")
    second = store.page("history", before=first["next_before"])
    third = store.page("history", before=second["next_before"])
    assert third["turns"] + second["turns"] + first["turns"] == history
    assert not third["has_more"] and third["next_before"] is None


@pytest.mark.parametrize("limit", [0, 101, -1, True, "20"])
def test_invalid_page_and_context_sizes_are_rejected(tmp_path, limit):
    store = ConversationStore(tmp_path)
    with pytest.raises(ValueError):
        store.page("session", limit=limit)
    with pytest.raises(ValueError):
        store.load_recent("session", limit)


def test_concurrent_appends_preserve_all_pairs(tmp_path):
    def append(index):
        ConversationStore(tmp_path).append_turn("shared", f"问题{index}", f"回答{index}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append, range(24)))
    history = ConversationStore(tmp_path).load("shared")
    assert len(history) == 24
    assert {item["user"] for item in history} == {f"问题{i}" for i in range(24)}
    assert all(item["assistant"] == item["user"].replace("问题", "回答") for item in history)


def test_concurrent_migration_only_imports_once(tmp_path):
    (tmp_path / "legacy.json").write_text(json.dumps(turns(25)), encoding="utf-8")
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: ConversationStore(tmp_path), range(6)))
    assert ConversationStore(tmp_path).load("legacy") == turns(25)


def test_failed_message_write_rolls_back_the_entire_turn(tmp_path):
    store = ConversationStore(tmp_path)
    store.append_turn("safe", "已有问题", "已有回答")
    with sqlite3.connect(store.database_path) as conn:
        conn.execute(
            "CREATE TRIGGER block_answer BEFORE INSERT ON messages "
            "WHEN NEW.role='assistant' BEGIN SELECT RAISE(ABORT,'测试写入失败'); END;"
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.append_turn("safe", "失败问题", "失败回答")
    assert store.load("safe") == [{"user": "已有问题", "assistant": "已有回答"}]
    assert store.session_page()["conversations"][0]["turn_count"] == 1


def test_model_context_is_bounded_but_completed_turn_is_appended(tmp_path, monkeypatch):
    settings = Settings(
        "test",
        "test",
        "test",
        tmp_path,
        "unused",
        "old",
        100,
        10,
        2,
        conversation_dir=tmp_path / "sessions",
        chat_context_turns=3,
    )
    store = ConversationStore(settings.conversation_dir)
    history = turns(25)
    store.save("chat", history)
    monkeypatch.setattr(agent, "retrieve_library", lambda *_: [Evidence("S1", "证据", "test.md")])
    monkeypatch.setattr(agent, "_suggest_followups", lambda *_: ())
    captured = []

    class Model:
        def bind_tools(self, _tools):
            return self

        def stream(self, messages):
            captured.extend(m.content for m in messages if isinstance(m, HumanMessage))
            yield AIMessageChunk(content="基于证据的回答[S1]")

    monkeypatch.setattr(agent, "_model", lambda _: Model())
    list(agent.stream_answer_turn("当前问题", settings, "chat", store))
    assert captured == ["问题22", "问题23", "问题24", "当前问题"]
    saved = store.load("chat")
    assert len(saved) == 26 and saved[:25] == history


@pytest.mark.parametrize("value", ["0", "101", "abc", ""])
def test_invalid_context_configuration(monkeypatch, value):
    monkeypatch.setenv("CHAT_CONTEXT_TURNS", value)
    with pytest.raises(ValueError, match="CHAT_CONTEXT_TURNS"):
        get_settings()


def test_context_configuration_defaults_and_override(monkeypatch):
    monkeypatch.delenv("CHAT_CONTEXT_TURNS", raising=False)
    assert get_settings().chat_context_turns == 20
    monkeypatch.setenv("CHAT_CONTEXT_TURNS", "5")
    assert get_settings().chat_context_turns == 5


def test_http_history_and_session_pagination(tmp_path):
    from research_agent.web_server import create_handler

    settings = Settings(
        "test",
        "test",
        "test",
        tmp_path,
        "unused",
        "old",
        100,
        10,
        2,
        conversation_dir=tmp_path / "sessions",
    )
    store = ConversationStore(settings.conversation_dir)
    store.save("long", turns(45))
    store.append_turn("second", "第二个会话", "回答")
    server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(settings))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path):
        with closing(HTTPConnection("127.0.0.1", server.server_port, timeout=10)) as conn:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, json.loads(response.read())

    try:
        status, first = request("/api/conversations/long?limit=20")
        assert status == 200 and first["turns"] == turns(45)[-20:]
        status, second = request(f"/api/conversations/long?before={first['next_before']}&limit=20")
        assert status == 200 and second["turns"] == turns(45)[5:25]
        status, page = request("/api/conversations?limit=1&offset=0")
        assert status == 200 and page["has_more"] and page["conversations"][0]["id"] == "second"
        assert request("/api/conversations?limit=1&offset=1")[1]["conversations"][0]["id"] == "long"
        for suffix in ("?limit=0", "?before=-1", "?before=abc"):
            assert request("/api/conversations/long" + suffix)[0] == 400
        assert request("/api/conversations?offset=-1")[0] == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)
