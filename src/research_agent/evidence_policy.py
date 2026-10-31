"""业务与评测共用的证据拆分、合并及回答约束。"""

import re
from dataclasses import replace

GROUNDING_GUIDANCE = """逐项给出问题所问的全部事实，分清全书与分卷、初稿与修订、编纂与标点等对象和角色。某项缺证就明确该项无法确认，不猜测。
每项事实紧邻引用直接支持它的标签。直接引号必须保留证据原字，包括疑似OCR错字；若作释义或校改，明确标为释义/疑似，不伪装成原文。纯数字不能推断为年份。
同一上下文对所问事实有异说时，即使主答案明确，也须简短列出不同说法及各自引用；不同版本分别说明，不能拼接。缺证时简短说明缺少哪项信息即可，不推断无关片段的含义。
用一句或简短列表回答所问字段，省略未问的背景、人物、出版信息和相关事件；仅补充与所问直接相关且有证据的异说。不推断片段的页面身份：编纂名单、序跋与版权页不可互相替代，未明确页种时只称证据片段。年号对应的公元年份仅在证据明确给出时列出，不自行换算。"""


def split_queries(question: str) -> list[str]:
    """仅拆分范围自足的连续编号；多轮指代由业务原有历史拼接处理。"""
    if "\n后续问题：" in question:
        return [question]
    markers = list(re.finditer(r"[①②③④]", question))
    if len(markers) < 2 or [m.group() for m in markers] != list("①②③④"[: len(markers)]):
        return [question]
    queries = [
        question[m.end() : markers[i + 1].start() if i + 1 < len(markers) else None].strip()
        for i, m in enumerate(markers)
    ]
    return queries if all(queries) else [question]


def merge_round_robin(groups: list[list], top_k: int = 4) -> list:
    """每个子问题轮流保留证据，去重但不合并来源或版本。"""
    if top_k < 1:
        raise ValueError("证据预算必须大于零。")
    found, seen = [], set()
    for rank in range(max((len(g) for g in groups), default=0)):
        for group in groups:
            if rank >= len(group):
                continue
            item = group[rank]
            key = (item.source.replace("\\", "/"), item.page, re.sub(r"\s+", "", item.text))
            if key in seen:
                continue
            seen.add(key)
            found.append(replace(item, label=f"S{len(found) + 1}"))
            if len(found) == top_k:
                return found
    return found
