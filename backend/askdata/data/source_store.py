"""Persistent lifecycle metadata for locally managed SQLite data sources."""

from __future__ import annotations

import sqlite3
import os
import time
import json
from pathlib import Path
from threading import Lock
from typing import Any

from askdata.data.schema_catalog import BuildSqliteCatalog, DiffCatalog


class DataSourceStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or Path(os.getenv("ASKDATA_STATE_DIR", ".checkpoints")) / "datasources.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        with self._connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS data_sources (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'sqlite',
                path TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                health TEXT NOT NULL DEFAULT 'unknown', last_error TEXT,
                table_count INTEGER NOT NULL DEFAULT 0, last_tested_at REAL,
                last_synced_at REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS schema_catalogs (
                source_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                previous_fingerprint TEXT, catalog_json TEXT NOT NULL,
                change_summary_json TEXT NOT NULL, synced_at REAL NOT NULL
            )""")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _item(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        return item

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM data_sources ORDER BY name, id").fetchall()
        return [self._with_catalog(self._item(row)) for row in rows]

    def get(self, source_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM data_sources WHERE id = ?", (source_id,)).fetchone()
        return self._with_catalog(self._item(row)) if row else None

    def _with_catalog(self, source: dict[str, Any]) -> dict[str, Any]:
        snapshot = self.catalog(source["id"])
        source.update({
            "schema_fingerprint": snapshot["fingerprint"] if snapshot else None,
            "schema_changed": snapshot["change_summary"]["changed"] if snapshot else False,
            "schema_change_summary": snapshot["change_summary"] if snapshot else None,
            "index_count": snapshot["catalog"]["index_count"] if snapshot else 0,
        })
        return source

    def save(self, source_id: str, name: str, path: str, enabled: bool = True) -> dict[str, Any]:
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO data_sources(id, name, path, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, path=excluded.path,
                enabled=excluded.enabled, updated_at=excluded.updated_at""",
                (source_id, name, path, int(enabled), now, now),
            )
        return self.get(source_id)  # type: ignore[return-value]

    def set_enabled(self, source_id: str, enabled: bool) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            changed = connection.execute(
                "UPDATE data_sources SET enabled = ?, updated_at = ? WHERE id = ?",
                (int(enabled), time.time(), source_id),
            ).rowcount
        return self.get(source_id) if changed else None

    def check(self, source_id: str) -> dict[str, Any] | None:
        source = self.get(source_id)
        if source is None:
            return None
        health, error, table_count = "healthy", None, 0
        try:
            connection = sqlite3.connect(f"file:{Path(source['path']).resolve()}?mode=ro", uri=True, timeout=3)
            try:
                table_count = connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchone()[0]
            finally:
                connection.close()
        except sqlite3.Error as exc:
            health, error = "unhealthy", str(exc)
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE data_sources SET health=?, last_error=?, table_count=?, last_tested_at=?, updated_at=? WHERE id=?",
                (health, error, table_count, now, now, source_id),
            )
        return self.get(source_id)

    def mark_synced(self, source_id: str) -> dict[str, Any] | None:
        checked = self.check(source_id)
        if checked is None or checked["health"] != "healthy":
            return checked
        previous = self.catalog(source_id)
        catalog = BuildSqliteCatalog(checked["path"])
        change_summary = DiffCatalog(previous["catalog"] if previous else None, catalog)
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO schema_catalogs(
                    source_id, fingerprint, previous_fingerprint, catalog_json,
                    change_summary_json, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    fingerprint=excluded.fingerprint,
                    previous_fingerprint=excluded.previous_fingerprint,
                    catalog_json=excluded.catalog_json,
                    change_summary_json=excluded.change_summary_json,
                    synced_at=excluded.synced_at""",
                (
                    source_id,
                    catalog["fingerprint"],
                    previous["fingerprint"] if previous else None,
                    json.dumps(catalog, ensure_ascii=False, sort_keys=True),
                    json.dumps(change_summary, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
            connection.execute(
                "UPDATE data_sources SET last_synced_at=?, updated_at=? WHERE id=?",
                (now, now, source_id),
            )
        result = self.get(source_id)
        if result is not None:
            result.update({
                "schema_fingerprint": catalog["fingerprint"],
                "schema_changed": change_summary["changed"],
                "schema_change_summary": change_summary,
                "index_count": catalog["index_count"],
            })
        return result

    def catalog(self, source_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM schema_catalogs WHERE source_id = ?", (source_id,)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["catalog"] = json.loads(item.pop("catalog_json"))
        item["change_summary"] = json.loads(item.pop("change_summary_json"))
        return item

    def delete(self, source_id: str) -> bool:
        with self._lock, self._connect() as connection:
            changed = connection.execute("DELETE FROM data_sources WHERE id = ?", (source_id,)).rowcount
            connection.execute("DELETE FROM schema_catalogs WHERE source_id = ?", (source_id,))
            return changed > 0


data_source_store = DataSourceStore()
