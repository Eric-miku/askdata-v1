"""Lightweight SQLite executor — validates with SQLValidator, preserves model's LIMIT, adds safety cap only when missing."""

import re
import sqlite3

from askdata.db.validator import SQLValidator


_validator = SQLValidator(dialect="sqlite")
_select_only = re.compile(r"^\s*(SELECT|WITH|UNION)\b", re.I)
_has_limit = re.compile(r"\blimit\b", re.I)
_safety_cap = 100


def Execute(sql: str, database_path: str) -> dict:
    """Validate and execute a SELECT query against a SQLite database.

    Preserves the model's explicit LIMIT. Adds a safety cap only when no LIMIT is present.
    Returns {"success": True, "sql": <executed_sql>, "columns": [...], "rows": [...]}
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

    if not _has_limit.search(normalized):
        normalized = f"{normalized.rstrip(';').strip()} LIMIT {_safety_cap}"

    try:
        connection = sqlite3.connect(database_path)
        try:
            connection.row_factory = sqlite3.Row
            cursor = connection.execute(normalized)
            columns = [item[0] for item in cursor.description or []]
            rows = [dict(row) for row in cursor.fetchall()]
            return {"success": True, "sql": normalized, "columns": columns, "rows": rows}
        finally:
            connection.close()
    except Exception as exc:
        return {"success": False, "sql": normalized, "error": str(exc)}
