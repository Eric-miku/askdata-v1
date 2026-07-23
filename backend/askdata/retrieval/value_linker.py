"""Bounded literal-to-column linking for Text2SQL prompts."""

from __future__ import annotations

import sqlite3
from typing import Iterable, Mapping

from pydantic import BaseModel, Field

from askdata.agent.question_analyzer import QuestionAnalysis, QuestionFilter


class ValueLink(BaseModel):
    value: str
    normalized_value: str
    table: str
    column: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class ValueLinker:
    """Probe likely columns for question literals using bounded exact matches."""

    def __init__(self, max_columns: int = 40) -> None:
        self.max_columns = max_columns

    def Link(
        self,
        question: str,
        retrieval: Mapping,
        analysis: QuestionAnalysis,
    ) -> list[ValueLink]:
        database_path = str(retrieval.get("database_path") or "")
        schema = retrieval.get("schema") or {}
        if not database_path or not schema:
            return []
        candidates = self._CandidateColumns(schema, retrieval.get("matched_tables") or [])
        links: list[ValueLink] = []
        with sqlite3.connect(database_path) as conn:
            for item in analysis.filters:
                for table, column in candidates:
                    if not self._ColumnMatchesKind(column, item.kind):
                        continue
                    normalized = item.normalized or item.raw
                    if self._Exists(conn, table, column, normalized):
                        links.append(
                            ValueLink(
                                value=item.raw,
                                normalized_value=normalized,
                                table=table,
                                column=column,
                                confidence=self._Confidence(column, item),
                                reason=f"Exact {item.kind} value match.",
                            )
                        )
                        break
        return sorted(links, key=lambda link: (-link.confidence, link.table, link.column))

    def _CandidateColumns(self, schema: Mapping[str, Iterable[str]], matched_tables: list[dict]) -> list[tuple[str, str]]:
        matched = [item.get("table_name") for item in matched_tables if item.get("table_name")]
        table_order = [*matched, *[table for table in schema if table not in matched]]
        columns: list[tuple[str, str]] = []
        for table in table_order:
            for column in schema.get(table, []):
                columns.append((table, column))
                if len(columns) >= self.max_columns:
                    return columns
        return columns

    @staticmethod
    def _ColumnMatchesKind(column: str, kind: str) -> bool:
        lowered = column.casefold()
        if kind == "identifier":
            return "id" in lowered or lowered.endswith("code")
        if kind == "number":
            return any(token in lowered for token in ("price", "amount", "count", "consumption", "total"))
        if kind == "date":
            return "date" in lowered or "year" in lowered
        if kind == "text":
            return any(token in lowered for token in ("county", "city", "name", "type", "label", "segment"))
        return False

    @staticmethod
    def _Exists(conn: sqlite3.Connection, table: str, column: str, value: str) -> bool:
        quoted_table = '"' + table.replace('"', '""') + '"'
        quoted_column = '"' + column.replace('"', '""') + '"'
        sql = f"SELECT 1 FROM {quoted_table} WHERE {quoted_column} = ? LIMIT 1"
        try:
            row = conn.execute(sql, (value,)).fetchone()
        except sqlite3.Error:
            return False
        return row is not None

    @staticmethod
    def _Confidence(column: str, item: QuestionFilter) -> float:
        lowered = column.casefold()
        if item.kind == "identifier" and "id" in lowered:
            return 0.95
        if item.kind == "date" and "date" in lowered:
            return 0.95
        if item.kind == "number" and lowered in {"price", "amount", "consumption"}:
            return 0.9
        if item.kind == "text" and lowered in {"county", "city", "school type"}:
            return 0.9
        return 0.75
