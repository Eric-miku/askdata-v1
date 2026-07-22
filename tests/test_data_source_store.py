import sqlite3

from askdata.data.source_store import DataSourceStore
from askdata.data.schema_catalog import BuildSqliteCatalog


def test_data_source_lifecycle_and_connection_check(tmp_path):
    database = tmp_path / "demo.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT)")
    store = DataSourceStore(tmp_path / "sources.sqlite")
    source = store.save("demo", "Demo DB", str(database))
    assert source["enabled"] is True
    checked = store.check("demo")
    assert checked["health"] == "healthy"
    assert checked["table_count"] == 1
    synced = store.mark_synced("demo")
    assert synced["last_synced_at"] is not None
    assert store.set_enabled("demo", False)["enabled"] is False
    assert store.delete("demo") is True


def test_missing_database_is_reported_as_unhealthy(tmp_path):
    store = DataSourceStore(tmp_path / "sources.sqlite")
    store.save("missing", "Missing", str(tmp_path / "missing.sqlite"))
    assert store.check("missing")["health"] == "unhealthy"


def test_schema_catalog_captures_constraints_indexes_and_changes(tmp_path):
    database = tmp_path / "catalog.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE departments (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE employees (
                id INTEGER PRIMARY KEY,
                department_id INTEGER NOT NULL,
                email TEXT DEFAULT '',
                FOREIGN KEY (department_id) REFERENCES departments(id)
            );
            CREATE UNIQUE INDEX idx_employees_email ON employees(email);
        """)

    catalog = BuildSqliteCatalog(database)
    assert catalog["table_count"] == 2
    assert catalog["index_count"] == 1
    employees = next(item for item in catalog["tables"] if item["name"] == "employees")
    assert employees["primary_key"] == ["id"]
    assert employees["foreign_keys"][0]["referenced_table"] == "departments"
    assert employees["indexes"][0]["columns"] == ["email"]
    assert len(catalog["fingerprint"]) == 64

    store = DataSourceStore(tmp_path / "state.sqlite")
    store.save("catalog", "Catalog", str(database))
    first = store.mark_synced("catalog")
    assert first["schema_changed"] is False
    assert first["schema_change_summary"]["initial_sync"] is True
    fingerprint = first["schema_fingerprint"]

    unchanged = store.mark_synced("catalog")
    assert unchanged["schema_changed"] is False
    assert unchanged["schema_fingerprint"] == fingerprint

    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE employees ADD COLUMN display_name TEXT")
    changed = store.mark_synced("catalog")
    assert changed["schema_changed"] is True
    assert changed["schema_fingerprint"] != fingerprint
    assert changed["schema_change_summary"]["tables_changed"] == ["employees"]
    snapshot = store.catalog("catalog")
    assert snapshot["previous_fingerprint"] == fingerprint
    assert snapshot["catalog"]["column_count"] == 6

    assert store.delete("catalog") is True
    assert store.catalog("catalog") is None
