"""会话持久化存储（P1：单用户/同浏览器/匿名会话号）。

ConversationStore 接口 + SQLite 实现。RAG core 不感知会话；P3 多 worker
换 Redis 实现只需匹配同一接口，测试用内存 SQLite。坐标图不入库，按 meta
里的页码+坐标经 /cite_crop 再生。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ConversationSummary:
    id: str
    title: str
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class StoredMessage:
    role: str
    content: str
    meta: dict
    created_at: float


class ConversationStore(Protocol):
    """会话库操作手册：换存储实现（SQLite→Redis）不动调用方。"""

    def create(self, session_id: str, title: str = "") -> str: ...
    def list(self, session_id: str) -> list[ConversationSummary]: ...
    def get(self, conversation_id: str) -> list[StoredMessage]: ...
    def append(self, conversation_id: str, role: str, content: str,
               meta: dict | None = None) -> None: ...
    def delete(self, conversation_id: str) -> None: ...
    def rename(self, conversation_id: str, title: str) -> None: ...
    def owner(self, conversation_id: str) -> str | None: ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    rev INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id);
"""


class SqliteConversationStore:
    """SQLite 实现；单连接 + 锁，供 FastAPI 线程池并发调用。"""

    def __init__(self, path: str | Path):
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            self._rev = self._conn.execute(
                "SELECT COALESCE(MAX(rev), 0) FROM conversations").fetchone()[0]

    def close(self) -> None:
        self._conn.close()

    def create(self, session_id: str, title: str = "") -> str:
        cid = uuid.uuid4().hex
        now = time.time()
        with self._lock:
            self._rev += 1
            self._conn.execute(
                "INSERT INTO conversations(id, session_id, title, created_at, updated_at, rev)"
                " VALUES(?,?,?,?,?,?)", (cid, session_id, title, now, now, self._rev))
            self._conn.commit()
        return cid

    def list(self, session_id: str) -> list[ConversationSummary]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, title, created_at, updated_at FROM conversations"
                " WHERE session_id=? ORDER BY rev DESC",
                (session_id,)).fetchall()
        return [ConversationSummary(r["id"], r["title"], r["created_at"], r["updated_at"])
                for r in rows]

    def get(self, conversation_id: str) -> list[StoredMessage]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content, meta, created_at FROM messages"
                " WHERE conversation_id=? ORDER BY id", (conversation_id,)).fetchall()
        return [StoredMessage(r["role"], r["content"], json.loads(r["meta"]), r["created_at"])
                for r in rows]

    def append(self, conversation_id: str, role: str, content: str,
               meta: dict | None = None) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages(conversation_id, role, content, meta, created_at)"
                " VALUES(?,?,?,?,?)",
                (conversation_id, role, content,
                 json.dumps(meta or {}, ensure_ascii=False), now))
            self._rev += 1
            self._conn.execute(
                "UPDATE conversations SET updated_at=?, rev=? WHERE id=?",
                (now, self._rev, conversation_id))
            self._conn.commit()

    def delete(self, conversation_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
            self._conn.commit()

    def rename(self, conversation_id: str, title: str) -> None:
        with self._lock:
            self._rev += 1
            self._conn.execute(
                "UPDATE conversations SET title=?, rev=? WHERE id=?",
                (title, self._rev, conversation_id))
            self._conn.commit()

    def owner(self, conversation_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT session_id FROM conversations WHERE id=?",
                (conversation_id,)).fetchone()
        return row["session_id"] if row else None