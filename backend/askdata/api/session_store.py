"""Transactional SQLite persistence for API sessions and turns."""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    database_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    response_kind TEXT NOT NULL,
    answer TEXT,
    sql TEXT,
    result_preview_json TEXT,
    chart_json TEXT,
    confidence TEXT,
    error_json TEXT,
    trace_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clarifications (
    id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL UNIQUE REFERENCES turns(id) ON DELETE CASCADE,
    prompt TEXT NOT NULL,
    options_json TEXT NOT NULL,
    resolution_json TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump_json(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _load_json(value: Optional[str]) -> Any:
    if value is None:
        return None
    return json.loads(value)


class SessionStore:
    """Persist conversations using one asynchronously accessed SQLite connection."""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._connection: Optional[aiosqlite.Connection] = None
        self._connection_lock = asyncio.Lock()

    def _require_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("SessionStore is not initialized; call Initialize first")
        return self._connection

    async def Initialize(self) -> None:
        async with self._connection_lock:
            if self._connection is not None:
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            connection = await aiosqlite.connect(self._path)
            connection.row_factory = aiosqlite.Row
            try:
                await connection.execute("PRAGMA foreign_keys = ON")
                journal_cursor = await connection.execute("PRAGMA journal_mode = WAL")
                journal_row = await journal_cursor.fetchone()
                if journal_row is None or str(journal_row[0]).lower() != "wal":
                    raise RuntimeError("SQLite WAL mode could not be enabled")
                await connection.execute("PRAGMA busy_timeout = 5000")
                await connection.executescript(_SCHEMA)
                await connection.commit()
            except BaseException:
                await connection.rollback()
                await connection.close()
                raise
            self._connection = connection

    async def Close(self) -> None:
        async with self._connection_lock:
            connection = self._require_connection()
            self._connection = None
            await connection.close()

    async def CreateSession(self, database_id: str, title: str = "") -> str:
        session_id = str(uuid.uuid4())
        async with self._connection_lock:
            connection = self._require_connection()
            timestamp = _utc_now()
            try:
                await connection.execute("BEGIN IMMEDIATE")
                await connection.execute(
                    """
                    INSERT INTO sessions (id, database_id, title, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (session_id, database_id, title, timestamp, timestamp),
                )
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise
        return session_id

    async def ListSessions(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self._connection_lock:
            connection = self._require_connection()
            if limit <= 0:
                raise ValueError("limit must be greater than 0")
            cursor = await connection.execute(
                """
                SELECT id, database_id, title, created_at, updated_at
                FROM sessions
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def GetSession(self, session_id: str) -> Optional[dict[str, Any]]:
        async with self._connection_lock:
            connection = self._require_connection()
            session_cursor = await connection.execute(
                """
                SELECT id, database_id, title, created_at, updated_at
                FROM sessions WHERE id = ?
                """,
                (session_id,),
            )
            session_row = await session_cursor.fetchone()
            if session_row is None:
                return None

            turns_cursor = await connection.execute(
                """
                SELECT
                    t.id, t.question, t.response_kind, t.answer, t.sql,
                    t.result_preview_json, t.chart_json, t.confidence,
                    t.error_json, t.trace_json, t.created_at,
                    c.id AS clarification_id, c.prompt AS clarification_prompt,
                    c.options_json, c.resolution_json,
                    c.status AS clarification_status,
                    c.created_at AS clarification_created_at, c.resolved_at
                FROM turns AS t
                LEFT JOIN clarifications AS c ON c.turn_id = t.id
                WHERE t.session_id = ?
                ORDER BY t.created_at ASC, t.id ASC
                """,
                (session_id,),
            )
            rows = await turns_cursor.fetchall()
            turns = [self._turn_from_row(row) for row in rows]
            return {**dict(session_row), "turns": turns}

    async def DeleteSession(self, session_id: str) -> bool:
        async with self._connection_lock:
            connection = self._require_connection()
            try:
                await connection.execute("BEGIN IMMEDIATE")
                cursor = await connection.execute(
                    "DELETE FROM sessions WHERE id = ?", (session_id,)
                )
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise
        return cursor.rowcount > 0

    async def SaveTurn(self, session_id: str, turn: dict[str, Any]) -> str:
        turn_id = turn.get("id") or turn.get("turn_id")
        question = turn.get("question")
        response_kind = turn.get("response_kind") or turn.get("kind")

        async with self._connection_lock:
            connection = self._require_connection()
            if not turn_id:
                raise ValueError("turn must contain a non-empty id or turn_id")
            if question is None or not response_kind:
                raise ValueError("turn must contain question and response_kind")
            try:
                await connection.execute("BEGIN IMMEDIATE")
                session_cursor = await connection.execute(
                    "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
                )
                if await session_cursor.fetchone() is None:
                    raise ValueError(f"Session does not exist: {session_id}")
                owner_cursor = await connection.execute(
                    "SELECT session_id FROM turns WHERE id = ?", (turn_id,)
                )
                owner = await owner_cursor.fetchone()
                if owner is not None and owner["session_id"] != session_id:
                    raise ValueError(f"Turn belongs to another session: {turn_id}")

                timestamp = _utc_now()
                await connection.execute(
                    """
                    INSERT INTO turns (
                        id, session_id, question, response_kind, answer, sql,
                        result_preview_json, chart_json, confidence, error_json,
                        trace_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        question = excluded.question,
                        response_kind = excluded.response_kind,
                        answer = excluded.answer,
                        sql = excluded.sql,
                        result_preview_json = excluded.result_preview_json,
                        chart_json = excluded.chart_json,
                        confidence = excluded.confidence,
                        error_json = excluded.error_json,
                        trace_json = excluded.trace_json
                    """,
                    (
                        turn_id,
                        session_id,
                        question,
                        response_kind,
                        turn.get("answer"),
                        turn.get("sql"),
                        _dump_json(turn.get("result_preview")),
                        _dump_json(turn.get("chart")),
                        turn.get("confidence"),
                        _dump_json(turn.get("error")),
                        _dump_json(turn.get("trace", [])),
                        timestamp,
                    ),
                )
                await connection.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?",
                    (timestamp, session_id),
                )
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise
        return str(turn_id)

    async def CreateClarification(
        self, turn_id: str, prompt: str, options: list[dict[str, Any]]
    ) -> dict[str, Any]:
        clarification_id = str(uuid.uuid4())
        async with self._connection_lock:
            connection = self._require_connection()
            timestamp = _utc_now()
            try:
                await connection.execute("BEGIN IMMEDIATE")
                turn_cursor = await connection.execute(
                    "SELECT 1 FROM turns WHERE id = ?", (turn_id,)
                )
                if await turn_cursor.fetchone() is None:
                    raise ValueError(f"Turn does not exist: {turn_id}")
                existing_cursor = await connection.execute(
                    "SELECT 1 FROM clarifications WHERE turn_id = ?", (turn_id,)
                )
                if await existing_cursor.fetchone() is not None:
                    raise ValueError(f"Clarification already exists for turn: {turn_id}")
                await connection.execute(
                    """
                    INSERT INTO clarifications (
                        id, turn_id, prompt, options_json, resolution_json,
                        status, created_at, resolved_at
                    ) VALUES (?, ?, ?, ?, NULL, 'pending', ?, NULL)
                    """,
                    (clarification_id, turn_id, prompt, _dump_json(options), timestamp),
                )
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise
        return {
            "id": clarification_id,
            "turn_id": turn_id,
            "prompt": prompt,
            "options": options,
            "resolution": None,
            "status": "pending",
            "created_at": timestamp,
            "resolved_at": None,
        }

    async def ResolveClarification(
        self, session_id: str, clarification_id: str, resolution: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        async with self._connection_lock:
            connection = self._require_connection()
            resolved_at = _utc_now()
            try:
                await connection.execute("BEGIN IMMEDIATE")
                cursor = await connection.execute(
                    """
                    UPDATE clarifications
                    SET resolution_json = ?, status = 'resolved', resolved_at = ?
                    WHERE id = ?
                      AND status = 'pending'
                      AND EXISTS (
                          SELECT 1 FROM turns
                          WHERE turns.id = clarifications.turn_id
                            AND turns.session_id = ?
                      )
                    """,
                    (
                        _dump_json(resolution),
                        resolved_at,
                        clarification_id,
                        session_id,
                    ),
                )
                if cursor.rowcount == 0:
                    await connection.commit()
                    return None
                record_cursor = await connection.execute(
                    "SELECT * FROM clarifications WHERE id = ?", (clarification_id,)
                )
                record = await record_cursor.fetchone()
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise
        return self._clarification_from_row(record)

    @staticmethod
    def _clarification_from_row(row: aiosqlite.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "turn_id": row["turn_id"],
            "prompt": row["prompt"],
            "options": _load_json(row["options_json"]),
            "resolution": _load_json(row["resolution_json"]),
            "status": row["status"],
            "created_at": row["created_at"],
            "resolved_at": row["resolved_at"],
        }

    @classmethod
    def _turn_from_row(cls, row: aiosqlite.Row) -> dict[str, Any]:
        clarification = None
        if row["clarification_id"] is not None:
            clarification = {
                "id": row["clarification_id"],
                "turn_id": row["id"],
                "prompt": row["clarification_prompt"],
                "options": _load_json(row["options_json"]),
                "resolution": _load_json(row["resolution_json"]),
                "status": row["clarification_status"],
                "created_at": row["clarification_created_at"],
                "resolved_at": row["resolved_at"],
            }
        return {
            "id": row["id"],
            "question": row["question"],
            "response_kind": row["response_kind"],
            "answer": row["answer"],
            "sql": row["sql"],
            "result_preview": _load_json(row["result_preview_json"]),
            "chart": _load_json(row["chart_json"]),
            "confidence": row["confidence"],
            "error": _load_json(row["error_json"]),
            "trace": _load_json(row["trace_json"]),
            "created_at": row["created_at"],
            "clarification": clarification,
        }
