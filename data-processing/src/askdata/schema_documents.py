from __future__ import annotations

from collections import defaultdict
from typing import Any
from urllib.parse import quote


def BuildSchemaDocuments(schemas: dict[str, dict[str, Any]] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build stable table and column documents for schema embedding."""
    schema_items = schemas.values() if isinstance(schemas, dict) else schemas
    documents: list[dict[str, Any]] = []
    for schema in sorted(schema_items, key=lambda item: item.get("database_id", "")):
        database_id = str(schema.get("database_id", ""))
        foreign_keys = _ForeignKeysByTable(schema.get("foreign_keys", []))
        for table in sorted(schema.get("tables", []), key=lambda item: item.get("table_name", "")):
            documents.append(_BuildTableDocument(database_id, table, foreign_keys))
            for column in sorted(table.get("columns", []), key=lambda item: item.get("column_name", "")):
                documents.append(_BuildColumnDocument(database_id, table, column, foreign_keys))
    return documents


def _BuildTableDocument(database_id: str, table: dict[str, Any], foreign_keys: dict[str, list[str]]) -> dict[str, Any]:
    table_name = str(table.get("table_name", ""))
    display_name = str(table.get("display_name") or table_name)
    column_names = [
        _ColumnSummary(column)
        for column in table.get("columns", [])
    ]
    text_parts = [
        f"Database: {database_id}.",
        f"Table: {table_name}.",
        f"Business table name: {display_name}.",
    ]
    if table.get("row_count") is not None:
        text_parts.append(f"Row count: {table['row_count']}.")
    if column_names:
        text_parts.append("Columns: " + "; ".join(column_names) + ".")
    if foreign_keys.get(table_name):
        text_parts.append("Foreign keys: " + "; ".join(foreign_keys[table_name]) + ".")
    return {
        "id": f"schema://{_Quote(database_id)}/table/{_Quote(table_name)}",
        "database_id": database_id,
        "doc_type": "table",
        "table_name": table_name,
        "column_name": None,
        "data_type": None,
        "display_name": display_name,
        "is_primary_key": None,
        "row_count": table.get("row_count"),
        "text": _TrimEmbeddingText(" ".join(text_parts)),
    }


def _BuildColumnDocument(
    database_id: str,
    table: dict[str, Any],
    column: dict[str, Any],
    foreign_keys: dict[str, list[str]],
) -> dict[str, Any]:
    table_name = str(table.get("table_name", ""))
    table_display = str(table.get("display_name") or table_name)
    column_name = str(column.get("column_name", ""))
    display_name = str(column.get("display_name") or column_name)
    data_type = str(column.get("data_type") or "unknown")
    description = str(column.get("description") or "")
    text_parts = [
        f"Database: {database_id}.",
        f"Table: {table_name}.",
        f"Business table name: {table_display}.",
        f"Column: {column_name}.",
        f"Business column name: {display_name}.",
        f"Data type: {data_type}.",
    ]
    if description:
        text_parts.append(f"Description: {description}.")
    if column.get("is_primary_key"):
        text_parts.append("Primary key: true.")
    column_fk_context = [
        item
        for item in foreign_keys.get(table_name, [])
        if f".{column_name} " in item or item.endswith(f".{column_name}")
    ]
    if column_fk_context:
        text_parts.append("Foreign key context: " + "; ".join(column_fk_context) + ".")
    return {
        "id": f"schema://{_Quote(database_id)}/table/{_Quote(table_name)}/column/{_Quote(column_name)}",
        "database_id": database_id,
        "doc_type": "column",
        "table_name": table_name,
        "column_name": column_name,
        "data_type": data_type,
        "display_name": display_name,
        "is_primary_key": bool(column.get("is_primary_key")),
        "row_count": None,
        "text": _TrimEmbeddingText(" ".join(text_parts)),
    }


def _ForeignKeysByTable(foreign_keys: list[dict[str, Any]]) -> dict[str, list[str]]:
    by_table: dict[str, list[str]] = defaultdict(list)
    for key in foreign_keys:
        source_table = str(key.get("source_table") or "")
        source_column = str(key.get("source_column") or "")
        target_table = str(key.get("target_table") or "")
        target_column = str(key.get("target_column") or "")
        if source_table and source_column and target_table and target_column:
            text = f"{source_table}.{source_column} -> {target_table}.{target_column}"
            by_table[source_table].append(text)
            by_table[target_table].append(text)
    return by_table


def _ColumnSummary(column: dict[str, Any]) -> str:
    name = str(column.get("column_name", ""))
    display = str(column.get("display_name") or name)
    data_type = str(column.get("data_type") or "unknown")
    primary = ", primary key" if column.get("is_primary_key") else ""
    description = str(column.get("description") or "")
    details = f"{name} ({data_type}, business name: {display}{primary})"
    if description and description != display:
        details += f" - {description}"
    return details


def _Quote(value: str) -> str:
    return quote(value, safe="")


def _TrimEmbeddingText(text: str, max_words: int = 100, max_chars: int = 900) -> str:
    words = text.split()
    trimmed = text if len(words) <= max_words else " ".join(words[:max_words]) + " ..."
    if len(trimmed) <= max_chars:
        return trimmed
    return trimmed[:max_chars].rstrip() + " ..."
