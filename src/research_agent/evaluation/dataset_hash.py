"""文本题集摘要：统一换行，并兼容历史Windows换行摘要。"""

import hashlib
from pathlib import Path


def text_digest(path: Path) -> str:
    """仅将CRLF统一为LF，其他字节变化仍改变摘要。"""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def matches_text_digest(path: Path, expected: str) -> bool:
    """接受规范LF摘要及历史CRLF摘要，不改写冻结题集记录。"""
    normalized = path.read_bytes().replace(b"\r\n", b"\n")
    return expected in {
        hashlib.sha256(normalized).hexdigest(),
        hashlib.sha256(normalized.replace(b"\n", b"\r\n")).hexdigest(),
    }
