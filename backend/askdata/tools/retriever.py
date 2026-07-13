"""Schema index and semantic retriever — loads BIRD database schema, token-matches question keywords to tables/columns, builds structured prompt context with foreign key JOIN hints and per-DB business instructions."""

import json
import re
from pathlib import Path
from typing import Any

from askdata.core.config import settings
from askdata.core.paths import project_path


def GetValue(item: Any, *names: str, default=None):
    for name in names:
        if isinstance(item, dict) and name in item:
            return item[name]
        if hasattr(item, name):
            return getattr(item, name)
    return default


def _Tokens(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z0-9]+", (text or "").lower()))


class BirdSchemaIndex:
    """BIRD-first schema index. TODO: add a Spider adapter when Spider data is finalized."""

    def __init__(self, instructions_dir=None):
        self.databases: dict[str, Any] = {}
        self.instructions_dir = project_path(instructions_dir or settings.BIRD_INSTRUCTIONS_DIR)

    def Build(self, databases: list[Any], instructions_dir=None):
        self.databases = {GetValue(database, "databaseId", "database_id"): database for database in databases}
        if instructions_dir:
            self.instructions_dir = project_path(instructions_dir)
        return self

    def Retrieve(self, database_id: str, question: str) -> dict[str, Any]:
        database = self.databases.get(database_id)
        if not database:
            raise ValueError(f"Unknown BIRD database_id: {database_id}")

        question_tokens = _Tokens(question)
        matched_tables = []
        matched_columns = []
        tables = list(GetValue(database, "tables", default=[]))

        for table in tables:
            table_name = GetValue(table, "tableName", "table_name")
            table_matched = bool(question_tokens & _Tokens(table_name))
            column_matches = []
            for column in GetValue(table, "columns", default=[]):
                column_name = GetValue(column, "columnName", "column_name")
                if question_tokens & _Tokens(column_name):
                    column_matches.append(column)
                    matched_columns.append(self._ColumnDict(table_name, column, "Token match."))
            if table_matched or column_matches:
                matched_tables.append({"table_name": table_name, "reason": "Token match."})

        if not matched_tables:
            matched_tables = [
                {"table_name": GetValue(table, "tableName", "table_name"), "reason": "Included for compact database context."}
                for table in tables[:8]
            ]

        selected_names = {table["table_name"] for table in matched_tables}
        for table in tables:
            table_name = GetValue(table, "tableName", "table_name")
            if table_name not in selected_names:
                continue
            for column in GetValue(table, "columns", default=[]):
                if GetValue(column, "isPrimary", "is_primary", default=False):
                    exists = any(
                        item["table_name"] == table_name and item["column_name"] == GetValue(column, "columnName", "column_name")
                        for item in matched_columns
                    )
                    if not exists:
                        matched_columns.append(self._ColumnDict(table_name, column, "Primary key."))

        matched_joins = []
        for key in GetValue(database, "foreignKeys", "foreign_keys", default=[]):
            left_table = GetValue(key, "leftTable", "left_table")
            right_table = GetValue(key, "rightTable", "right_table")
            if left_table in selected_names or right_table in selected_names:
                matched_joins.append({
                    "left_table": left_table,
                    "left_column": GetValue(key, "leftColumn", "left_column"),
                    "right_table": right_table,
                    "right_column": GetValue(key, "rightColumn", "right_column"),
                })

        schema_prompt = self.BuildSchemaPrompt(database, selected_names, matched_joins)
        return {
            "database_id": database_id,
            "database_path": GetValue(database, "databasePath", "database_path", default=""),
            "matched_tables": matched_tables,
            "matched_columns": matched_columns,
            "matched_joins": matched_joins,
            "schema_prompt": schema_prompt,
        }

    def _ColumnDict(self, table_name: str, column: Any, reason: str) -> dict[str, str]:
        return {
            "table_name": table_name,
            "column_name": GetValue(column, "columnName", "column_name"),
            "column_type": GetValue(column, "columnType", "column_type", default="text"),
            "reason": reason,
        }

    def BuildSchemaPrompt(self, database: Any, selected_names: set[str], joins: list[dict[str, str]]) -> str:
        database_id = GetValue(database, "databaseId", "database_id")
        database_path = GetValue(database, "databasePath", "database_path", default="")
        lines = [f"Database: {database_id}", "Dialect: SQLite"]
        if database_path:
            lines.append(f"SQLite path: {database_path}")
        instructions = self._LoadInstructions(database_id)
        if instructions:
            lines.append(f"\n--- Business Context ---\n{instructions}\n---")
        tables = GetValue(database, "tables", default=[])
        for table in tables:
            table_name = GetValue(table, "tableName", "table_name")
            if selected_names and table_name not in selected_names and len(tables) > 8:
                continue
            columns = ", ".join(
                f"{GetValue(column, 'columnName', 'column_name')} {GetValue(column, 'columnType', 'column_type', default='text')}".strip()
                for column in GetValue(table, "columns", default=[])
            )
            lines.append(f"Table {table_name}({columns})")
        for join in joins:
            lines.append(
                f"Join {join['left_table']}.{join['left_column']} = {join['right_table']}.{join['right_column']}"
            )
        return "\n".join(lines)

    def _LoadInstructions(self, database_id: str) -> str:
        path = self.instructions_dir / f"{database_id}.md"
        if not path.exists():
            return ""
        content = path.read_text(encoding="utf-8")
        parts = []
        business = self._ExtractSection(content, "Business Term Mappings")
        joins = self._ExtractSection(content, "JOIN Patterns")
        if business:
            parts.append(f"Term mappings:\n{business}")
        if joins:
            parts.append(f"JOIN patterns:\n{joins}")
        return "\n\n".join(parts)

    def _ExtractSection(self, content: str, heading: str) -> str:
        collecting = False
        result = []
        for line in content.splitlines():
            stripped = line.strip()
            if heading in stripped:
                collecting = True
                continue
            if collecting and stripped.startswith("##"):
                break
            if collecting and stripped and not stripped.startswith("#") and not stripped.startswith("```"):
                result.append(stripped)
        return "\n".join(result)


class SchemaIndex(BirdSchemaIndex):
    """Neutral alias for the BIRD-first schema index."""


class SemanticRetriever:
    """Loads BIRD processed schemas and returns prompt text for AgentState.schema_context."""

    def __init__(self, processed_dir=None, index: BirdSchemaIndex | None = None):
        base_dir = project_path(processed_dir or settings.BIRD_DATA_DIR)
        self.processed_dir = base_dir if (base_dir / "databases.json").exists() else base_dir / "processed"
        self.index = index

    def Build(self):
        if self.index:
            return self
        path = self.processed_dir / "databases.json"
        if not path.exists():
            raise FileNotFoundError(f"Missing BIRD processed schema file: {path}")
        databases = json.loads(path.read_text(encoding="utf-8"))
        self.index = BirdSchemaIndex().Build(databases)
        return self

    def Retrieve(self, database_id: str, question: str) -> str:
        if not self.index:
            self.Build()
        return self.index.Retrieve(database_id, question)["schema_prompt"]
