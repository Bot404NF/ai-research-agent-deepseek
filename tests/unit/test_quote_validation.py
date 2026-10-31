"""逐字检查只是诊断，不把标签合法当作事实支持。"""

from research_agent.quote_validation import check_direct_quotes


def test_ocr_change_wrong_source_and_unmarked_omission_are_reported():
    evidence = [
        {"label": "S1", "text": "其涉于怪诞者虽工不金。清张某（字甲）著。"},
        {"label": "S2", "text": "另书编者乙"},
    ]
    for answer in (
        "原文：“其涉于怪诞者虽工不录”[S1]",
        "原文：“清张某著”[S1]",
        "原文：“另书编者乙”[S1]",
    ):
        assert check_direct_quotes(answer, evidence)["issues"]
    assert not check_direct_quotes("原文：“清张某……著”[S1]", evidence)["issues"]


def test_punctuation_and_whitespace_are_allowed_without_changing_text():
    evidence = [{"label": "S1", "text": "初稿四卷合并为三卷"}]
    assert not check_direct_quotes("原文：“初稿四卷，合并为三卷。”[S1]", evidence)["issues"]
    assert not check_direct_quotes("它的字为‘甲’。[S1]", evidence)["issues"]
