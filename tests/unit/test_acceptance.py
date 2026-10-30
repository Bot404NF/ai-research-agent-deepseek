"""防止小样本性能统计口径出错。"""

from research_agent.evaluation.acceptance import distribution


def test_percentile_uses_nearest_rank_and_median_handles_even_samples():
    values = list(range(1, 21))
    assert distribution(values) == {"count": 20, "median_seconds": 10.5, "p95_seconds": 19}
    assert distribution([])["p95_seconds"] is None
    assert distribution([2])["median_seconds"] == 2
