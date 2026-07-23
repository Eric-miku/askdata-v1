"""Database adapter registry keyed by database_id."""

from __future__ import annotations

import json
from pathlib import Path

from askdata.db.adapters.base import DatabaseAdapter
from askdata.db.adapters.mysql import MySQLAdapter
from askdata.db.adapters.postgresql import PostgreSQLAdapter
from askdata.db.adapters.sqlite import SQLiteAdapter


_REGISTRY: dict[str, DatabaseAdapter] = {}
_CONFIG_LOADED = False


def Register(database_id: str, adapter: DatabaseAdapter) -> None:
    key = (database_id or "").strip()
    if not key:
        raise ValueError("database_id must not be blank")
    _REGISTRY[key] = adapter


def Resolve(database_id_or_path: str) -> DatabaseAdapter:
    global _CONFIG_LOADED
    key = (database_id_or_path or "").strip()
    if key in _REGISTRY:
        return _REGISTRY[key]
    if not _CONFIG_LOADED:
        LoadConfiguredAdapters()
        _CONFIG_LOADED = True
        if key in _REGISTRY:
            return _REGISTRY[key]
    return SQLiteAdapter(key)


def ClearRegistryForTests() -> None:
    global _CONFIG_LOADED
    _REGISTRY.clear()
    _CONFIG_LOADED = False


def LoadFromJson(path: str | Path) -> int:
    config_path = Path(path)
    if not config_path.exists():
        return 0
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("database connections config must be a JSON object")
    count = 0
    for database_id, config in payload.items():
        if not isinstance(config, dict):
            raise ValueError(f"database config for {database_id} must be an object")
        dialect = str(config.get("dialect") or "").casefold()
        url = str(config.get("url") or "")
        if dialect == "sqlite":
            adapter = SQLiteAdapter(url)
        elif dialect == "mysql":
            adapter = MySQLAdapter(url)
        elif dialect in {"postgresql", "postgres"}:
            adapter = PostgreSQLAdapter(url)
        else:
            raise ValueError(f"unsupported database dialect for {database_id}: {dialect}")
        Register(str(database_id), adapter)
        count += 1
    return count


def LoadConfiguredAdapters() -> int:
    from askdata.core.config import settings
    from askdata.core.paths import project_path

    return LoadFromJson(project_path(settings.DATABASE_CONNECTIONS_PATH))
