"""换行兼容不能掩盖题集内容变更，历史冻结摘要也必须继续有效。"""

import hashlib

import pytest

from research_agent.evaluation.dataset_hash import matches_text_digest, text_digest


@pytest.mark.parametrize("file_newline", [b"\n", b"\r\n"], ids=["linux", "windows"])
@pytest.mark.parametrize("recorded_newline", [b"\n", b"\r\n"], ids=["lf-hash", "legacy-crlf-hash"])
def test_text_digest_preserves_content_check_across_platforms(
    tmp_path, file_newline, recorded_newline
):
    canonical = '{"question": "初稿几卷？"}\n'.encode()
    path = tmp_path / "dataset.json"
    path.write_bytes(canonical.replace(b"\n", file_newline))
    expected = hashlib.sha256(canonical.replace(b"\n", recorded_newline)).hexdigest()
    assert matches_text_digest(path, expected)
    assert text_digest(path) == hashlib.sha256(canonical).hexdigest()
    path.write_bytes(path.read_bytes().replace("初稿".encode(), "全志".encode()))
    assert not matches_text_digest(path, expected)
