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


def test_query_route_uses_agent_graph_instead_of_mock(monkeypatch):
    monkeypatch.setattr(routes, "AgentGraph", FakeGraph, raising=False)
    client = TestClient(app)

    response = client.post("/api/query", json={"question": "How many items?", "database_id": "demo"})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "共有 3 条。"
    assert body["sql"] == "SELECT COUNT(id) AS count FROM items"
    assert body["columns"] == ["count"]
    assert body["rows"] == [{"count": 3}]
    assert body["trace"][0]["step"] == "RetrieveSchema"
