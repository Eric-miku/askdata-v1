import sqlite3

from fastapi.testclient import TestClient

from askdata.api import routes
from askdata.api.app import app
from askdata.data.source_store import DataSourceStore
from askdata.security.permissions import PermissionStore


def _database(tmp_path):
    path = tmp_path / "sales.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY,
                region TEXT NOT NULL,
                amount REAL NOT NULL
            );
            INSERT INTO orders(region, amount) VALUES ('华东', 100), ('华南', 200);
        """)
    return path


def test_schema_sync_route_persists_catalog_and_reports_changes(tmp_path, monkeypatch):
    path = _database(tmp_path)
    store = DataSourceStore(tmp_path / "sources.sqlite")
    store.save("sales", "Sales", str(path))
    monkeypatch.setattr(routes, "data_source_store", store)
    client = TestClient(app, backend_options={"use_uvloop": True})

    first = client.post("/api/data-sources/sales/sync")
    assert first.status_code == 200
    assert len(first.json()["schema_fingerprint"]) == 64
    assert first.json()["schema_changed"] is False

    schema = client.get("/api/data-sources/sales/schema")
    assert schema.status_code == 200
    payload = schema.json()
    assert payload["catalog"]["tables"][0]["name"] == "orders"
    assert "sales.sqlite" not in schema.text

    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE orders ADD COLUMN channel TEXT")
    changed = client.post("/api/data-sources/sales/sync")
    assert changed.status_code == 200
    assert changed.json()["schema_changed"] is True
    assert changed.json()["schema_change_summary"]["tables_changed"] == ["orders"]


def test_catalog_route_requires_sync_and_known_source(tmp_path, monkeypatch):
    store = DataSourceStore(tmp_path / "sources.sqlite")
    store.save("sales", "Sales", str(_database(tmp_path)))
    monkeypatch.setattr(routes, "data_source_store", store)
    client = TestClient(app, backend_options={"use_uvloop": True})

    assert client.get("/api/data-sources/missing/schema").status_code == 404
    assert client.get("/api/data-sources/sales/schema").status_code == 409


def test_explain_route_reuses_field_permissions_and_blocks_mutation(tmp_path, monkeypatch):
    path = _database(tmp_path)
    permissions = PermissionStore(tmp_path / "permissions.sqlite")
    permissions.save({
        "user_id": "alice",
        "database_id": "sales",
        "table_name": "orders",
        "field_name": "region",
        "can_query": True,
        "can_export": False,
    })
    monkeypatch.setattr(routes, "permission_store", permissions)
    monkeypatch.setattr(routes, "_scan_databases", lambda: [{
        "id": "sales", "name": "Sales", "path": str(path), "tables_count": 1,
    }])
    client = TestClient(app, backend_options={"use_uvloop": True})
    headers = {"X-User-ID": "alice"}

    explained = client.post(
        "/api/query/explain",
        headers=headers,
        json={"database_id": "sales", "sql": "SELECT region FROM orders WHERE region = '华东'"},
    )
    assert explained.status_code == 200
    assert explained.json()["plan"]

    denied = client.post(
        "/api/query/explain",
        headers=headers,
        json={"database_id": "sales", "sql": "SELECT amount FROM orders"},
    )
    assert denied.status_code == 403
    assert "字段 amount" in denied.json()["detail"]

    blocked = client.post(
        "/api/query/explain",
        headers=headers,
        json={"database_id": "sales", "sql": "DELETE FROM orders"},
    )
    assert blocked.status_code == 400

    hidden = client.post(
        "/api/query/explain",
        headers={"X-User-ID": "bob"},
        json={"database_id": "sales", "sql": "SELECT region FROM orders"},
    )
    assert hidden.status_code == 404
