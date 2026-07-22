"""Read-only SQLite query-plan inspection and deterministic suggestions."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from askdata.core.config import settings
from askdata.data.schema_catalog import BuildSqliteCatalog
from askdata.db.validator import SQLValidator


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _filter_columns(root: exp.Expression, table_name: str, aliases: dict[str, str], table_count: int) -> list[str]:
    columns: set[str] = set()
    for column in root.find_all(exp.Column):
        parent = column.parent
        relevant = False
        while parent is not None:
            if isinstance(parent, (exp.Where, exp.Join)):
                relevant = True
                break
            if isinstance(parent, exp.Select):
                break
            parent = parent.parent
        if not relevant:
            continue
        physical_table = aliases.get(column.table, column.table) if column.table else ""
        if physical_table.casefold() == table_name.casefold() or (not physical_table and table_count == 1):
            columns.add(column.name)
    return sorted(columns)


def ExplainSqliteQuery(sql: str, database_path: str | Path) -> dict[str, Any]:
    validator = SQLValidator(
        dialect="sqlite",
        max_joins=settings.SQL_MAX_JOINS,
        max_subquery_depth=settings.SQL_MAX_SUBQUERY_DEPTH,
    )
    validation = validator.validate(sql)
    if not validation.is_valid:
        return {"success": False, "error_code": "SQL_BLOCKED", "error": validation.reason}

    resolved = Path(database_path).resolve()
    try:
        connection = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True, timeout=3)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(f"EXPLAIN QUERY PLAN {validation.normalized_sql}").fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return {"success": False, "error_code": "DB_ERROR", "error": str(exc)}

    plan = [
        {"id": int(row["id"]), "parent": int(row["parent"]), "detail": str(row["detail"])}
        for row in rows
    ]
    root = sqlglot.parse_one(validation.normalized_sql, read="sqlite")
    table_nodes = list(root.find_all(exp.Table))
    table_names = {table.name for table in table_nodes}
    aliases = {table.alias: table.name for table in table_nodes if table.alias}
    catalog = BuildSqliteCatalog(resolved)
    catalog_tables = {item["name"].casefold(): item for item in catalog["tables"]}
    scanned_tables: set[str] = set()
    for item in plan:
        detail = item["detail"]
        if detail.startswith("SCAN ") and "USING INDEX" not in detail and "USING COVERING INDEX" not in detail:
            token = detail.removeprefix("SCAN ").split()[0].strip('"`[]')
            scanned_tables.add(aliases.get(token, token))

    suggestions: list[dict[str, Any]] = []
    for table_name in sorted(scanned_tables):
        table = catalog_tables.get(table_name.casefold())
        if table is None:
            continue
        columns = _filter_columns(root, table_name, aliases, len(table_names))
        if not columns:
            continue
        existing_prefixes = {
            tuple(str(column).casefold() for column in index["columns"][: len(columns)])
            for index in table["indexes"]
        }
        candidate = tuple(column.casefold() for column in columns)
        if candidate in existing_prefixes:
            continue
        index_name = f"idx_{table_name}_{'_'.join(columns)}"
        quoted_columns = ", ".join(_quote_identifier(column) for column in columns)
        suggestions.append({
            "type": "index_candidate",
            "table": table_name,
            "columns": columns,
            "reason": "执行计划显示筛选或关联条件正在扫描整表",
            "sql": f"CREATE INDEX {_quote_identifier(index_name)} ON {_quote_identifier(table_name)} ({quoted_columns})",
            "automatic": False,
        })

    if any("USE TEMP B-TREE" in item["detail"] for item in plan):
        suggestions.append({
            "type": "temporary_sort",
            "reason": "执行计划使用临时 B-Tree 进行排序或分组，可结合业务查询频率评估复合索引",
            "automatic": False,
        })

    return {
        "success": True,
        "normalized_sql": validation.normalized_sql,
        "plan": plan,
        "suggestions": suggestions[:5],
        "warnings": ["索引建议仅供管理员评估，AskData 不会自动修改数据库索引"],
    }
