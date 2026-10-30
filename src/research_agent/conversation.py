"""SQLite 会话存储：完整历史与模型使用的近期上下文分别读取。"""

import json
import re
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path

SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_TURNS = 20  # 仅作默认模型上下文轮数，不再限制数据库历史。


def _session_id(value: str) -> str:
    if not isinstance(value, str) or not SESSION_ID_PATTERN.fullmatch(value):
        raise ValueError("会话 ID 只能包含英文字母、数字、下划线和连字符，长度为 1 至 64。")
    return value


def _turns(value: list) -> list[dict[str, str]]:
    if not isinstance(value, list) or any(
        not isinstance(item, dict)
        or not isinstance(item.get("user"), str)
        or not isinstance(item.get("assistant"), str)
        for item in value
    ):
        raise ValueError("会话记录必须是包含 user 和 assistant 文本的轮次列表。")
    return [{"user": item["user"], "assistant": item["assistant"]} for item in value]


def _page_size(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("每页条数必须是 1 至 100 的整数。")
    return limit


class ConversationStore:
    """每个操作使用独立连接；事务保证一轮的两条消息和会话计数一起提交。"""

    def __init__(self, directory: Path):
        self.directory = directory
        self.database_path = directory / "conversations.sqlite"
        self.migration_warnings: list[str] = []
        self._invalid_legacy: set[str] = set()
        directory.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    session_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    turn_count INTEGER NOT NULL DEFAULT 0,
                    deleted INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS messages (
                    message_id INTEGER PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES conversations(session_id),
                    turn_no INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(session_id, turn_no, role)
                );
                CREATE INDEX IF NOT EXISTS conversation_recent
                    ON conversations(deleted, updated_at DESC, session_id);
            """)
        self._migrate_legacy()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _write(self):
        with closing(self._connect()) as conn, conn:
            # 在读取轮次计数前取得写锁，避免并发追加得到相同的 turn_no。
            conn.execute("BEGIN IMMEDIATE")
            yield conn

    @staticmethod
    def _row(conn, session_id: str):
        return conn.execute(
            "SELECT * FROM conversations WHERE session_id=?", (session_id,)
        ).fetchone()

    @staticmethod
    def _insert(conn, session_id: str, turns: list[dict], now: float) -> None:
        row = ConversationStore._row(conn, session_id)
        if row is not None and row["deleted"]:
            raise ValueError("该会话已删除，请新建会话。")
        count = row["turn_count"] if row else 0
        if not turns:
            return
        if row is None:
            title = " ".join(turns[0]["user"].split())
            title = title[:35].rstrip() + "…" if len(title) > 36 else title
            conn.execute(
                "INSERT INTO conversations(session_id,title,created_at,updated_at) VALUES(?,?,?,?)",
                (session_id, title or "新对话", now, now),
            )
        conn.executemany(
            "INSERT INTO messages(session_id,turn_no,role,content,created_at) VALUES(?,?,?,?,?)",
            [
                (session_id, index, role, turn[role], now)
                for index, turn in enumerate(turns, count + 1)
                for role in ("user", "assistant")
            ],
        )
        conn.execute(
            "UPDATE conversations SET turn_count=?,updated_at=? WHERE session_id=?",
            (count + len(turns), now, session_id),
        )

    def _migrate_legacy(self) -> None:
        with closing(self._connect()) as conn:
            known = {row[0] for row in conn.execute("SELECT session_id FROM conversations")}
        for path in self.directory.glob("*.json"):
            session_id = path.stem
            if not SESSION_ID_PATTERN.fullmatch(session_id) or session_id in known:
                continue
            try:
                turns = _turns(json.loads(path.read_text(encoding="utf-8")))
                modified = path.stat().st_mtime
            except (OSError, UnicodeError, ValueError):
                self._invalid_legacy.add(session_id)
                self.migration_warnings.append(
                    f"会话 {session_id} 的旧文件未能导入，原文件已保留。"
                )
                continue
            with self._write() as conn:
                if self._row(conn, session_id) is None:
                    self._insert(conn, session_id, turns, modified)
                    # 空历史也记录迁移结果，防止每次重新导入。
                    if not turns:
                        conn.execute(
                            "INSERT INTO conversations(session_id,title,created_at,updated_at) VALUES(?,?,?,?)",
                            (session_id, "新对话", modified, modified),
                        )

    def _check(self, session_id: str) -> None:
        _session_id(session_id)
        if session_id in self._invalid_legacy:
            raise ValueError("旧会话文件格式无效或无法读取，原文件已保留，请修复后重试。")

    @staticmethod
    def _select(conn, session_id: str, *, limit=None, before=None, descending=False):
        sql = """SELECT u.turn_no,u.content AS user,a.content AS assistant
                 FROM messages u JOIN messages a
                   ON u.session_id=a.session_id AND u.turn_no=a.turn_no AND a.role='assistant'
                 JOIN conversations c ON c.session_id=u.session_id
                 WHERE u.session_id=? AND u.role='user' AND c.deleted=0"""
        params = [session_id]
        if before is not None:
            sql += " AND u.turn_no<?"
            params.append(before)
        sql += " ORDER BY u.turn_no " + ("DESC" if descending else "ASC")
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return conn.execute(sql, params).fetchall()

    @staticmethod
    def _as_turns(rows) -> list[dict[str, str]]:
        return [{"user": row["user"], "assistant": row["assistant"]} for row in rows]

    def load(self, session_id: str) -> list[dict[str, str]]:
        """供本地调用者读取完整历史；模型调用应使用 load_recent。"""
        self._check(session_id)
        with closing(self._connect()) as conn:
            return self._as_turns(self._select(conn, session_id))

    def load_recent(self, session_id: str, limit: int = MAX_TURNS) -> list[dict[str, str]]:
        self._check(session_id)
        _page_size(limit)
        with closing(self._connect()) as conn:
            rows = self._select(conn, session_id, limit=limit, descending=True)
            return self._as_turns(reversed(rows))

    def append_turn(self, session_id: str, user: str, assistant: str) -> None:
        self._check(session_id)
        turns = _turns([{"user": user, "assistant": assistant}])
        with self._write() as conn:
            self._insert(conn, session_id, turns, time.time())

    def save(self, session_id: str, turns: list[dict[str, str]]) -> None:
        """兼容完整快照调用，只追加新增后缀；禁止短上下文覆盖完整历史。"""
        self._check(session_id)
        turns = _turns(turns)
        with self._write() as conn:
            existing = self._as_turns(self._select(conn, session_id))
            if turns[: len(existing)] != existing:
                raise ValueError("保存快照不能缩短或改写已有历史；新轮次请使用 append_turn。")
            self._insert(conn, session_id, turns[len(existing) :], time.time())

    def page(self, session_id: str, *, limit: int = 20, before: int | None = None) -> dict:
        self._check(session_id)
        _page_size(limit)
        if before is not None and (
            isinstance(before, bool) or not isinstance(before, int) or before < 1
        ):
            raise ValueError("历史游标必须是正整数。")
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN")
            row = self._row(conn, session_id)
            rows = self._select(conn, session_id, limit=limit + 1, before=before, descending=True)
            has_more = len(rows) > limit
            rows = list(reversed(rows[:limit]))
            return {
                "id": session_id,
                "turns": self._as_turns(rows),
                "total_turns": row["turn_count"] if row and not row["deleted"] else 0,
                "has_more": has_more,
                "next_before": rows[0]["turn_no"] if has_more else None,
            }

    def delete(self, session_id: str) -> bool:
        """删除数据库中的消息并保留删除标记；不删除 JSON 备份，防止其再次导入。"""
        self._check(session_id)
        with self._write() as conn:
            row = self._row(conn, session_id)
            if row is None or row["deleted"]:
                return False
            conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            conn.execute(
                "UPDATE conversations SET deleted=1,turn_count=0,title='',updated_at=? WHERE session_id=?",
                (time.time(), session_id),
            )
            return True

    def session_page(self, *, limit: int = 50, offset: int = 0) -> dict:
        _page_size(limit)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("会话列表偏移量必须是非负整数。")
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT session_id,title,updated_at,turn_count FROM conversations "
                "WHERE deleted=0 AND turn_count>0 ORDER BY updated_at DESC,session_id LIMIT ? OFFSET ?",
                (limit + 1, offset),
            ).fetchall()
        return {
            "conversations": [
                {
                    "id": row["session_id"],
                    "title": row["title"],
                    "updated_at": str(row["updated_at"]),
                    "turn_count": row["turn_count"],
                }
                for row in rows[:limit]
            ],
            "has_more": len(rows) > limit,
            "next_offset": offset + limit if len(rows) > limit else None,
            "migration_warnings": self.migration_warnings,
        }

    def list_sessions(self) -> list[dict]:
        """保留本地完整列表接口；网页使用 session_page 分页。"""
        result, offset = [], 0
        while True:
            page = self.session_page(limit=100, offset=offset)
            result.extend(page["conversations"])
            if not page["has_more"]:
                return result
            offset = page["next_offset"]
