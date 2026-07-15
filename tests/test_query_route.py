import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.api import routes
from askdata.api.session_store import SessionStore


class FakeGraph:
    calls = []

    def __init__(self):
        pass

    async def ARun(self, question, database_id, session_context=None):
        self.calls.append(
            {
                "question": question,
                "database_id": database_id,
                "session_context": session_context,
            }
        )
        return {
            "answer": "共有 3 条。",
            "sql": "SELECT COUNT(id) AS count FROM items",
            "columns": ["count"],
            "rows": [{"count": 3}],
            "chart": {"type": "bar"},
            "trace": [
                {
                    "step": "RetrieveSchema",
                    "status": "success",
                    "message": "Schema matched.",
                }
            ],
            "error": None,
        }


@pytest.fixture
def api(tmp_path, monkeypatch):
    database_path = tmp_path / "sessions.sqlite"
    state = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = SessionStore(database_path)
        await store.Initialize()
        app.state.session_store = store
        state["store"] = store
        try:
            yield
        finally:
            await store.Close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(routes.router, prefix="/api")
    FakeGraph.calls = []
    monkeypatch.setattr(routes, "AgentGraph", FakeGraph)
    with TestClient(app) as client:
        yield client, state, database_path


def test_create_session_returns_id_and_created_at(api):
    client, _, _ = api

    response = client.post("/api/sessions?database_id=demo")

    assert response.status_code == 200
    assert response.json()["session_id"]
    assert response.json()["created_at"].endswith("+00:00")


def test_list_sessions_orders_recent_first_and_respects_limit(api):
    client, _, _ = api
    first = client.post("/api/sessions?database_id=demo").json()["session_id"]
    second = client.post("/api/sessions?database_id=demo").json()["session_id"]
    client.post(
        "/api/query",
        json={"question": "Refresh first", "database_id": "demo", "session_id": first},
    )

    response = client.get("/api/sessions?limit=1")

    assert response.status_code == 200
    assert [session["id"] for session in response.json()] == [first]
    assert second != first
    assert client.get("/api/sessions?limit=0").status_code == 422
    assert client.get("/api/sessions?limit=101").status_code == 422


def test_get_session_returns_turns_in_order(api):
    client, _, _ = api
    session_id = client.post("/api/sessions?database_id=demo").json()["session_id"]
    client.post(
        "/api/query",
        json={"question": "First question", "database_id": "demo", "session_id": session_id},
    )
    client.post(
        "/api/query",
        json={"question": "Second question", "database_id": "demo", "session_id": session_id},
    )

    response = client.get(f"/api/sessions/{session_id}")

    assert response.status_code == 200
    assert response.json()["id"] == session_id
    assert [turn["question"] for turn in response.json()["turns"]] == [
        "First question",
        "Second question",
    ]


def test_get_unknown_session_returns_404(api):
    client, _, _ = api

    response = client.get("/api/sessions/missing")

    assert response.status_code == 404


def test_delete_session_succeeds_cascades_and_missing_returns_404(api):
    client, _, database_path = api
    session_id = client.post("/api/sessions?database_id=demo").json()["session_id"]
    client.post(
        "/api/query",
        json={"question": "Persist me", "database_id": "demo", "session_id": session_id},
    )

    response = client.delete(f"/api/sessions/{session_id}")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert client.get(f"/api/sessions/{session_id}").status_code == 404
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM turns WHERE session_id = ?", (session_id,)
        ).fetchone()[0] == 0
    assert client.delete(f"/api/sessions/{session_id}").status_code == 404


def test_query_uses_persisted_context_and_saves_turns(api):
    client, _, _ = api

    first = client.post(
        "/api/query", json={"question": "First question", "database_id": "demo"}
    )
    assert first.status_code == 200
    session_id = client.get("/api/sessions").json()[0]["id"]
    second = client.post(
        "/api/query",
        json={"question": "Second question", "database_id": "demo", "session_id": session_id},
    )

    assert second.status_code == 200
    assert FakeGraph.calls == [
        {
            "question": "First question",
            "database_id": "demo",
            "session_context": {},
        },
        {
            "question": "Second question",
            "database_id": "demo",
            "session_context": {
                "last_question": "First question",
                "last_sql": "SELECT COUNT(id) AS count FROM items",
            },
        },
    ]
    turns = client.get(f"/api/sessions/{session_id}").json()["turns"]
    assert [turn["question"] for turn in turns] == ["First question", "Second question"]
    assert all(turn["response_kind"] == "answer" for turn in turns)
    assert turns[-1]["result_preview"] == [{"count": 3}]
    assert turns[-1]["chart"] == {"type": "bar"}
    assert turns[-1]["trace"] == [
        {
            "step": "RetrieveSchema",
            "status": "success",
            "message": "Schema matched.",
        }
    ]


def test_query_with_session_for_other_database_preserves_old_history(api):
    client, _, _ = api
    client.post("/api/query", json={"question": "Old question", "database_id": "first"})
    old_session_id = client.get("/api/sessions").json()[0]["id"]

    response = client.post(
        "/api/query",
        json={
            "question": "New question",
            "database_id": "second",
            "session_id": old_session_id,
        },
    )

    assert response.status_code == 200
    sessions = client.get("/api/sessions").json()
    assert len(sessions) == 2
    new_session = next(session for session in sessions if session["id"] != old_session_id)
    assert new_session["database_id"] == "second"
    old_session = client.get(f"/api/sessions/{old_session_id}").json()
    assert old_session["database_id"] == "first"
    assert [turn["question"] for turn in old_session["turns"]] == ["Old question"]
    assert [turn["question"] for turn in client.get(f"/api/sessions/{new_session['id']}").json()["turns"]] == [
        "New question"
    ]
    assert FakeGraph.calls[-1]["session_context"] == {}


def test_query_rejects_clarification_until_continuations_are_supported(api):
    client, _, _ = api

    response = client.post(
        "/api/query",
        json={
            "database_id": "demo",
            "clarification": {"clarification_id": "clarify-1", "option_id": "2024"},
        },
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Only question queries are supported"}


def test_query_failure_keeps_answer_safe_and_persists_error(api, monkeypatch):
    client, _, _ = api

    class FailingGraph:
        async def ARun(self, **kwargs):
            raise RuntimeError("database password leaked")

    monkeypatch.setattr(routes, "AgentGraph", FailingGraph)
    response = client.post(
        "/api/query", json={"question": "Fail safely", "database_id": "demo"}
    )

    assert response.status_code == 200
    assert "database password leaked" not in response.json()["answer"]
    assert response.json()["error"] == "database password leaked"
    session_id = client.get("/api/sessions").json()[0]["id"]
    turn = client.get(f"/api/sessions/{session_id}").json()["turns"][0]
    assert turn["question"] == "Fail safely"
    assert turn["response_kind"] == "error"
    assert turn["error"] == "database password leaked"
