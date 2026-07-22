"""Persistent session metadata and optional LangGraph SQLite checkpoints."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Optional


class SessionManager:
    """Persist conversations while keeping LangGraph's thread ID stable.

    LangGraph checkpoints store graph state and are optional at runtime because
    ``langgraph-checkpoint-sqlite`` is an optional dependency.  Session and
    history data use a small SQLite database unconditionally, so the history
    API remains durable across restarts even when the graph is run in the
    lightweight ReAct compatibility mode.
    """

    def __init__(self, checkpoint_dir: Optional[str] = None):
        self.checkpoint_dir = Path(checkpoint_dir or os.getenv("ASKDATA_STATE_DIR") or Path.cwd() / ".checkpoints")
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_path = self.checkpoint_dir / "sessions.sqlite"
        self._lock = asyncio.Lock()
        self._saver: Any | None = None
        self._saver_context: Any | None = None
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.metadata_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL UNIQUE,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    database_id TEXT,
                    user_id TEXT NOT NULL DEFAULT 'local-user'
                );
                CREATE TABLE IF NOT EXISTS session_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    question TEXT NOT NULL,
                    sql TEXT,
                    answer TEXT NOT NULL,
                    timestamp REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_session_history_session_time
                    ON session_history(session_id, timestamp, id);
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(sessions)")}
            if "user_id" not in columns:
                connection.execute(
                    "ALTER TABLE sessions ADD COLUMN user_id TEXT NOT NULL DEFAULT 'local-user'"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_user_updated ON sessions(user_id, updated_at DESC)"
            )

    def get_saver(self):
        """Return the process-wide ``SqliteSaver`` when the optional package exists."""
        if self._saver is not None:
            return self._saver
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "LangGraph SQLite checkpoints require langgraph-checkpoint-sqlite."
            ) from exc
        context = SqliteSaver.from_conn_string(str(self.checkpoint_dir / "langgraph_checkpoints.sqlite"))
        self._saver_context = context
        self._saver = context.__enter__() if hasattr(context, "__enter__") else context
        return self._saver

    def get_thread_id(self, session_id: str) -> str:
        return session_id

    def load_agent_state(self, session_id: str) -> dict[str, Any]:
        """Load the latest lightweight Agent state recorded for this thread."""
        try:
            checkpoint = self.get_saver().get_tuple({
                "configurable": {"thread_id": self.get_thread_id(session_id), "checkpoint_ns": ""},
            })
        except RuntimeError:
            return {}
        if checkpoint is None:
            return {}
        state = checkpoint.checkpoint.get("channel_values", {}).get("agent_state", {})
        return state if isinstance(state, dict) else {}

    def save_agent_state(self, session_id: str, state: dict[str, Any]) -> None:
        """Persist the latest ReAct state in LangGraph's SQLite checkpoint store."""
        try:
            from langgraph.checkpoint.base import empty_checkpoint

            version = str(time.time_ns())
            checkpoint = empty_checkpoint()
            checkpoint["channel_values"] = {"agent_state": state}
            checkpoint["channel_versions"] = {"agent_state": version}
            self.get_saver().put(
                {"configurable": {"thread_id": self.get_thread_id(session_id), "checkpoint_ns": ""}},
                checkpoint,
                {"source": "loop", "step": 0, "parents": {}},
                {"agent_state": version},
            )
        except RuntimeError:
            # The history database is still durable when the optional LangGraph
            # checkpoint extension has deliberately not been installed.
            return

    async def create_session(self, database_id: Optional[str] = None, user_id: str = "local-user") -> str:
        async with self._lock:
            session_id = str(uuid.uuid4())
            now = time.time()
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO sessions(session_id, thread_id, created_at, updated_at, database_id, user_id) VALUES (?, ?, ?, ?, ?, ?)",
                    (session_id, session_id, now, now, database_id, user_id),
                )
            return session_id

    async def get_session(self, session_id: str, user_id: str = "local-user") -> Optional[dict]:
        async with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT session_id, thread_id, created_at, updated_at, database_id, user_id FROM sessions WHERE session_id = ? AND user_id = ?",
                    (session_id, user_id),
                ).fetchone()
        if row is None:
            return None
        session = dict(row)
        session["history"] = await self.get_history(session_id, user_id) or []
        return session

    async def list_sessions(self, limit: int = 50, offset: int = 0, user_id: str = "local-user") -> tuple[list[dict], int]:
        async with self._lock:
            with self._connect() as connection:
                total = connection.execute(
                    "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (user_id,)
                ).fetchone()[0]
                rows = connection.execute(
                    """
                    SELECT s.session_id, s.thread_id, s.created_at, s.updated_at, s.database_id,
                           COUNT(h.id) AS question_count
                    FROM sessions AS s
                    LEFT JOIN session_history AS h ON h.session_id = s.session_id
                    WHERE s.user_id = ?
                    GROUP BY s.session_id
                    ORDER BY s.updated_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (user_id, limit, offset),
                ).fetchall()
        return [dict(row) for row in rows], int(total)

    async def delete_session(self, session_id: str, user_id: str = "local-user") -> bool:
        async with self._lock:
            with self._connect() as connection:
                owned = connection.execute(
                    "SELECT 1 FROM sessions WHERE session_id = ? AND user_id = ?", (session_id, user_id)
                ).fetchone()
                if owned is None:
                    return False
                connection.execute("DELETE FROM session_history WHERE session_id = ?", (session_id,))
                deleted = connection.execute(
                    "DELETE FROM sessions WHERE session_id = ? AND user_id = ?", (session_id, user_id)
                ).rowcount
        return bool(deleted)

    async def update_session(self, session_id: str, database_id: Optional[str] = None, user_id: str = "local-user") -> bool:
        async with self._lock:
            with self._connect() as connection:
                if database_id is None:
                    updated = connection.execute(
                        "UPDATE sessions SET updated_at = ? WHERE session_id = ? AND user_id = ?",
                        (time.time(), session_id, user_id),
                    ).rowcount
                else:
                    updated = connection.execute(
                        "UPDATE sessions SET database_id = ?, updated_at = ? WHERE session_id = ? AND user_id = ?",
                        (database_id, time.time(), session_id, user_id),
                    ).rowcount
        return bool(updated)

    async def append_history(self, session_id: str, question: str, sql: Optional[str] = None, answer: str = "", user_id: str = "local-user") -> bool:
        async with self._lock:
            now = time.time()
            with self._connect() as connection:
                exists = connection.execute(
                    "SELECT 1 FROM sessions WHERE session_id = ? AND user_id = ?", (session_id, user_id)
                ).fetchone()
                if exists is None:
                    return False
                connection.execute(
                    "INSERT INTO session_history(session_id, question, sql, answer, timestamp) VALUES (?, ?, ?, ?, ?)",
                    (session_id, question, sql, answer, now),
                )
                connection.execute("UPDATE sessions SET updated_at = ? WHERE session_id = ?", (now, session_id))
        return True

    async def get_history(self, session_id: str, user_id: str = "local-user") -> Optional[list[dict[str, Any]]]:
        async with self._lock:
            with self._connect() as connection:
                exists = connection.execute(
                    "SELECT 1 FROM sessions WHERE session_id = ? AND user_id = ?", (session_id, user_id)
                ).fetchone()
                if exists is None:
                    return None
                rows = connection.execute(
                    "SELECT question, sql, answer, timestamp FROM session_history WHERE session_id = ? ORDER BY timestamp, id",
                    (session_id,),
                ).fetchall()
        return [dict(row) for row in rows]

    async def clear_history(self, session_id: str, user_id: str = "local-user") -> bool:
        async with self._lock:
            with self._connect() as connection:
                exists = connection.execute(
                    "SELECT 1 FROM sessions WHERE session_id = ? AND user_id = ?", (session_id, user_id)
                ).fetchone()
                if exists is None:
                    return False
                connection.execute("DELETE FROM session_history WHERE session_id = ?", (session_id,))
                connection.execute("UPDATE sessions SET updated_at = ? WHERE session_id = ?", (time.time(), session_id))
        return True


session_manager = SessionManager()
