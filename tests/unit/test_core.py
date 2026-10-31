from pathlib import Path

import pytest
from langchain_core.documents import Document

from research_agent.answer import answer_question, render_context
from research_agent.config import Settings, get_settings
from research_agent.ingest import load_documents
from research_agent.retrieval import evidence_from_documents


def settings(tmp_path: Path) -> Settings:
    return Settings(
        "", "chat", "embed", tmp_path / "docs", "http://localhost:19530", "test", 100, 10, 2
    )


def test_loads_markdown_and_text_with_relative_sources(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "one.md").write_text("甲项证据", encoding="utf-8")
    (docs / "ignore.csv").write_text("忽略的内容", encoding="utf-8")
    loaded = load_documents(docs)
    assert len(loaded) == 1
    assert loaded[0].metadata["source"] == "one.md"


def test_missing_document_directory_is_clear(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_documents(tmp_path / "missing")


def test_evidence_labels_and_pdf_page_source() -> None:
    found = evidence_from_documents(
        [
            Document(page_content="第一项", metadata={"source": "a.pdf", "page": 3}),
            Document(page_content="第二项", metadata={"source": "b.md"}),
        ]
    )
    assert [item.label for item in found] == ["S1", "S2"]
    assert found[0].display_source == "a.pdf，第 3 页"


def test_context_includes_citation_labels_and_sources() -> None:
    found = evidence_from_documents(
        [Document(page_content="证据文本", metadata={"source": "notes.md"})]
    )
    context = render_context(found)
    assert "[S1] 来源：notes.md" in context
    assert "证据文本" in context


def test_abstains_without_calling_model() -> None:
    response = answer_question("未知问题？", [], settings(Path(".")))
    assert "没有足够信息" in response


def test_invalid_chunk_configuration_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHUNK_SIZE", "100")
    monkeypatch.setenv("CHUNK_OVERLAP", "100")
    with pytest.raises(ValueError, match="CHUNK_SIZE"):
        get_settings()


def test_default_milvus_port_matches_compose(monkeypatch):
    monkeypatch.delenv("MILVUS_URI", raising=False)
    assert get_settings().milvus_uri == "http://localhost:19531"


def test_default_chat_model_and_environment_override(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_CHAT_MODEL", raising=False)
    assert get_settings().chat_model == "deepseek-flash"
    monkeypatch.setenv("DEEPSEEK_CHAT_MODEL", "custom-model")
    assert get_settings().chat_model == "custom-model"
