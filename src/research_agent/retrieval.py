import json
import re
from dataclasses import dataclass, replace

from langchain_core.documents import Document
from langchain_milvus import Milvus

from research_agent.config import Settings
from research_agent.embeddings import create_embeddings
from research_agent.observability.runtime import observed_step
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
        evidence.append(
            Evidence(
                label=f"{label_prefix}{index}",
                text=document.page_content,
                source=str(document.metadata.get("source", "未知来源")),
                page=document.metadata.get("page"),
                relevance_score=(relevance_scores[index - 1] if relevance_scores else None),
            )
        )
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
    source_paths: tuple[str, ...] = (),
) -> list[Evidence]:
    embeddings = create_embeddings(settings.embedding_model)
    return _retrieve_with_embeddings(
        question,
        settings,
        collection_name,
        embeddings,
        top_k,
        label_prefix,
        source_paths=source_paths,
    )


def _retrieve_with_embeddings(
    question: str,
    settings: Settings,
    collection_name: str,
    embeddings,
    top_k: int | None,
    label_prefix: str,
    folder_id: str | None = None,
    source_paths: tuple[str, ...] = (),
) -> list[Evidence]:
    store = Milvus(
        embedding_function=embeddings,
        collection_name=collection_name,
        connection_args={"uri": settings.milvus_uri},
    )
    if not store.client.has_collection(collection_name):
        return []
    indexes = [
        store.client.describe_index(collection_name, name)
        for name in store.client.list_indexes(collection_name)
    ]
    vector_indexes = [item for item in indexes if item.get("field_name") == "vector"]
    if len(vector_indexes) != 1:
        raise ValueError("无法确定资料库的向量索引距离度量。")
    metric = vector_indexes[0]["metric_type"]
    limit = settings.top_k if top_k is None else top_k
    if limit <= 0:
        raise ValueError("检索条数必须大于零。")
    # 先扩大候选，避免标题和重复段落挤占最终证据名额；不修改索引。
    filters = []
    if folder_id:
        filters.append(f"folder_id == {json.dumps(folder_id)}")
    if source_paths:
        filters.append(f"source in {json.dumps(list(source_paths), ensure_ascii=False)}")
    documents_and_scores = store.similarity_search_with_score(
        question,
        k=min(max(limit * 10, 40), 200),
        **({"expr": " and ".join(filters)} if filters else {}),
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
    *,
    candidate_k: int | None = None,
    source_paths: tuple[str, ...] = (),
) -> tuple[list[Evidence], list[Evidence]]:
    """兼容历史调用：分别读取增量与基础集合；业务入口使用 retrieve_library。"""
    embeddings = create_embeddings(settings.embedding_model)
    retrieval_limit = top_k if candidate_k is None else candidate_k
    databases = list_databases(settings)
    if not any(item["collection"] == settings.additions_collection_name for item in databases):
        databases.insert(
            1,
            {
                "id": "legacy-additions",
                "name": "补充资料",
                "collection": settings.additions_collection_name,
            },
        )
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
        (
            item
            for item in reversed(databases)
            if item["id"] != "legacy-additions" and folder_mentioned(item)
        ),
        None,
    )
    new_evidence = []
    seen_collections = set()
    if named_folder:
        new_evidence.extend(
            _retrieve_with_embeddings(
                question,
                settings,
                settings.additions_collection_name,
                embeddings,
                retrieval_limit,
                "N",
                named_folder["id"],
            )
        )
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
        new_evidence.extend(
            _retrieve_with_embeddings(
                question, settings, collection, embeddings, retrieval_limit, "N"
            )
        )
        seen_collections.add(collection)
    if named_folder and len(new_evidence) < (top_k or settings.top_k):
        # 目录内证据不足时扩大到全部新增资料，避免目录匹配错误造成漏答。
        new_evidence = []
        seen_collections.clear()
        for database in additions:
            collection = database["collection"]
            if collection in seen_collections:
                continue
            new_evidence.extend(
                _retrieve_with_embeddings(
                    question, settings, collection, embeddings, retrieval_limit, "N"
                )
            )
            seen_collections.add(collection)
    old_evidence = _retrieve_with_embeddings(
        question,
        settings,
        settings.collection_name,
        embeddings,
        retrieval_limit,
        "O",
        **({"source_paths": source_paths} if source_paths else {}),
    )
    return [
        replace(item, label=f"N{index}") for index, item in enumerate(new_evidence, 1)
    ], old_evidence


@observed_step("retrieval")
def retrieve_library(
    question: str,
    settings: Settings,
    top_k: int | None = None,
) -> list[Evidence]:
    """沿用集合/目录选择，分题取候选并按书目及内容选择最终证据。"""
    from research_agent.evidence_policy import merge_round_robin, split_queries
    from research_agent.grounded_retrieval import normalize, rank_content, source_scope

    limit = settings.top_k if top_k is None else top_k
    if limit <= 0:
        raise ValueError("检索条数必须大于零。")
    if not settings.grounded_answers:
        additions, base = retrieve_versions(question, settings, top_k)
        candidates = sorted(
            [*additions, *base],
            key=lambda e: e.relevance_score if e.relevance_score is not None else -1,
            reverse=True,
        )
        return merge_round_robin([candidates], top_k=limit)
    groups = []
    for query in split_queries(question):
        scope = source_scope(query, settings.data_dir)
        additions, base = retrieve_versions(
            query, settings, limit, candidate_k=max(12, limit), source_paths=scope
        )
        candidates = sorted(
            [*additions, *base],
            key=lambda item: item.relevance_score if item.relevance_score is not None else -1,
            reverse=True,
        )
        titles = [normalize(t) for t in re.findall(r"《([^》]+)》", query)]
        # 增量集合也按实际来源名匹配书目；不因导入时间替换或拼接不同版本。
        matched = [
            e
            for e in candidates
            if any(
                t
                and t in normalize(e.source.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0])
                for t in titles
            )
        ]
        if matched:
            candidates = matched
        groups.append(rank_content(query, candidates))
    return merge_round_robin(groups, top_k=limit)
