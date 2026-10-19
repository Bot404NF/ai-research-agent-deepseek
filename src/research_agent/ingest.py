from pathlib import Path

from langchain_core.documents import Document
from langchain_milvus import Milvus
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

from research_agent.config import Settings
from research_agent.embeddings import create_embeddings


SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf"}
INGEST_BATCH_SIZE = 128


def load_documents(directory: Path) -> list[Document]:
    """读取支持的资料，并保留文件路径和 PDF 页码元数据。"""
    if not directory.exists():
        raise FileNotFoundError(f"资料目录不存在：{directory}")
    documents: list[Document] = []
    for path in sorted(p for p in directory.rglob("*") if p.suffix.lower() in SUPPORTED_SUFFIXES):
        documents.extend(load_file(path, directory))
    return documents


def load_file(path: Path, source_root: Path) -> list[Document]:
    """解析单个 TXT、Markdown 或 PDF 文件，记录相对路径和 PDF 页码。"""
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(f"不支持的文件类型：{path.suffix}；支持 .txt、.md 和 .pdf。")
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在：{path}")
    relative = path.relative_to(source_root).as_posix()
    if path.suffix.lower() == ".pdf":
        documents = []
        for page_number, page in enumerate(PdfReader(path).pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                documents.append(Document(
                    page_content=text,
                    metadata={"source": relative, "page": page_number},
                ))
        return documents
    text = path.read_text(encoding="utf-8")
    return [Document(page_content=text, metadata={"source": relative})] if text.strip() else []


def build_index(settings: Settings) -> int:
    """重建 Milvus 集合并导入文本块，返回文本块数量。"""
    source_docs = load_documents(settings.data_dir)
    if not source_docs:
        raise ValueError(f"目录中没有找到可读取的 .txt、.md 或 .pdf 文件：{settings.data_dir}")
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        add_start_index=True,
    )
    chunks = splitter.split_documents(source_docs)
    embeddings = create_embeddings(settings.embedding_model)
    store = Milvus(
        embedding_function=embeddings,
        collection_name=settings.collection_name,
        connection_args={"uri": settings.milvus_uri},
        drop_old=True,
    )
    for start in range(0, len(chunks), INGEST_BATCH_SIZE):
        batch = chunks[start : start + INGEST_BATCH_SIZE]
        store.add_documents(batch)
        completed = min(start + len(batch), len(chunks))
        print(f"正在写入文本块：{completed}/{len(chunks)}", flush=True)
    return len(chunks)
