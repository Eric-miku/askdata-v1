from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from threading import Lock
from typing import Any


class KnowledgeStore:
    """Small durable SQLite store for terms, metrics, aliases and mappings."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or Path(os.getenv("ASKDATA_STATE_DIR", ".checkpoints")) / "knowledge.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS knowledge_entries (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, standard_name TEXT NOT NULL,
                    definition TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '',
                    scope TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'draft',
                    aliases TEXT NOT NULL DEFAULT '[]', mappings TEXT NOT NULL DEFAULT '[]',
                    formula TEXT NOT NULL DEFAULT '', aggregation TEXT NOT NULL DEFAULT '',
                    unit TEXT NOT NULL DEFAULT '', time_field TEXT NOT NULL DEFAULT '',
                    examples TEXT NOT NULL DEFAULT '[]', version INTEGER NOT NULL DEFAULT 1,
                    changelog TEXT NOT NULL DEFAULT '', updated_by TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL
                )"""
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_name ON knowledge_entries(standard_name)")
            connection.execute("""CREATE TABLE IF NOT EXISTS knowledge_versions (
                entry_id TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL,
                created_at REAL NOT NULL, PRIMARY KEY(entry_id, version)
            )""")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for field in ("aliases", "mappings", "examples"):
            item[field] = json.loads(item[field] or "[]")
        return item

    def list(self, *, kind: str | None = None, search: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        clauses, values = [], []
        if kind:
            clauses.append("kind = ?"); values.append(kind)
        if status:
            clauses.append("status = ?"); values.append(status)
        if search:
            clauses.append("(standard_name LIKE ? OR definition LIKE ? OR aliases LIKE ?)")
            needle = f"%{search}%"; values.extend([needle, needle, needle])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(f"SELECT * FROM knowledge_entries{where} ORDER BY updated_at DESC", values).fetchall()
        return [self._decode(row) for row in rows]

    def get(self, entry_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM knowledge_entries WHERE id = ?", (entry_id,)).fetchone()
        return self._decode(row) if row else None

    def save(self, payload: dict[str, Any], entry_id: str | None = None, updated_by: str = "local-admin") -> dict[str, Any]:
        now = time.time()
        entry_id = entry_id or str(uuid.uuid4())
        with self._lock, self._connect() as connection:
            existing = connection.execute("SELECT version, status FROM knowledge_entries WHERE id = ?", (entry_id,)).fetchone()
            version = (existing["version"] + 1) if existing else 1
            status = payload.get("status", existing["status"] if existing else "draft")
            values = (
                entry_id, payload["kind"], payload["standard_name"], payload.get("definition", ""), payload.get("category", ""), payload.get("scope", ""), status,
                json.dumps(payload.get("aliases", []), ensure_ascii=False), json.dumps(payload.get("mappings", []), ensure_ascii=False), payload.get("formula", ""), payload.get("aggregation", ""), payload.get("unit", ""), payload.get("time_field", ""), json.dumps(payload.get("examples", []), ensure_ascii=False), version, payload.get("changelog", ""), updated_by, now,
            )
            if existing:
                old = connection.execute("SELECT * FROM knowledge_entries WHERE id = ?", (entry_id,)).fetchone()
                connection.execute("INSERT OR REPLACE INTO knowledge_versions VALUES (?, ?, ?, ?)", (entry_id, old["version"], json.dumps(self._decode(old), ensure_ascii=False), now))
            connection.execute(
                """INSERT INTO knowledge_entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, standard_name=excluded.standard_name, definition=excluded.definition,
                category=excluded.category, scope=excluded.scope, status=excluded.status, aliases=excluded.aliases, mappings=excluded.mappings,
                formula=excluded.formula, aggregation=excluded.aggregation, unit=excluded.unit, time_field=excluded.time_field,
                examples=excluded.examples, version=excluded.version, changelog=excluded.changelog, updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
                values,
            )
        return self.get(entry_id)  # type: ignore[return-value]

    def rollback(self, entry_id: str, version: int, updated_by: str = "api-user") -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM knowledge_versions WHERE entry_id = ? AND version = ?", (entry_id, version)).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload"])
        payload["status"] = "draft"
        payload["changelog"] = f"回滚到版本 {version}"
        return self.save(payload, entry_id=entry_id, updated_by=updated_by)

    def list_versions(self, entry_id: str) -> list[dict[str, Any]]:
        current = self.get(entry_id)
        if current is None:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT version, payload, created_at FROM knowledge_versions WHERE entry_id = ? ORDER BY version DESC",
                (entry_id,),
            ).fetchall()
        versions = [{**json.loads(row["payload"]), "archived_at": row["created_at"]} for row in rows]
        return [current, *versions]

    def delete(self, entry_id: str) -> bool:
        with self._lock, self._connect() as connection:
            result = connection.execute("DELETE FROM knowledge_entries WHERE id = ?", (entry_id,))
            connection.execute("DELETE FROM knowledge_versions WHERE entry_id = ?", (entry_id,))
        return result.rowcount > 0


knowledge_store = KnowledgeStore()
