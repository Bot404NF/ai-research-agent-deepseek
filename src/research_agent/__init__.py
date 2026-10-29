"""AI 研究助手软件包的公开接口。"""

from research_agent.agent import AgentEvent, answer_turn, stream_answer_turn
from research_agent.answer import answer_question, answer_versioned_question, render_context
from research_agent.config import Settings, get_settings
from research_agent.conversation import ConversationStore
from research_agent.ingest import build_index, load_documents
from research_agent.rag_updates import add_file_to_rag, list_incoming_files
from research_agent.retrieval import (
    Evidence,
    evidence_from_documents,
    retrieve,
    retrieve_library,
    retrieve_versions,
)
from research_agent.web_search import WebEvidence, search_web

__all__ = [
    "AgentEvent",
    "ConversationStore",
    "Evidence",
    "Settings",
    "WebEvidence",
    "add_file_to_rag",
    "answer_question",
    "answer_turn",
    "answer_versioned_question",
    "build_index",
    "evidence_from_documents",
    "get_settings",
    "list_incoming_files",
    "load_documents",
    "render_context",
    "retrieve",
    "retrieve_library",
    "retrieve_versions",
    "search_web",
    "stream_answer_turn",
]
