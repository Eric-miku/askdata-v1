from __future__ import annotations

import contextvars
import os
import sqlite3
import time
import uuid
from pathlib import Path
from threading import Lock
from typing import Any, Callable

import sqlglot
from sqlglot import exp


SqlAuthorizer = Callable[[str, str], tuple[bool, str | None] | tuple[bool, str | None, str]]
_sql_authorizer: contextvars.ContextVar[SqlAuthorizer | None] = contextvars.ContextVar("askdata_sql_authorizer", default=None)


def SetSqlAuthorizer(authorizer: SqlAuthorizer):
    return _sql_authorizer.set(authorizer)


def ResetSqlAuthorizer(token) -> None:
    _sql_authorizer.reset(token)


def AuthorizeCurrentSql(sql: str, access_mode: str = "query") -> tuple[bool, str | None]:
    authorizer = _sql_authorizer.get()
    if authorizer is None:
        return True, None
    result = authorizer(sql, access_mode)
    return result[0], result[1]


def PrepareCurrentSql(sql: str, access_mode: str = "query") -> tuple[bool, str | None, str]:
    """Authorize SQL and return the internal statement after policy rewriting."""
    authorizer = _sql_authorizer.get()
    if authorizer is None:
        return True, None, sql
    result = authorizer(sql, access_mode)
    if len(result) == 2:
        return result[0], result[1], sql
    return result


_ROW_FILTER_NODES = (
    exp.And, exp.Or, exp.Not, exp.Paren,
    exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE,
    exp.In, exp.Between, exp.Like, exp.ILike, exp.Is,
    exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod, exp.Neg,
    exp.Column, exp.Identifier, exp.Literal, exp.Null, exp.Boolean,
    exp.Tuple,
)


def ParseRowFilter(value: str, table_name: str) -> exp.Expression:
    """Parse the deliberately small row-policy expression language."""
    raw = value.strip()
    if not raw:
        raise ValueError("行过滤条件不能为空")
    try:
        statements = sqlglot.parse(f"SELECT 1 WHERE {raw}", read="sqlite")
    except Exception as exc:
        raise ValueError("行过滤条件不是有效的 SQLite 表达式") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise ValueError("行过滤条件只允许单个表达式")
    where = statements[0].args.get("where")
    if where is None:
        raise ValueError("行过滤条件不能为空")
    predicate = where.this
    for node in predicate.walk():
        if not isinstance(node, _ROW_FILTER_NODES):
            raise ValueError(f"行过滤条件不允许使用 {type(node).__name__}")
        if isinstance(node, exp.Column) and node.table and node.table.casefold() != table_name.casefold():
            raise ValueError("行过滤条件只能引用当前表字段")
    return predicate


def NormalizeRowFilter(value: str | None, table_name: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    if not table_name:
        raise ValueError("行级权限必须指定表名")
    return ParseRowFilter(value, table_name).sql(dialect="sqlite")


class PermissionStore:
    """Allow-list policies for users, data sources, tables, fields and export."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or Path(os.getenv("ASKDATA_STATE_DIR", ".checkpoints")) / "permissions.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        with self._connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS permission_policies (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, database_id TEXT NOT NULL,
                table_name TEXT, field_name TEXT, can_query INTEGER NOT NULL DEFAULT 1,
                can_export INTEGER NOT NULL DEFAULT 1, row_filter TEXT, created_at REAL NOT NULL,
                UNIQUE(user_id, database_id, table_name, field_name)
            )""")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(permission_policies)")}
            if "row_filter" not in columns:
                connection.execute("ALTER TABLE permission_policies ADD COLUMN row_filter TEXT")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _item(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["can_query"] = bool(item["can_query"])
        item["can_export"] = bool(item["can_export"])
        return item

    def configured(self) -> bool:
        with self._connect() as connection:
            return connection.execute("SELECT EXISTS(SELECT 1 FROM permission_policies)").fetchone()[0] == 1

    def list(self, user_id: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as connection:
            if user_id:
                rows = connection.execute("SELECT * FROM permission_policies WHERE user_id=? ORDER BY database_id, table_name, field_name", (user_id,)).fetchall()
            else:
                rows = connection.execute("SELECT * FROM permission_policies ORDER BY user_id, database_id, table_name, field_name").fetchall()
        return [self._item(row) for row in rows]

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        policy_id = payload.get("id") or str(uuid.uuid4())
        table_name = payload.get("table_name") or None
        field_name = payload.get("field_name") or None
        row_filter = NormalizeRowFilter(payload.get("row_filter"), table_name)
        if row_filter and field_name:
            raise ValueError("行过滤条件应配置在表级策略，不应指定字段")
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM permission_policies WHERE user_id=? AND database_id=? AND table_name IS ? AND field_name IS ?",
                (payload["user_id"], payload["database_id"], table_name, field_name),
            ).fetchone()
            if existing:
                connection.execute(
                    "UPDATE permission_policies SET can_query=?, can_export=?, row_filter=? WHERE id=?",
                    (int(payload.get("can_query", True)), int(payload.get("can_export", True)), row_filter, existing["id"]),
                )
                policy_id = existing["id"]
            else:
                connection.execute(
                    """INSERT INTO permission_policies(
                        id, user_id, database_id, table_name, field_name,
                        can_query, can_export, row_filter, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (policy_id, payload["user_id"], payload["database_id"], table_name, field_name,
                     int(payload.get("can_query", True)), int(payload.get("can_export", True)), row_filter, time.time()),
                )
            row = connection.execute("SELECT * FROM permission_policies WHERE id=?", (policy_id,)).fetchone()
        return self._item(row)

    def delete(self, policy_id: str) -> bool:
        with self._lock, self._connect() as connection:
            return connection.execute("DELETE FROM permission_policies WHERE id=?", (policy_id,)).rowcount > 0

    def _policies(self, user_id: str, database_id: str) -> list[dict[str, Any]]:
        return [item for item in self.list(user_id) if item["database_id"] == database_id]

    def database_allowed(self, user_id: str, database_id: str, access_mode: str = "query") -> bool:
        if not self.configured():
            return True
        flag = "can_export" if access_mode == "export" else "can_query"
        return any(item[flag] for item in self._policies(user_id, database_id))

    def table_allowed(self, user_id: str, database_id: str, table_name: str, access_mode: str = "query") -> bool:
        if not self.configured():
            return True
        flag = "can_export" if access_mode == "export" else "can_query"
        return any(item[flag] and (item["table_name"] is None or item["table_name"].casefold() == table_name.casefold()) for item in self._policies(user_id, database_id))

    def field_allowed(self, user_id: str, database_id: str, table_name: str, field_name: str, access_mode: str = "query") -> bool:
        if not self.configured():
            return True
        flag = "can_export" if access_mode == "export" else "can_query"
        policies = self._policies(user_id, database_id)
        if any(item[flag] and item["table_name"] is None for item in policies):
            return True
        table_policies = [item for item in policies if item["table_name"] and item["table_name"].casefold() == table_name.casefold()]
        field_policies = [item for item in table_policies if item["field_name"]]
        if not field_policies:
            return any(item[flag] for item in table_policies)
        return any(item[flag] and item["field_name"].casefold() == field_name.casefold() for item in field_policies)

    def authorize_sql(self, user_id: str, database_id: str, sql: str, access_mode: str = "query") -> tuple[bool, str | None]:
        if not self.configured():
            return True, None
        if not self.database_allowed(user_id, database_id, access_mode):
            return False, f"用户无权访问数据源 {database_id}"
        try:
            root = sqlglot.parse_one(sql, read="sqlite")
        except Exception:
            return False, "SQL 无法进行权限解析"
        cte_names = {cte.alias_or_name.casefold() for cte in root.find_all(exp.CTE)}
        table_nodes = [
            table for table in root.find_all(exp.Table)
            if table.name.casefold() not in cte_names
        ]
        tables = {table.name for table in table_nodes}
        aliases = {table.alias: table.name for table in table_nodes if table.alias}
        for table in tables:
            if not self.table_allowed(user_id, database_id, table, access_mode):
                return False, f"用户无权访问表 {table}"
        restricted_fields = [
            item for item in self._policies(user_id, database_id)
            if item["field_name"] and item["can_export" if access_mode == "export" else "can_query"]
        ]
        if restricted_fields:
            if any(
                isinstance(projection, exp.Star)
                for select in root.find_all(exp.Select)
                for projection in select.expressions
            ):
                return False, "字段级权限不允许使用 SELECT *"
            for column in root.find_all(exp.Column):
                if isinstance(column.this, exp.Star):
                    return False, "字段级权限不允许使用 SELECT *"
                physical_table = aliases.get(column.table, column.table) if column.table else ""
                candidates = {physical_table} if physical_table else tables
                if not any(self.field_allowed(user_id, database_id, candidate, column.name, access_mode) for candidate in candidates):
                    return False, f"用户无权访问字段 {column.name}"
        return True, None

    def prepare_sql(
        self, user_id: str, database_id: str, sql: str, access_mode: str = "query"
    ) -> tuple[bool, str | None, str]:
        """Authorize and apply table-scoped row filters without exposing them externally."""
        allowed, reason = self.authorize_sql(user_id, database_id, sql, access_mode)
        if not allowed:
            return False, reason, sql
        flag = "can_export" if access_mode == "export" else "can_query"
        filters = {
            item["table_name"].casefold(): item["row_filter"]
            for item in self._policies(user_id, database_id)
            if item[flag] and item["table_name"] and not item["field_name"] and item.get("row_filter")
        }
        if not filters:
            return True, None, sql
        try:
            root = sqlglot.parse_one(sql, read="sqlite")
            cte_names = {cte.alias_or_name.casefold() for cte in root.find_all(exp.CTE)}
            for table in list(root.find_all(exp.Table)):
                table_key = table.name.casefold()
                if table_key in cte_names or table_key not in filters:
                    continue
                source = table.copy()
                source.set("alias", None)
                predicate = ParseRowFilter(filters[table_key], table.name)
                secured = exp.select("*").from_(source).where(predicate).subquery(table.alias_or_name)
                table.replace(secured)
            return True, None, root.sql(dialect="sqlite")
        except Exception:
            return False, "SQL 无法应用行级权限", sql


permission_store = PermissionStore()
