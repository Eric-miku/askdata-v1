import sqlite3

from askdata.data.source_store import DataSourceStore, ResolveConnectionUrl
from askdata.data.schema_catalog import BuildSqlAlchemyCatalog, BuildSqliteCatalog


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


def test_external_sqlalchemy_source_syncs_schema_without_rows(tmp_path):
    database = tmp_path / "company.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT)")
        connection.execute("INSERT INTO customers VALUES (1, 'secret')")

    url = f"sqlite:///{database}"
    catalog = BuildSqlAlchemyCatalog(url, "mysql")
    assert catalog["dialect"] == "mysql"
    assert catalog["table_count"] == 1
    assert "secret" not in str(catalog)

    store = DataSourceStore(tmp_path / "sources.sqlite")
    store.save("company", "Company DB", url, kind="mysql")
    checked = store.check("company")
    assert checked["health"] == "healthy"
    assert checked["table_count"] == 1
    synced = store.mark_synced("company")
    assert synced["schema_fingerprint"]
    assert store.catalog("company")["catalog"]["dialect"] == "mysql"


def test_scan_databases_prioritizes_synced_external_company_source(tmp_path, monkeypatch):
    from askdata.api import routes

    bird_dir = tmp_path / "bird" / "databases"
    local_path = bird_dir / "demo" / "demo.sqlite"
    local_path.parent.mkdir(parents=True)
    with sqlite3.connect(local_path) as connection:
        connection.execute("CREATE TABLE local_items(id INTEGER PRIMARY KEY)")

    company_path = tmp_path / "company.sqlite"
    with sqlite3.connect(company_path) as connection:
        connection.execute("CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT)")

    store = DataSourceStore(tmp_path / "sources.sqlite")
    store.save("intern_db", "Company MySQL intern_db", f"sqlite:///{company_path}", kind="mysql")
    assert store.mark_synced("intern_db")["health"] == "healthy"

    monkeypatch.setattr(routes, "data_source_store", store)
    monkeypatch.setattr(routes, "_get_bird_databases_dir", lambda: bird_dir)

    databases = routes._scan_databases()

    assert [database["id"] for database in databases[:2]] == ["intern_db", "demo"]
    assert databases[0]["kind"] == "mysql"
    assert databases[0]["path"] == "source:intern_db"
    assert databases[1]["kind"] == "sqlite"


def test_external_source_can_reference_env_url(tmp_path, monkeypatch):
    database = tmp_path / "company_env.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT)")
    monkeypatch.setenv("TEST_COMPANY_DB_URL", f"sqlite:///{database}")

    store = DataSourceStore(tmp_path / "sources.sqlite")
    store.save("company", "Company DB", "env:TEST_COMPANY_DB_URL", kind="mysql")

    assert ResolveConnectionUrl("env:TEST_COMPANY_DB_URL") == f"sqlite:///{database}"
    assert store.check("company")["health"] == "healthy"
    assert store.mark_synced("company")["schema_fingerprint"]
    assert store.get("company")["path"] == "env:TEST_COMPANY_DB_URL"
