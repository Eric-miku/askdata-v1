import sqlite3
import csv
import io

from fastapi.testclient import TestClient

from askdata.api import routes
from askdata.api.app import app
from askdata.db.query_runner import Execute
from askdata.security.permissions import (
    PermissionStore,
    ResetSqlAuthorizer,
    SetSqlAuthorizer,
)


def _store(tmp_path):
    return PermissionStore(tmp_path / "permissions.sqlite")


def _grant(store, **overrides):
    payload = {
        "user_id": "alice",
        "database_id": "sales",
        "table_name": None,
        "field_name": None,
        "can_query": True,
        "can_export": True,
    }
    payload.update(overrides)
    return store.save(payload)


def test_unconfigured_store_allows_local_development(tmp_path):
    store = _store(tmp_path)

    assert store.database_allowed("anyone", "any-db")
    assert store.authorize_sql("anyone", "any-db", "SELECT * FROM private_data") == (True, None)


def test_legacy_policy_store_migrates_row_filter_column(tmp_path):
    path = tmp_path / "permissions.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE permission_policies (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, database_id TEXT NOT NULL,
            table_name TEXT, field_name TEXT, can_query INTEGER NOT NULL DEFAULT 1,
            can_export INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL,
            UNIQUE(user_id, database_id, table_name, field_name)
        )""")
        connection.execute(
            "INSERT INTO permission_policies VALUES ('legacy', 'alice', 'sales', 'orders', NULL, 1, 1, 1)"
        )

    store = PermissionStore(path)
    assert store.list()[0]["row_filter"] is None
    updated = _grant(store, table_name="orders", row_filter="region = '华东'")
    assert updated["id"] == "legacy"
    assert updated["row_filter"] == "region = '华东'"


def test_database_policy_uses_allow_list_and_updates_without_duplicates(tmp_path):
    store = _store(tmp_path)
    original = _grant(store)
    updated = _grant(store, can_export=False)

    assert original["id"] == updated["id"]
    assert len(store.list("alice")) == 1
    assert store.database_allowed("alice", "sales")
    assert not store.database_allowed("bob", "sales")
    assert not store.database_allowed("alice", "other")
    assert not store.database_allowed("alice", "sales", "export")


def test_table_policy_blocks_other_tables(tmp_path):
    store = _store(tmp_path)
    _grant(store, table_name="orders")

    assert store.authorize_sql("alice", "sales", "SELECT id FROM orders") == (True, None)
    allowed, reason = store.authorize_sql("alice", "sales", "SELECT id FROM customers")
    assert not allowed
    assert reason == "用户无权访问表 customers"


def test_field_policy_blocks_other_fields_star_and_handles_aliases(tmp_path):
    store = _store(tmp_path)
    _grant(store, table_name="orders", field_name="amount")

    assert store.authorize_sql("alice", "sales", "SELECT o.amount FROM orders AS o") == (True, None)
    allowed, reason = store.authorize_sql("alice", "sales", "SELECT o.customer_name FROM orders AS o")
    assert not allowed
    assert reason == "用户无权访问字段 customer_name"
    allowed, reason = store.authorize_sql("alice", "sales", "SELECT * FROM orders")
    assert not allowed
    assert reason == "字段级权限不允许使用 SELECT *"


def test_query_can_be_allowed_while_export_is_denied(tmp_path):
    store = _store(tmp_path)
    _grant(store, can_query=True, can_export=False)

    assert store.authorize_sql("alice", "sales", "SELECT 1", "query") == (True, None)
    allowed, reason = store.authorize_sql("alice", "sales", "SELECT 1", "export")
    assert not allowed
    assert reason == "用户无权访问数据源 sales"


def test_context_authorizer_blocks_query_runner_before_execution(tmp_path):
    database_path = tmp_path / "data.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE secrets(value TEXT)")
        connection.execute("INSERT INTO secrets VALUES ('hidden')")

    token = SetSqlAuthorizer(lambda sql, mode: (False, "denied by test policy"))
    try:
        result = Execute("SELECT value FROM secrets", str(database_path))
    finally:
        ResetSqlAuthorizer(token)

    assert result == {
        "success": False,
        "error": "denied by test policy",
        "error_code": "PERMISSION_DENIED",
    }


def test_row_filter_is_applied_to_query_and_hidden_from_result_sql(tmp_path):
    database_path = tmp_path / "data.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE orders(id INTEGER, region TEXT, amount REAL)")
        connection.executemany(
            "INSERT INTO orders VALUES (?, ?, ?)",
            [(1, "华东", 10), (2, "华南", 20), (3, "华东", 30)],
        )
    store = _store(tmp_path)
    _grant(store, table_name="orders", row_filter="region = '华东'")

    original_sql = "SELECT id, region, amount FROM orders ORDER BY id"
    token = SetSqlAuthorizer(
        lambda sql, mode: store.prepare_sql("alice", "sales", sql, mode)
    )
    try:
        result = Execute(original_sql, str(database_path))
    finally:
        ResetSqlAuthorizer(token)

    assert result["success"]
    assert result["rows"] == [
        {"id": 1, "region": "华东", "amount": 10.0},
        {"id": 3, "region": "华东", "amount": 30.0},
    ]
    assert result["sql"] == original_sql
    assert "华东" not in result["sql"]


def test_row_filters_cover_joined_tables_and_ctes(tmp_path):
    database_path = tmp_path / "joined.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE orders(id INTEGER, customer_id INTEGER, tenant_id INTEGER)")
        connection.execute("CREATE TABLE customers(id INTEGER, tenant_id INTEGER, name TEXT)")
        connection.executemany("INSERT INTO orders VALUES (?, ?, ?)", [(1, 1, 7), (2, 2, 8)])
        connection.executemany(
            "INSERT INTO customers VALUES (?, ?, ?)", [(1, 7, "allowed"), (2, 8, "hidden")]
        )
    store = _store(tmp_path)
    _grant(store, table_name="orders", row_filter="tenant_id = 7")
    _grant(store, table_name="customers", row_filter="tenant_id = 7")
    sql = """WITH selected AS (
        SELECT o.customer_id FROM orders o
    )
    SELECT c.name FROM selected s JOIN customers c ON c.id = s.customer_id
    """

    allowed, reason, rewritten = store.prepare_sql("alice", "sales", sql)
    assert allowed and reason is None
    assert rewritten.count("tenant_id = 7") == 2
    token = SetSqlAuthorizer(lambda value, mode: store.prepare_sql("alice", "sales", value, mode))
    try:
        result = Execute(sql, str(database_path))
    finally:
        ResetSqlAuthorizer(token)
    assert result["success"]
    assert result["rows"] == [{"name": "allowed"}]


def test_row_filter_rejects_unsafe_or_wrong_scope_expressions(tmp_path):
    store = _store(tmp_path)

    for row_filter in (
        "region IN (SELECT region FROM secrets)",
        "random() > 0",
        "other.region = '华东'",
        "region = '华东'; DELETE FROM orders",
    ):
        try:
            _grant(store, table_name="orders", row_filter=row_filter)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe row filter accepted: {row_filter}")

    try:
        _grant(store, row_filter="region = '华东'")
    except ValueError as exc:
        assert str(exc) == "行级权限必须指定表名"
    else:
        raise AssertionError("database-level row filter was accepted")


def test_row_filter_is_enforced_by_replay_export_and_explain_routes(tmp_path, monkeypatch):
    database_path = tmp_path / "sales.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE orders(id INTEGER, region TEXT)")
        connection.executemany("INSERT INTO orders VALUES (?, ?)", [(1, "华东"), (2, "华南")])
    store = _store(tmp_path)
    _grant(store, table_name="orders", row_filter="region = '华东'")
    monkeypatch.setattr(routes, "permission_store", store)
    monkeypatch.setattr(routes, "_scan_databases", lambda: [{
        "id": "sales", "name": "Sales", "path": str(database_path), "tables_count": 1,
    }])
    client = TestClient(app, backend_options={"use_uvloop": True})
    headers = {"X-User-ID": "alice"}
    sql = "SELECT id, region FROM orders ORDER BY id"

    replay = client.post("/api/query/execute-sql", headers=headers, json={"database_id": "sales", "sql": sql})
    assert replay.status_code == 200
    assert replay.json()["rows"] == [{"id": 1, "region": "华东"}]
    assert replay.json()["sql"] == sql

    exported = client.post("/api/query/export", headers=headers, json={
        "database_id": "sales", "question": "orders", "sql": sql, "format": "csv",
    })
    assert exported.status_code == 200
    rows = list(csv.reader(io.StringIO(exported.content.decode("utf-8-sig"))))
    assert rows[-1] == ["1", "华东"]
    assert all("华南" not in cell for row in rows for cell in row)

    explained = client.post("/api/query/explain", headers=headers, json={"database_id": "sales", "sql": sql})
    assert explained.status_code == 200
    assert explained.json()["normalized_sql"] == sql


def test_permission_route_rejects_unsafe_row_filter():
    client = TestClient(app, backend_options={"use_uvloop": True})
    response = client.post("/api/permissions", json={
        "user_id": "alice", "database_id": "sales", "table_name": "orders",
        "row_filter": "region IN (SELECT region FROM secrets)",
    })

    assert response.status_code == 422


def test_policy_delete_removes_saved_policy(tmp_path):
    store = _store(tmp_path)
    policy = _grant(store)

    assert store.delete(policy["id"])
    assert store.list() == []
    assert not store.delete(policy["id"])


def test_permission_routes_and_metadata_filtering(tmp_path, monkeypatch):
    database_path = tmp_path / "sales.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE orders(amount REAL, secret TEXT)")
        connection.execute("CREATE TABLE customers(name TEXT)")

    store = _store(tmp_path)
    monkeypatch.setattr(routes, "permission_store", store)
    monkeypatch.setattr(routes, "_scan_databases", lambda: [{
        "id": "sales", "name": "Sales", "path": str(database_path), "tables_count": 2,
    }])
    client = TestClient(app, backend_options={"use_uvloop": True})

    created = client.post("/api/permissions", json={
        "user_id": "alice", "database_id": "sales", "table_name": "orders",
        "field_name": "amount", "can_query": True, "can_export": False,
    })
    assert created.status_code == 200
    policy = created.json()

    assert client.get("/api/metadata/databases", headers={"X-User-ID": "bob"}).json() == []
    visible = client.get("/api/metadata/sales/tables", headers={"X-User-ID": "alice"})
    assert visible.status_code == 200
    assert visible.json()["tables"] == [{
        "table_name": "orders",
        "columns": [{"name": "amount", "type": "REAL", "primary_key": False, "nullable": True}],
    }]
    assert client.get("/api/permissions", params={"user_id": "alice"}).json()["policies"] == [policy]
    assert client.delete(f"/api/permissions/{policy['id']}").json() == {"success": True}


def test_permission_request_rejects_field_without_table():
    client = TestClient(app, backend_options={"use_uvloop": True})
    response = client.post("/api/permissions", json={
        "user_id": "alice", "database_id": "sales", "field_name": "amount",
    })

    assert response.status_code == 422
