"""距离换算、低信息候选与重复候选的回归检查。"""

from types import SimpleNamespace

import pytest
from langchain_core.documents import Document

from research_agent import retrieval
from research_agent.config import Settings


def test_metric_conversion_and_unknown_metric():
    assert retrieval.relevance_from_distance(1.0, "L2") == .75
    assert retrieval.relevance_from_distance(.5, "COSINE") == .75
    assert retrieval.relevance_from_distance(.5, "IP") == .75
    with pytest.raises(ValueError):
        retrieval.relevance_from_distance(1, "UNKNOWN")


def test_short_duplicate_and_low_score_candidates_do_not_fill_slots(monkeypatch, tmp_path):
    calls = []
    useful = "原文明确记载该县设立的时间与县治所在，具备足够的上下文支持回答。"
    other = "另一条具有完整上下文的原始记录，说明该县后续沿革和行政区划变更。"
    client = SimpleNamespace(has_collection=lambda _: True,
        list_indexes=lambda _: ["vector"],
        describe_index=lambda *_: {"field_name": "vector", "metric_type": "L2"})

    class Store:
        def __init__(self, **_):
            self.client = client

        def similarity_search_with_score(self, question, **kwargs):
            calls.append(kwargs)
            return [(Document(page_content=text, metadata={"source": "资料.md"}), score)
                    for text, score in [("上海市通志馆期刊", .1), (useful, .2),
                                        (useful, .3), (other, .4), (other + "附注", 3)]]

    monkeypatch.setattr(retrieval, "Milvus", Store)
    settings = Settings("", "chat", "embed", tmp_path, "unused", "base", 800, 120, 2)
    result = retrieval._retrieve_with_embeddings("问题", settings, "base", object(), 2, "S", "folder")
    assert [item.text for item in result] == [useful, other]
    assert calls == [{"k": 40, "expr": 'folder_id == "folder"'}]
    assert [item.label for item in result] == ["S1", "S2"]
