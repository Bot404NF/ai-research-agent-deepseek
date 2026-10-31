"""质量候选策略：显式书目范围检索和内容词排序；不改默认业务入口。"""

import math
import re
from collections import Counter

from research_agent.evaluation.split_retrieval import merge_round_robin, split_queries
from research_agent.retrieval import retrieve, retrieve_collection

GROUNDING_GUIDANCE = """逐项给出问题所问的全部事实，分清全书与分卷、初稿与修订、编纂与标点等对象和角色。某项缺证就明确该项无法确认，不猜测。
每项事实紧邻引用直接支持它的标签。直接引号必须保留证据原字，包括疑似OCR错字；若作释义或校改，明确标为释义/疑似，不伪装成原文。纯数字不能推断为年份。
同一上下文对所问事实有异说时，即使主答案明确，也须简短列出不同说法及各自引用；不同版本分别说明，不能拼接。缺证时简短说明缺少哪项信息即可，不推断无关片段的含义。"""


def normalize(value):
    return re.sub(r"[\W_]+", "", value.casefold())


def source_scope(query, data_dir):
    """只读文件名确定显式书目范围；未知书目不构造路径，不读取文件正文。"""
    titles = [normalize(t) for t in re.findall(r"《([^》]+)》", query)]
    if not titles:
        return ()
    paths = [p for p in data_dir.rglob("*.md") if p.resolve().is_relative_to(data_dir.resolve())]
    selected = [p for p in paths if any(t and t in normalize(p.stem) for t in titles)]
    # 如问题显式限定第一卷等分册，优先匹配该分册，避免同刊其他卷挤占。
    volumes = re.findall(r"第[一二三四五六七八九十百\d]+卷", query)
    exact = [p for p in selected if any(v in p.stem for v in volumes)]
    return tuple(sorted(p.relative_to(data_dir).as_posix() for p in (exact or selected)))


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
    return [
        e
        for _, e in sorted(
            zip(scores, candidates, strict=True), key=lambda pair: pair[0], reverse=True
        )
    ]


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
