"""Deterministic schema catalog and change detection."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, text


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def BuildSqliteCatalog(database_path: str | Path) -> dict[str, Any]:
    """Read a complete, stable catalog without opening the database writable."""
    resolved = Path(database_path).resolve()
    connection = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True, timeout=3)
    connection.row_factory = sqlite3.Row
    try:
        table_rows = connection.execute(
            """SELECT name, sql FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"""
        ).fetchall()
        tables: list[dict[str, Any]] = []
        for table_row in table_rows:
            table_name = str(table_row["name"])
            quoted = _quote_identifier(table_name)
            columns = [
                {
                    "name": row["name"],
                    "type": row["type"] or "TEXT",
                    "nullable": not bool(row["notnull"]),
                    "default": row["dflt_value"],
                    "primary_key_position": int(row["pk"]),
                }
                for row in connection.execute(f"PRAGMA table_info({quoted})").fetchall()
            ]
            foreign_keys = [
                {
                    "id": int(row["id"]),
                    "sequence": int(row["seq"]),
                    "referenced_table": row["table"],
                    "from_column": row["from"],
                    "to_column": row["to"],
                    "on_update": row["on_update"],
                    "on_delete": row["on_delete"],
                }
                for row in connection.execute(f"PRAGMA foreign_key_list({quoted})").fetchall()
            ]
            indexes = []
            for index_row in connection.execute(f"PRAGMA index_list({quoted})").fetchall():
                index_name = str(index_row["name"])
                index_quoted = _quote_identifier(index_name)
                index_columns = [
                    row["name"]
                    for row in connection.execute(f"PRAGMA index_info({index_quoted})").fetchall()
                    if row["name"] is not None
                ]
                indexes.append({
                    "name": index_name,
                    "unique": bool(index_row["unique"]),
                    "origin": index_row["origin"],
                    "partial": bool(index_row["partial"]),
                    "columns": index_columns,
                })
            indexes.sort(key=lambda item: item["name"])
            tables.append({
                "name": table_name,
                "ddl": table_row["sql"] or "",
                "columns": columns,
                "primary_key": [
                    item["name"]
                    for item in sorted(columns, key=lambda item: item["primary_key_position"])
                    if item["primary_key_position"]
                ],
                "foreign_keys": foreign_keys,
                "indexes": indexes,
            })
    finally:
        connection.close()

    payload: dict[str, Any] = {
        "dialect": "sqlite",
        "tables": tables,
        "table_count": len(tables),
        "column_count": sum(len(table["columns"]) for table in tables),
        "index_count": sum(len(table["indexes"]) for table in tables),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["fingerprint"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def BuildSqlAlchemyCatalog(db_url: str, dialect: str, schema: str | None = None) -> dict[str, Any]:
    """Read a stable catalog from an external SQL database without reading table data."""
    engine = create_engine(db_url, pool_pre_ping=True, pool_size=1, max_overflow=0, pool_timeout=5)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            inspector = inspect(connection)
            table_names = sorted(inspector.get_table_names(schema=schema))
            tables: list[dict[str, Any]] = []
            for table_name in table_names:
                columns_raw = inspector.get_columns(table_name, schema=schema)
                pk_raw = inspector.get_pk_constraint(table_name, schema=schema) or {}
                pk_columns = [str(item) for item in pk_raw.get("constrained_columns", [])]
                pk_positions = {name: index + 1 for index, name in enumerate(pk_columns)}
                columns = [
                    {
                        "name": str(column["name"]),
                        "type": str(column.get("type") or "TEXT"),
                        "nullable": bool(column.get("nullable", True)),
                        "default": column.get("default"),
                        "primary_key_position": pk_positions.get(str(column["name"]), 0),
                    }
                    for column in columns_raw
                ]
                foreign_keys = []
                for index, foreign_key in enumerate(inspector.get_foreign_keys(table_name, schema=schema) or []):
                    constrained = foreign_key.get("constrained_columns") or []
                    referred = foreign_key.get("referred_columns") or []
                    for sequence, from_column in enumerate(constrained):
                        foreign_keys.append({
                            "id": index,
                            "sequence": sequence,
                            "referenced_table": foreign_key.get("referred_table"),
                            "from_column": from_column,
                            "to_column": referred[sequence] if sequence < len(referred) else None,
                            "on_update": foreign_key.get("options", {}).get("onupdate"),
                            "on_delete": foreign_key.get("options", {}).get("ondelete"),
                        })
                indexes = [
                    {
                        "name": str(index.get("name") or ""),
                        "unique": bool(index.get("unique", False)),
                        "origin": "database",
                        "partial": False,
                        "columns": [str(column) for column in index.get("column_names") or [] if column],
                    }
                    for index in (inspector.get_indexes(table_name, schema=schema) or [])
                ]
                indexes.sort(key=lambda item: item["name"])
                tables.append({
                    "name": table_name,
                    "ddl": "",
                    "columns": columns,
                    "primary_key": pk_columns,
                    "foreign_keys": foreign_keys,
                    "indexes": indexes,
                })
    finally:
        engine.dispose()

    payload: dict[str, Any] = {
        "dialect": "postgres" if dialect == "postgresql" else dialect,
        "tables": tables,
        "table_count": len(tables),
        "column_count": sum(len(table["columns"]) for table in tables),
        "index_count": sum(len(table["indexes"]) for table in tables),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    payload["fingerprint"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def DiffCatalog(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    """Return stable object-level changes between two schema snapshots."""
    if previous is None:
        return {
            "changed": False,
            "initial_sync": True,
            "tables_added": [],
            "tables_removed": [],
            "tables_changed": [],
        }

    previous_tables = {item["name"]: item for item in previous.get("tables", [])}
    current_tables = {item["name"]: item for item in current.get("tables", [])}
    added = sorted(current_tables.keys() - previous_tables.keys())
    removed = sorted(previous_tables.keys() - current_tables.keys())
    changed = sorted(
        name
        for name in current_tables.keys() & previous_tables.keys()
        if current_tables[name] != previous_tables[name]
    )
    return {
        "changed": bool(added or removed or changed),
        "initial_sync": False,
        "tables_added": added,
        "tables_removed": removed,
        "tables_changed": changed,
    }
