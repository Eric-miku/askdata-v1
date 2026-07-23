from pathlib import Path
import sqlite3
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.db.query_runner import Execute  # noqa: E402
from askdata.data import source_store  # noqa: E402
from askdata.data.source_store import DataSourceStore  # noqa: E402


def test_execute_rejects_missing_database_without_creating_file(tmp_path):
    database_path = tmp_path / "missing.sqlite"

    result = Execute("SELECT 1", str(database_path))

    assert result["success"] is False
    assert "does not exist" in result["error"]
    assert not database_path.exists()


def test_execute_decodes_legacy_gbk_text_without_crashing(tmp_path):
    database_path = tmp_path / "legacy.sqlite"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE items(note TEXT)")
    connection.execute("INSERT INTO items(note) VALUES (CAST(X'C4E3BAC3' AS TEXT))")
    connection.commit()
    connection.close()

    result = Execute("SELECT note FROM items", str(database_path))

    assert result["success"] is True
    assert result["rows"] == [{"note": "你好"}]


def test_execute_managed_external_source_and_blocks_mutation(tmp_path, monkeypatch):
    database_path = tmp_path / "company.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT)")
        connection.execute("INSERT INTO items VALUES (1, 'alpha')")

    store = DataSourceStore(tmp_path / "sources.sqlite")
    store.save("company", "Company", f"sqlite:///{database_path}", kind="mysql")
    monkeypatch.setattr(source_store, "data_source_store", store)

    result = Execute("SELECT name FROM items", "source:company")
    assert result["success"] is True
    assert result["rows"] == [{"name": "alpha"}]

    blocked = Execute("DROP TABLE items", "source:company")
    assert blocked["success"] is False
    assert blocked["error_code"] == "SQL_BLOCKED"

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


def test_execute_managed_external_source_resolves_env_url(tmp_path, monkeypatch):
    database_path = tmp_path / "company_env.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT)")
        connection.execute("INSERT INTO items VALUES (1, 'alpha')")
    monkeypatch.setenv("TEST_COMPANY_DB_URL", f"sqlite:///{database_path}")

    store = DataSourceStore(tmp_path / "sources.sqlite")
    store.save("company", "Company", "env:TEST_COMPANY_DB_URL", kind="mysql")
    monkeypatch.setattr(source_store, "data_source_store", store)

    result = Execute("SELECT name FROM items", "source:company")

    assert result["success"] is True
    assert result["rows"] == [{"name": "alpha"}]
