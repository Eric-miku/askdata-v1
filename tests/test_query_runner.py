from pathlib import Path
import sqlite3
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.db.query_runner import Execute  # noqa: E402


def test_execute_rejects_missing_database_without_creating_file(tmp_path):
    database_path = tmp_path / "missing.sqlite"

    result = Execute("SELECT 1", str(database_path))

    assert result["success"] is False
    assert "does not exist" in result["error"]
    assert not database_path.exists()


def populated_database(tmp_path, row_count=150):
    database_path = tmp_path / "rows.sqlite"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE items(id INTEGER)")
    connection.executemany(
        "INSERT INTO items(id) VALUES (?)", [(index,) for index in range(row_count)]
    )
    connection.commit()
    connection.close()
    return database_path


def test_execute_caps_explicit_limit_without_rewriting_sql(tmp_path):
    database_path = populated_database(tmp_path)
    sql = "SELECT id FROM items ORDER BY id LIMIT 120"

    result = Execute(sql, str(database_path))

    assert result["success"] is True
    assert result["sql"] == sql
    assert len(result["rows"]) == 100
    assert result["rows"][-1] == {"id": 99}
    assert result["truncated"] is True


def test_execute_caps_query_without_limit_without_rewriting_sql(tmp_path):
    database_path = populated_database(tmp_path)
    sql = "SELECT id FROM items ORDER BY id"

    result = Execute(sql, str(database_path))

    assert result["success"] is True
    assert result["sql"] == sql
    assert len(result["rows"]) == 100
    assert result["truncated"] is True


def test_execute_marks_preview_not_truncated_at_or_below_cap(tmp_path):
    database_path = populated_database(tmp_path)

    result = Execute("SELECT id FROM items ORDER BY id LIMIT 100", str(database_path))

    assert result["success"] is True
    assert len(result["rows"]) == 100
    assert result["truncated"] is False
