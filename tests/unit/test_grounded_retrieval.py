"""来源范围必须来自合法目录，排序不读标准答案。"""

from dataclasses import replace

from research_agent.config import get_settings
from research_agent.grounded_retrieval import rank_content, retrieve_grounded, source_scope
from research_agent.retrieval import Evidence


def test_scope_normalizes_spaces_and_respects_explicit_volume(tmp_path):
    for name in ("上海 甲志.md", "期刊 第一卷.md", "期刊 第二卷.md"):
        (tmp_path / name).write_text("测试正文", encoding="utf-8")
    assert source_scope("《甲志》何时出版", tmp_path) == ("上海 甲志.md",)
    assert source_scope("《期刊》第一卷何时出版", tmp_path) == ("期刊 第一卷.md",)
    assert source_scope("《不存在》谁编", tmp_path) == ()


def test_content_words_beat_directory_headings():
    heading = Evidence("S1", "前言 凡例 目录 人民", "a")
    answer = Evidence("S2", "一九五九年十月建安亭人民公社", "a")
    assert rank_content("《安亭志》前言人民公社何时建立？", [heading, answer])[0] == answer


def test_scoped_query_does_not_fallback_to_other_books_and_keeps_budget(tmp_path):
    (tmp_path / "甲志.md").write_text("测试", encoding="utf-8")
    settings = replace(get_settings(), data_dir=tmp_path)
    calls = []

    def scoped(q, settings, collection, **kwargs):
        calls.append(kwargs)
        return []

    def unscoped(*args, **kwargs):
        return [Evidence("S1", "其他书的数字", "b")]

    result = retrieve_grounded(
        "①《甲志》谁编？②无书名问题", settings, retrieve_fn=unscoped, scoped_fn=scoped
    )
    assert calls[0]["source_paths"] == ("甲志.md",)
    assert [e.text for e in result] == ["其他书的数字"]


def test_source_filter_is_serialized_as_data_with_folder_filter(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace

    from research_agent import retrieval

    calls = []

    class Store:
        def __init__(self, **kwargs):
            self.client = SimpleNamespace(
                has_collection=lambda _: True,
                list_indexes=lambda _: ["vector"],
                describe_index=lambda *_: {"field_name": "vector", "metric_type": "L2"},
            )

        def similarity_search_with_score(self, *args, **kwargs):
            calls.append(kwargs)
            return []

    monkeypatch.setattr(retrieval, "Milvus", Store)
    settings = replace(get_settings(), data_dir=tmp_path)
    source = '含"引号\\路径.md'
    retrieval._retrieve_with_embeddings(
        "问题", settings, "base", object(), 4, "S", folder_id="folder", source_paths=(source,)
    )
    expression = calls[0]["expr"]
    assert expression.startswith('folder_id == "folder" and source in ')
    assert json.loads(expression.split("source in ", 1)[1]) == [source]
