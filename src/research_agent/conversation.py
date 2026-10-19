"""保存可恢复的本地对话记录。"""

import json
import re
from pathlib import Path

from research_agent.storage import write_json_atomic


SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_TURNS = 20


class ConversationStore:
    """按会话 ID 保存最近若干轮用户与助手文本。"""

    def __init__(self, directory: Path):
        self.directory = directory

    def _path(self, session_id: str) -> Path:
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError("会话 ID 只能包含英文字母、数字、下划线和连字符，长度为 1 至 64。")
        return self.directory / f"{session_id}.json"

    def load(self, session_id: str) -> list[dict[str, str]]:
        path = self._path(session_id)
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError(f"会话记录格式无效：{path}")
        turns = [
            item for item in data
            if isinstance(item, dict)
            and isinstance(item.get("user"), str)
            and isinstance(item.get("assistant"), str)
        ]
        return turns[-MAX_TURNS:]

    def save(self, session_id: str, turns: list[dict[str, str]]) -> None:
        path = self._path(session_id)
        write_json_atomic(path, turns[-MAX_TURNS:])

    def delete(self, session_id: str) -> bool:
        """删除单个会话文件；返回会话此前是否存在。"""
        path = self._path(session_id)
        if not path.exists():
            return False
        path.unlink()
        path.with_suffix(".tmp").unlink(missing_ok=True)
        return True

    def list_sessions(self) -> list[dict[str, str]]:
        """列出有对话内容的会话，按最近修改时间倒序排列。"""
        if not self.directory.exists():
            return []
        sessions = []
        for path in self.directory.glob("*.json"):
            session_id = path.stem
            if not SESSION_ID_PATTERN.fullmatch(session_id):
                continue
            try:
                turns = self.load(session_id)
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            if not turns:
                continue
            title = " ".join(turns[0]["user"].split())
            if len(title) > 36:
                title = title[:35].rstrip() + "…"
            sessions.append({
                "id": session_id,
                "title": title or "新对话",
                "updated_at": str(path.stat().st_mtime),
            })
        return sorted(sessions, key=lambda item: float(item["updated_at"]), reverse=True)
