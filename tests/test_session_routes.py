from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from askdata.api import routes  # noqa: E402
from askdata.api.app import app  # noqa: E402
from askdata.api.session_manager import SessionManager  # noqa: E402


def test_session_crud_routes_use_persistent_manager(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "session_manager", SessionManager(checkpoint_dir=str(tmp_path)), raising=False)

    client = TestClient(app, backend_options={"use_uvloop": True})
    created = client.post("/api/sessions", json={"database_id": "demo"})

    assert created.status_code == 200
    session_id = created.json()["session_id"]
    assert created.json()["thread_id"] == session_id

    listed = client.get("/api/sessions")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["sessions"][0]["database_id"] == "demo"

    updated = client.patch(f"/api/sessions/{session_id}", json={"database_id": "finance"})
    assert updated.status_code == 200

    detail = client.get(f"/api/sessions/{session_id}")
    assert detail.status_code == 200
    assert detail.json()["database_id"] == "finance"
    assert detail.json()["history"] == []

    reset = client.post(f"/api/sessions/{session_id}/reset")
    assert reset.status_code == 200

    deleted = client.delete(f"/api/sessions/{session_id}")
    assert deleted.status_code == 200
    assert client.get(f"/api/sessions/{session_id}").status_code == 404


def test_execute_sql_route_replays_saved_sql(tmp_path, monkeypatch):
    database_path = tmp_path / "demo.sqlite"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE items(name TEXT, amount INTEGER)")
    connection.executemany("INSERT INTO items(name, amount) VALUES (?, ?)", [("A", 3), ("B", 5)])
    connection.commit()
    connection.close()
    monkeypatch.setattr(
        routes,
        "_scan_databases",
        lambda: [{"id": "demo", "name": "Demo", "path": str(database_path), "tables_count": 1}],
    )

    client = TestClient(app, backend_options={"use_uvloop": True})
    response = client.post(
        "/api/query/execute-sql",
        json={"database_id": "demo", "sql": "SELECT name, amount FROM items ORDER BY amount DESC"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["error"] is None
    assert body["columns"] == ["name", "amount"]
    assert body["rows"] == [{"name": "B", "amount": 5}, {"name": "A", "amount": 3}]
