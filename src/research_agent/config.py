import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    api_key: str = field(repr=False)
    chat_model: str
    embedding_model: str
    data_dir: Path
    milvus_uri: str = field(repr=False)
    collection_name: str
    chunk_size: int
    chunk_overlap: int
    top_k: int
    conversation_dir: Path = Path("data/conversations")
    max_tool_calls: int = 6
    web_search_enabled: bool = True
    web_search_max_results: int = 5
    additions_collection_name: str = "research_docs_additions"
    base_url: str = field(default="https://api.deepseek.com", repr=False)
    rag_relevance_threshold: float = 0.55
    chat_context_turns: int = 20
    grounded_answers: bool = False
    followup_mode: str = "model"
    telemetry_enabled: bool = True


def get_settings() -> Settings:
    followup_mode = os.getenv("FOLLOWUP_MODE", "model")
    if followup_mode not in {"local", "model"}:
        raise ValueError("FOLLOWUP_MODE 必须为 local 或 model。")
    data_dir = Path(os.getenv("DATA_DIR", "data/documents"))
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir
    size = int(os.getenv("CHUNK_SIZE", "800"))
    overlap = int(os.getenv("CHUNK_OVERLAP", "120"))
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("配置必须满足 CHUNK_SIZE > CHUNK_OVERLAP >= 0。")
    relevance_threshold = float(os.getenv("RAG_RELEVANCE_THRESHOLD", "0.55"))
    if not 0.0 <= relevance_threshold <= 1.0:
        raise ValueError("RAG_RELEVANCE_THRESHOLD 必须在 0 到 1 之间。")
    conversation_dir = Path(os.getenv("CONVERSATION_DIR", "data/conversations"))
    try:
        chat_context_turns = int(os.getenv("CHAT_CONTEXT_TURNS", "20"))
    except ValueError:
        raise ValueError("CHAT_CONTEXT_TURNS 必须是 1 至 100 的整数。") from None
    if not 1 <= chat_context_turns <= 100:
        raise ValueError("CHAT_CONTEXT_TURNS 必须是 1 至 100 的整数。")
    if not conversation_dir.is_absolute():
        conversation_dir = PROJECT_ROOT / conversation_dir
    collection_name = os.getenv("COLLECTION_NAME", "research_docs")
    additions_collection_name = os.getenv(
        "ADDITIONS_COLLECTION_NAME", f"{collection_name}_additions"
    )
    if additions_collection_name == collection_name:
        raise ValueError("ADDITIONS_COLLECTION_NAME 必须与 COLLECTION_NAME 不同。")
    return Settings(
        api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        chat_model=os.getenv("DEEPSEEK_CHAT_MODEL", "deepseek-flash"),
        embedding_model=os.getenv("BGE_M3_MODEL", "BAAI/bge-m3"),
        data_dir=data_dir,
        milvus_uri=os.getenv("MILVUS_URI", "http://localhost:19531"),
        collection_name=collection_name,
        chunk_size=size,
        chunk_overlap=overlap,
        top_k=int(os.getenv("TOP_K", "4")),
        conversation_dir=conversation_dir,
        chat_context_turns=chat_context_turns,
        max_tool_calls=int(os.getenv("MAX_TOOL_CALLS", "6")),
        web_search_enabled=os.getenv("WEB_SEARCH_ENABLED", "true").lower()
        in {"1", "true", "yes", "on"},
        web_search_max_results=max(1, min(int(os.getenv("WEB_SEARCH_MAX_RESULTS", "5")), 10)),
        additions_collection_name=additions_collection_name,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        grounded_answers=os.getenv("GROUNDED_ANSWERS", "false").lower() in {"1", "true", "yes"},
        followup_mode=followup_mode,
        telemetry_enabled=os.getenv("TELEMETRY_ENABLED", "true").lower() in {"1", "true", "yes"},
        rag_relevance_threshold=relevance_threshold,
    )
