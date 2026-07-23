"""SQLite adapter used by the V2 ReAct SQL runner."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from askdata.db.adapters.base import DatabaseAdapter
from askdata.db.error_normalizer import NormalizeDatabaseError
from askdata.db.validator import SQLValidator, ValidationResult


_select_only = re.compile(r"^\s*(SELECT|WITH|UNION)\b", re.I)


class SQLiteAdapter(DatabaseAdapter):
    dialect = "sqlite"

    def __init__(self, database_path: str):
        self.database_path = database_path
        self.validator = SQLValidator(dialect=self.dialect)

    def Validate(self, sql: str) -> ValidationResult:
        return self.validator.validate(sql)

    def Execute(self, sql: str, *, preview_limit: int = 100) -> dict:
        cleaned = (sql or "").strip().rstrip(";")
        if not cleaned:
            return {"success": False, "sql": sql or "", "error": "SQL is empty", "error_code": "syntax_error"}

        validation = self.Validate(cleaned)
        if not validation.is_valid:
            return {
                "success": False,
                "sql": cleaned,
                "error": validation.reason or "SQL validation failed",
                "error_code": "syntax_error",
            }

        normalized = validation.normalized_sql or cleaned
        if not _select_only.search(normalized):
            return {
                "success": False,
                "sql": normalized,
                "error": "Only SELECT / WITH / UNION queries are allowed",
                "error_code": "syntax_error",
            }

        path = Path(self.database_path).expanduser().resolve()
        if not path.is_file():
            return {
                "success": False,
                "sql": normalized,
                "error": f"SQLite database does not exist: {path}",
                "error_code": "database_error",
            }

        try:
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                connection.row_factory = sqlite3.Row
                cursor = connection.execute(normalized)
                columns = [item[0] for item in cursor.description or []]
                preview = cursor.fetchmany(preview_limit + 1)
                rows = [dict(row) for row in preview[:preview_limit]]
                return {
                    "success": True,
                    "sql": normalized,
                    "columns": columns,
                    "rows": rows,
                    "truncated": len(preview) > preview_limit,
                }
            finally:
                connection.close()
        except Exception as exc:
            normalized_error = NormalizeDatabaseError(self.dialect, exc)
            return {
                "success": False,
                "sql": normalized,
                "error": normalized_error.message,
                "error_code": normalized_error.code,
            }

    def IntrospectSchema(self) -> dict:
        path = Path(self.database_path).expanduser().resolve()
        if not path.is_file():
            return {"tables": []}
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            tables = []
            cursor = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
            for (table_name,) in cursor.fetchall():
                quoted = '"' + table_name.replace('"', '""') + '"'
                columns = [
                    {"name": row[1], "type": row[2] or "TEXT", "primary_key": bool(row[5])}
                    for row in connection.execute(f"PRAGMA table_info({quoted})").fetchall()
                ]
                tables.append({"table_name": table_name, "columns": columns})
            return {"tables": tables}
        finally:
            connection.close()
