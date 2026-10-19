from dataclasses import dataclass, replace
import re

from langchain_core.documents import Document
from langchain_milvus import Milvus
from research_agent.config import Settings
from research_agent.embeddings import create_embeddings
from research_agent.rag_updates import list_databases


@dataclass(frozen=True)
class Evidence:
    label: str
    text: str
    source: str
    page: int | None = None
    relevance_score: float | None = None

    @property
    def display_source(self) -> str:
        return f"{self.source}，第 {self.page} 页" if self.page else self.source

    @property
    def display_reference(self) -> str:
        """统一来源展示；没有相关性分数时保留来源本身。"""
        if self.relevance_score is None:
            return self.display_source
        return f"{self.display_source}（相关性 {self.relevance_score:.2f}）"


def evidence_from_documents(
    documents: list[Document],
    label_prefix: str = "S",
    relevance_scores: list[float] | None = None,
) -> list[Evidence]:
    evidence = []
    for index, document in enumerate(documents, start=1):
        evidence.append(Evidence(
            label=f"{label_prefix}{index}",
            text=document.page_content,
            source=str(document.metadata.get("source", "未知来源")),
            page=document.metadata.get("page"),
            relevance_score=(relevance_scores[index - 1] if relevance_scores else None),
        ))
    return evidence


def retrieve(question: str, settings: Settings, top_k: int | None = None) -> list[Evidence]:
    return retrieve_collection(
        question,
        settings,
        settings.collection_name,
        top_k=top_k,
        label_prefix="S",
    )


def retrieve_collection(
    question: str,
    settings: Settings,
    collection_name: str,
    top_k: int | None = None,
    label_prefix: str = "S",
) -> list[Evidence]:
    embeddings = create_embeddings(settings.embedding_model)
    return _retrieve_with_embeddings(
        question,
        settings,
        collection_name,
        embeddings,
        top_k,
        label_prefix,
    )


def _retrieve_with_embeddings(
    question: str,
    settings: Settings,
    collection_name: str,
    embeddings,
    top_k: int | None,
    label_prefix: str,
    folder_id: str | None = None,
) -> list[Evidence]:
    store = Milvus(
        embedding_function=embeddings,
        collection_name=collection_name,
        connection_args={"uri": settings.milvus_uri},
    )
    if not store.client.has_collection(collection_name):
        return []
    indexes = [store.client.describe_index(collection_name, name)
               for name in store.client.list_indexes(collection_name)]
    vector_indexes = [item for item in indexes if item.get("field_name") == "vector"]
    if len(vector_indexes) != 1:
        raise ValueError("无法确定资料库的向量索引距离度量。")
    metric = vector_indexes[0]["metric_type"]
    limit = settings.top_k if top_k is None else top_k
    if limit <= 0:
        raise ValueError("检索条数必须大于零。")
    # 先扩大候选，避免标题和重复段落挤占最终证据名额；不修改索引。
    documents_and_scores = store.similarity_search_with_score(
        question,
        k=min(max(limit * 10, 40), 200),
        **({"expr": f'folder_id == "{folder_id}"'} if folder_id else {}),
    )
    threshold = settings.rag_relevance_threshold
    relevant = []
    seen = set()
    for document, distance in documents_and_scores:
        score = relevance_from_distance(float(distance), metric)
        content = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", document.page_content)
        key = re.sub(r"\s+", "", content)
        # 单独的刊名、页眉和图片链接不足以作为回答证据。
        if score < threshold or len(re.findall(r"[\w]", key)) < 20 or key in seen:
            continue
        seen.add(key)
        relevant.append((document, score))
        if len(relevant) == limit:
            break
    return evidence_from_documents(
        [document for document, _ in relevant],
        label_prefix=label_prefix,
        relevance_scores=[score for _, score in relevant],
    )


def relevance_from_distance(distance: float, metric: str) -> float:
    """按实际索引度量换算；与归一化 BGE 向量及现有阈值口径一致。"""
    if metric == "L2":
        return 1 - distance / 4.0
    if metric in {"IP", "COSINE"}:
        return (distance + 1) / 2.0
    raise ValueError(f"不支持的向量距离度量：{metric}")


def retrieve_versions(
    question: str,
    settings: Settings,
    top_k: int | None = None,
) -> tuple[list[Evidence], list[Evidence]]:
    """分别检索新增资料和原有资料，新增集合优先展示。"""
    embeddings = create_embeddings(settings.embedding_model)
    databases = list_databases(settings)
    if not any(item["collection"] == settings.additions_collection_name for item in databases):
        databases.insert(1, {
            "id": "legacy-additions",
            "name": "补充资料",
            "collection": settings.additions_collection_name,
        })
    additions = [item for item in databases if item["id"] != "initial"]
    # 用户在问题中点名目录时，先在该目录内检索；其他问题只查询各集合一次。
    query = re.sub(r"\s+", "", question.casefold())
    def folder_mentioned(item: dict) -> bool:
        name = re.sub(r"\s+", "", item["name"].casefold())
        terms = []
        for part in re.findall(r"[\u4e00-\u9fff]+|[a-z0-9]+", name):
            terms.extend(
                re.findall(r"(?=([\u4e00-\u9fff]{2}))", part)
                if re.fullmatch(r"[\u4e00-\u9fff]+", part)
                else [part]
            )
        return name in query or any(term in query for term in terms)
    named_folder = next(
        (item for item in reversed(databases) if item["id"] != "legacy-additions" and folder_mentioned(item)),
        None,
    )
    new_evidence = []
    seen_collections = set()
    if named_folder:
        new_evidence.extend(_retrieve_with_embeddings(
            question, settings, settings.additions_collection_name, embeddings, top_k, "N", named_folder["id"]
        ))
        seen_collections.add(settings.additions_collection_name)
    for database in additions:
        collection = database["collection"]
        if named_folder:
            if collection == settings.additions_collection_name:
                continue
            if database["id"] != named_folder["id"]:
                continue
        if collection in seen_collections:
            continue
        new_evidence.extend(_retrieve_with_embeddings(
            question, settings, collection, embeddings, top_k, "N"
        ))
        seen_collections.add(collection)
    if named_folder and len(new_evidence) < (top_k or settings.top_k):
        # 目录内证据不足时扩大到全部新增资料，避免目录匹配错误造成漏答。
        new_evidence = []
        seen_collections.clear()
        for database in additions:
            collection = database["collection"]
            if collection in seen_collections:
                continue
            new_evidence.extend(_retrieve_with_embeddings(
                question, settings, collection, embeddings, top_k, "N"
            ))
            seen_collections.add(collection)
    old_evidence = _retrieve_with_embeddings(
        question,
        settings,
        settings.collection_name,
        embeddings,
        top_k,
        "O",
    )
    return [replace(item, label=f"N{index}") for index, item in enumerate(new_evidence, 1)], old_evidence
