import sqlite3

from askdata.core.config import settings
from askdata.db.executor import ErrorCode, SQLExecutor
from askdata.db.query_runner import Execute
from askdata.db.validator import SQLValidator


def _database(tmp_path):
    database_path = tmp_path / "governance.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE items(id INTEGER, payload TEXT)")
        connection.executemany(
            "INSERT INTO items VALUES (?, ?)",
            [(index, "x" * 20) for index in range(1, 6)],
        )
    return database_path


def test_validator_blocks_system_objects_join_excess_and_deep_subqueries():
    validator = SQLValidator(dialect="sqlite", max_joins=1, max_subquery_depth=1)

    system = validator.validate("SELECT name FROM sqlite_master")
    joins = validator.validate(
        "SELECT * FROM a JOIN b ON a.id=b.id JOIN c ON b.id=c.id"
    )
    nested = validator.validate(
        "SELECT * FROM (SELECT * FROM (SELECT * FROM items) AS inner_q) AS outer_q"
    )

    assert not system.is_valid and "系统对象" in system.reason
    assert not joins.is_valid and "JOIN" in joins.reason
    assert not nested.is_valid and "子查询深度" in nested.reason


def test_query_runner_enforces_mode_row_limits_and_returns_governance_metadata(tmp_path, monkeypatch):
    database_path = _database(tmp_path)
    monkeypatch.setattr(settings, "QUERY_MAX_ROWS", 2)
    monkeypatch.setattr(settings, "SLOW_QUERY_MS", 0)

    result = Execute("SELECT id FROM items ORDER BY id", str(database_path), page_size=100)

    assert result["success"] is True
    assert result["rows"] == [{"id": 1}, {"id": 2}]
    assert result["pagination"]["page_size"] == 2
    assert result["elapsed_ms"] >= 0
    assert result["warnings"] and result["warnings"][0].startswith("慢查询")


def test_executor_rejects_results_over_byte_limit(tmp_path):
    database_path = _database(tmp_path)
    executor = SQLExecutor(
        f"sqlite:///{database_path}",
        dialect="sqlite",
        default_page_size=5,
        max_page_size=5,
        max_result_bytes=30,
    )

    result = executor.execute("SELECT payload FROM items", page_size=5)

    assert result.success is False
    assert result.error is not None
    assert result.error.code == ErrorCode.RESULT_TOO_LARGE


def test_query_runner_exposes_stable_error_codes(tmp_path):
    missing = Execute("SELECT 1", str(tmp_path / "missing.sqlite"))
    database_path = _database(tmp_path)
    blocked = Execute("SELECT name FROM sqlite_master", str(database_path))

    assert missing["error_code"] == "DB_NOT_FOUND"
    assert blocked["error_code"] == "SQL_BLOCKED"
