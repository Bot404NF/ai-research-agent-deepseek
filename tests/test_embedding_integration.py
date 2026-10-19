import math
import os

import pytest

from research_agent.config import get_settings
from research_agent.embeddings import create_embeddings


@pytest.mark.integration
def test_bge_m3_embedding_dimension_and_vector_result() -> None:
    """加载配置的本地模型并验证生成的向量。

    运行 `uv run pytest -m integration -s tests/test_embedding_integration.py` 可查看向量维度和部分结果。
    首次运行可能会下载模型权重。
    """
    settings = get_settings()

    embeddings = create_embeddings(settings.embedding_model)
    vectors = embeddings.embed_documents([
        "检索增强生成使用相关资料为回答提供依据。",
        "向量嵌入将文本表示为一组数值。",
    ])

    assert len(vectors) == 2
    dimensions = {len(vector) for vector in vectors}
    assert len(dimensions) == 1
    dimension = dimensions.pop()
    assert dimension > 0
    for vector in vectors:
        assert all(math.isfinite(value) for value in vector)
        assert any(value != 0 for value in vector)
    assert vectors[0] != vectors[1]

    expected_dimension = os.getenv("BGE_M3_EXPECTED_DIMENSION")
    if expected_dimension:
        assert dimension == int(expected_dimension)

    print(f"模型={settings.embedding_model}，向量维度={dimension}")
    print(f"第一个向量的前 8 个值：{vectors[0][:8]}")
