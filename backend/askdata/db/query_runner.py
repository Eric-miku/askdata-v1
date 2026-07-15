"""Lightweight read-only SQLite executor with a bounded result preview."""

import re
import sqlite3
from pathlib import Path

from askdata.db.validator import SQLValidator


_validator = SQLValidator(dialect="sqlite")
_select_only = re.compile(r"^\s*(SELECT|WITH|UNION)\b", re.I)
_PREVIEW_ROW_LIMIT = 100


def Execute(sql: str, database_path: str) -> dict:
    """Validate and execute a SELECT query against a SQLite database.

    Preserves the model's SQL and bounds the returned preview without rewriting LIMIT.
    Returns {"success": True, "sql": <executed_sql>, "columns": [...],
    "rows": [...], "truncated": bool}
    or {"success": False, "sql": <attempted_sql>, "error": "..."}.
    """
    cleaned = (sql or "").strip().rstrip(";")
    if not cleaned:
        return {"success": False, "sql": sql or "", "error": "SQL is empty"}

    validation = _validator.validate(cleaned)
    if not validation.is_valid:
        return {"success": False, "sql": cleaned, "error": validation.reason or "SQL validation failed"}

    normalized = validation.normalized_sql or cleaned
    if not _select_only.search(normalized):
        return {"success": False, "sql": normalized, "error": "Only SELECT / WITH / UNION queries are allowed"}

    path = Path(database_path).expanduser().resolve()
    if not path.is_file():
        return {"success": False, "sql": normalized, "error": f"SQLite database does not exist: {path}"}

    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            connection.row_factory = sqlite3.Row
            cursor = connection.execute(normalized)
            columns = [item[0] for item in cursor.description or []]
            preview = cursor.fetchmany(_PREVIEW_ROW_LIMIT + 1)
            rows = [dict(row) for row in preview[:_PREVIEW_ROW_LIMIT]]
            return {
                "success": True,
                "sql": normalized,
                "columns": columns,
                "rows": rows,
                "truncated": len(preview) > _PREVIEW_ROW_LIMIT,
            }
        finally:
            connection.close()
    except Exception as exc:
        return {"success": False, "sql": normalized, "error": str(exc)}
