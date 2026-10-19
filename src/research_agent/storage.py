"""本地 JSON 文件的原子写入工具。"""

import json
from pathlib import Path
from tempfile import NamedTemporaryFile


def write_json_atomic(path: Path, value: object) -> None:
    """同目录临时写入后替换，避免读到半份 JSON 或复用并发临时文件。"""
    encoded = json.dumps(value, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as file:
            temporary = Path(file.name)
            file.write(encoded)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
