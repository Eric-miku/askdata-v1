import sqlite3

import pytest

from askdata.db.error_normalizer import NormalizeDatabaseError


def test_normalizes_sqlite_unknown_table():
    error = NormalizeDatabaseError("sqlite", "no such table: schools")
    assert error.code == "unknown_table"
    assert error.message == "unknown_table: schools"


def test_normalizes_mysql_unknown_table():
    error = NormalizeDatabaseError(
        "mysql", "(1146, \"Table 'demo.schools' doesn't exist\")"
    )
    assert error.code == "unknown_table"
    assert error.message == "unknown_table: schools"


def test_normalizes_postgres_unknown_table():
    error = NormalizeDatabaseError(
        "postgresql", "psycopg.errors.UndefinedTable: relation \"schools\" does not exist"
    )
    assert error.code == "unknown_table"
    assert error.message == "unknown_table: schools"


def test_normalizes_unknown_column_and_syntax_error():
    column = NormalizeDatabaseError("sqlite", "no such column: schools.name")
    syntax = NormalizeDatabaseError("postgresql", "syntax error at or near \"FROM\"")
    assert column.code == "unknown_column"
    assert column.message == "unknown_column: schools.name"
    assert syntax.code == "syntax_error"


def test_database_adapter_abc_rejects_incomplete_subclass():
    from askdata.db.adapters.base import DatabaseAdapter

    class Incomplete(DatabaseAdapter):
        dialect = "broken"

    with pytest.raises(TypeError, match="abstract"):
        Incomplete()


def test_sqlite_adapter_preserves_query_runner_behavior(tmp_path):
    from askdata.db.adapters.sqlite import SQLiteAdapter

    db_path = tmp_path / "demo.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE items(id INTEGER, name TEXT)")
        connection.execute("INSERT INTO items VALUES (1, 'a')")

    result = SQLiteAdapter(str(db_path)).Execute("SELECT id, name FROM items")

    assert result["success"] is True
    assert result["columns"] == ["id", "name"]
    assert result["rows"] == [{"id": 1, "name": "a"}]


def test_query_runner_falls_back_to_sqlite_path(tmp_path):
    from askdata.db.query_runner import Execute

    db_path = tmp_path / "demo.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE items(id INTEGER)")
        connection.execute("INSERT INTO items VALUES (1)")

    result = Execute("SELECT id FROM items", str(db_path))

    assert result["success"] is True
    assert result["rows"] == [{"id": 1}]


def test_registry_resolves_registered_database_id():
    from askdata.db.adapters.base import DatabaseAdapter
    from askdata.db.adapters.registry import ClearRegistryForTests, Register
    from askdata.db.query_runner import Execute
    from askdata.db.validator import SQLValidator

    class FakeAdapter(DatabaseAdapter):
        dialect = "sqlite"

        def Validate(self, sql):
            return SQLValidator(dialect="sqlite").validate(sql)

        def Execute(self, sql, *, preview_limit=100):
            return {"success": True, "sql": sql, "columns": ["ok"], "rows": [{"ok": 1}]}

        def IntrospectSchema(self):
            return {"tables": []}

    ClearRegistryForTests()
    Register("registered_demo", FakeAdapter())

    result = Execute("SELECT 1 AS ok", "registered_demo")

    assert result["success"] is True
    assert result["rows"] == [{"ok": 1}]
    ClearRegistryForTests()


class FakeRow:
    def __init__(self, mapping):
        self._mapping = mapping


class FakeResult:
    def __init__(self, keys, rows):
        self._keys = keys
        self._rows = list(rows)

    def keys(self):
        return self._keys

    def fetchmany(self, size):
        return self._rows[:size]


class FakeConnection:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement):
        self.statements.append(str(statement))
        if self.error:
            raise self.error
        return self.result


class FakeEngine:
    def __init__(self, connection):
        self.connection = connection

    def connect(self):
        return self.connection


def test_mysql_adapter_executes_with_injected_engine():
    from askdata.db.adapters.mysql import MySQLAdapter

    connection = FakeConnection(FakeResult(["id"], [FakeRow({"id": 1})]))
    adapter = MySQLAdapter(engine=FakeEngine(connection))

    result = adapter.Execute("SELECT id FROM items")

    assert result["success"] is True
    assert result["columns"] == ["id"]
    assert result["rows"] == [{"id": 1}]
    assert result["truncated"] is False
    assert "SELECT" in connection.statements[0]


def test_postgresql_adapter_normalizes_execution_errors():
    from askdata.db.adapters.postgresql import PostgreSQLAdapter

    connection = FakeConnection(error=RuntimeError('relation "schools" does not exist'))
    adapter = PostgreSQLAdapter(engine=FakeEngine(connection))

    result = adapter.Execute("SELECT * FROM schools")

    assert result["success"] is False
    assert result["error_code"] == "unknown_table"
    assert result["error"] == "unknown_table: schools"


def test_registry_loads_database_connections_json(tmp_path):
    from askdata.db.adapters.mysql import MySQLAdapter
    from askdata.db.adapters.postgresql import PostgreSQLAdapter
    from askdata.db.adapters.registry import ClearRegistryForTests, LoadFromJson, Resolve

    config = tmp_path / "database_connections.json"
    config.write_text(
        """
        {
          "sales_mysql": {"dialect": "mysql", "url": "mysql+pymysql://u:p@localhost/db"},
          "analytics_pg": {"dialect": "postgresql", "url": "postgresql+psycopg://u:p@localhost/db"}
        }
        """,
        encoding="utf-8",
    )

    ClearRegistryForTests()
    count = LoadFromJson(config)

    assert count == 2
    assert isinstance(Resolve("sales_mysql"), MySQLAdapter)
    assert isinstance(Resolve("analytics_pg"), PostgreSQLAdapter)
    ClearRegistryForTests()


def test_registry_ignores_missing_database_connections_json(tmp_path):
    from askdata.db.adapters.registry import LoadFromJson

    assert LoadFromJson(tmp_path / "missing.json") == 0
