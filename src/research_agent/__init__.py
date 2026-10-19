"""AI 研究助手软件包的公开接口。"""

from research_agent.agent import AgentEvent, answer_turn, stream_answer_turn
from research_agent.answer import answer_question, answer_versioned_question, render_context
from research_agent.config import Settings, get_settings
from research_agent.conversation import ConversationStore
from research_agent.ingest import build_index, load_documents
from research_agent.rag_updates import add_file_to_rag, list_incoming_files
from research_agent.retrieval import Evidence, evidence_from_documents, retrieve, retrieve_versions
from research_agent.web_search import WebEvidence, search_web

__all__ = [
    "Evidence",
    "ConversationStore",
    "AgentEvent",
    "Settings",
    "WebEvidence",
    "answer_question",
    "answer_versioned_question",
    "add_file_to_rag",
    "answer_turn",
    "stream_answer_turn",
    "build_index",
    "evidence_from_documents",
    "get_settings",
    "load_documents",
    "list_incoming_files",
    "render_context",
    "retrieve",
    "retrieve_versions",
    "search_web",
]
