from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient

from askdata.api import routes
from askdata.api.app import app


class FakeGraph:
    def __init__(self):
        pass

    async def ARun(self, question, database_id, session_context=None):
        assert question == "How many items?"
        assert database_id == "demo"
        return {
            "answer": "共有 3 条。",
            "sql": "SELECT COUNT(id) AS count FROM items",
            "columns": ["count"],
            "rows": [{"count": 3}],
            "chart": None,
            "trace": [{"step": "RetrieveSchema", "status": "success", "message": "Schema matched."}],
            "error": None,
        }


class CapturingGraph:
    contexts = []

    async def ARun(self, question, database_id, session_context=None):
        self.contexts.append(session_context)
        return {
            "answer": "ok", "sql": "SELECT 1", "columns": ["value"],
            "rows": [{"value": 1}], "trace": [], "error": None,
        }


def test_query_route_uses_agent_graph_instead_of_mock(monkeypatch):
    monkeypatch.setattr(routes, "AgentGraph", FakeGraph, raising=False)
    client = TestClient(app, backend_options={"use_uvloop": True})

    response = client.post("/api/query", json={"question": "How many items?", "database_id": "demo"})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "共有 3 条。"
    assert body["sql"] == "SELECT COUNT(id) AS count FROM items"
    assert body["columns"] == ["count"]
    assert body["rows"] == [{"count": 3}]
    assert body["trace"][0]["step"] == "UnderstandQuestion"
    assert body["trace"][1]["step"] == "RetrieveSchema"


def test_switching_database_does_not_send_previous_database_sql(monkeypatch, tmp_path):
    from askdata.api.session_manager import SessionManager

    manager = SessionManager(checkpoint_dir=str(tmp_path))
    monkeypatch.setattr(routes, "session_manager", manager)
    monkeypatch.setattr(routes, "AgentGraph", CapturingGraph)
    CapturingGraph.contexts = []
    client = TestClient(app, backend_options={"use_uvloop": True})
    session_id = client.post("/api/sessions", json={"database_id": "old-db"}).json()["session_id"]

    first = client.post("/api/query", json={
        "question": "旧库问题", "database_id": "old-db", "session_id": session_id,
    })
    second = client.post("/api/query", json={
        "question": "新库问题", "database_id": "new-db", "session_id": session_id,
    })

    assert first.status_code == second.status_code == 200
    assert CapturingGraph.contexts[0].get("last_sql") is None
    assert CapturingGraph.contexts[1].get("last_sql") is None
    assert CapturingGraph.contexts[1]["understanding"]["query_object"] == "新库问题"
