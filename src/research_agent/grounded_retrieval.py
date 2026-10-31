"""显式书目范围检索和内容词排序；业务与基础集合评测共用。"""

import math
import re
from collections import Counter

from research_agent.evidence_policy import (
    GROUNDING_GUIDANCE,  # noqa: F401 - 兼容评测导入
    merge_round_robin,
    split_queries,
)
from research_agent.retrieval import retrieve, retrieve_collection


def normalize(value):
    return re.sub(r"[\W_]+", "", value.casefold())


def source_scope(query, data_dir):
    """只读文件名确定显式书目范围；未知书目不构造路径，不读取文件正文。"""
    titles = [normalize(t) for t in re.findall(r"《([^》]+)》", query)]
    if not titles:
        return ()
    paths = [
        p
        for p in data_dir.rglob("*")
        if p.is_file()
        and p.suffix.lower() in {".md", ".txt", ".pdf"}
        and p.resolve().is_relative_to(data_dir.resolve())
    ]
    selected = [p for p in paths if any(t and t in normalize(p.stem) for t in titles)]
    # 如问题显式限定第一卷等分册，优先匹配该分册，避免同刊其他卷挤占。
    volumes = re.findall(r"第[一二三四五六七八九十百\d]+卷", query)
    exact = [p for p in selected if any(v in p.stem for v in volumes)]
    return tuple(sorted(p.relative_to(data_dir).as_posix() for p in (exact or selected)))


def retrieve_business(question, settings, top_k=None):
    """兼容第四周入口；所有业务检索统一由retrieve_library处理。"""
    from research_agent.retrieval import retrieve_library

    return retrieve_library(question, settings, top_k)


def query_terms(query):
    text = re.sub(r"《[^》]+》", " ", query)
    text = re.sub(
        r"请分别回答并逐项引用证据|请根据|记载|前言|凡例|版权页|题名页|是什么|分别|多少|何时|何年|哪些|如何|为什么|哪家|哪个|介绍",
        " ",
        text,
    )
    parts = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9]+", text)
    return {part[i : i + n] for part in parts for n in (2, 3) for i in range(len(part) - n + 1)}


def rank_content(query, candidates):
    """同一候选池内按问题内容词的区分度排序，不给目录中的章节名加分。"""
    terms = query_terms(query)
    if not terms or not candidates:
        return list(candidates)
    texts = [normalize(e.text) for e in candidates]
    counts = Counter(term for term in terms for text in texts if term in text)
    scores = [
        sum(math.log(1 + (len(texts) + 1) / (counts[t] + 1)) for t in terms if t in text)
        for text in texts
    ]
    ranked = [
        e
        for _, e in sorted(
            zip(scores, candidates, strict=True), key=lambda pair: pair[0], reverse=True
        )
    ]
    # 只在明确询问著录角色时优先有版次/CIP/ISBN等结构线索的片段；
    # 不据此声称物理页面身份，也不给目录中的“版权页”三个字单独加分。
    if re.search(
        r"出版社|出版[时日年]|第一版|第1版|印刷|ISBN|书号|编写机构|标点者|主编",
        query,
        re.IGNORECASE,
    ):

        def imprint(item):
            text = item.text
            return bool(
                re.search(r"图书在版编目|版\s*次|第\s*[一1]\s*版|ISBN", text, re.IGNORECASE)
                and re.search(r"出版发行|出版社|出版者|印刷", text)
            )

        ranked.sort(key=imprint, reverse=True)
    return ranked


def retrieve_grounded(
    question, settings, top_k=4, *, retrieve_fn=retrieve, scoped_fn=retrieve_collection
):
    """只在显式书目范围查基础集合；不读答案，不更改或重建索引。"""
    if not 1 <= top_k <= 12:
        raise ValueError("质量候选证据预算须为1至12。")
    groups = []
    for query in split_queries(question):
        scope = source_scope(query, settings.data_dir)
        if scope:
            candidates = scoped_fn(
                query, settings, settings.collection_name, top_k=12, source_paths=scope
            )
            # 明确指定的书目缺证时不把别的书误当作该书；空结果仍保留无答案。
        else:
            candidates = retrieve_fn(query, settings, top_k=12)
        groups.append(rank_content(query, candidates))
    return merge_round_robin(groups, top_k=top_k)
