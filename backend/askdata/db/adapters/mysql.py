"""MySQL database adapter for registered AskData databases."""

from __future__ import annotations

import re

from sqlalchemy import text

from askdata.db.adapters.base import DatabaseAdapter
from askdata.db.error_normalizer import NormalizeDatabaseError
from askdata.db.validator import SQLValidator, ValidationResult


_select_only = re.compile(r"^\s*(SELECT|WITH|UNION)\b", re.I)


class MySQLAdapter(DatabaseAdapter):
    dialect = "mysql"

    def __init__(self, url: str = "", *, engine=None):
        self.url = url
        self._engine = engine
        self.validator = SQLValidator(dialect=self.dialect)

    @property
    def engine(self):
        if self._engine is None:
            from sqlalchemy import create_engine

            if not self.url:
                raise ValueError("MySQLAdapter requires url or injected engine")
            self._engine = create_engine(self.url, pool_pre_ping=True)
        return self._engine

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
        try:
            with self.engine.connect() as connection:
                result = connection.execute(text(normalized))
                columns = list(result.keys())
                preview = result.fetchmany(preview_limit + 1)
                rows = [_row_to_dict(row) for row in preview[:preview_limit]]
                return {
                    "success": True,
                    "sql": normalized,
                    "columns": columns,
                    "rows": rows,
                    "truncated": len(preview) > preview_limit,
                }
        except Exception as exc:
            normalized_error = NormalizeDatabaseError(self.dialect, exc)
            return {
                "success": False,
                "sql": normalized,
                "error": normalized_error.message,
                "error_code": normalized_error.code,
            }

    def IntrospectSchema(self) -> dict:
        query = """
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
        ORDER BY table_name, ordinal_position
        """
        tables: dict[str, list[dict]] = {}
        with self.engine.connect() as connection:
            result = connection.execute(text(query))
            for row in result.fetchall():
                item = _row_to_dict(row)
                tables.setdefault(item["table_name"], []).append({
                    "name": item["column_name"],
                    "type": item["data_type"],
                })
        return {
            "tables": [
                {"table_name": table_name, "columns": columns}
                for table_name, columns in tables.items()
            ]
        }


def _row_to_dict(row) -> dict:
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    return dict(row)
