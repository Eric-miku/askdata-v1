import asyncio
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.api import routes
from askdata.api.query_service import QueryService
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
            "chart": {
                "type": "vertical_bar",
                "title": "Item count",
                "category_field": None,
                "value_fields": ["count"],
                "reason": "comparison",
            },
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
def api(tmp_path):
    database_path = tmp_path / "sessions.sqlite"
    state = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = SessionStore(database_path)
        await store.Initialize()
        app.state.session_store = store
        app.state.query_service = QueryService(store, graph_factory=FakeGraph)
        state["store"] = store
        try:
            yield
        finally:
            await store.Close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(routes.router, prefix="/api")
    FakeGraph.calls = []
    with TestClient(app) as client:
        yield client, state, database_path


def test_create_session_returns_id_and_created_at(api):
    client, _, _ = api

    response = client.post("/api/sessions?database_id=demo")

    assert response.status_code == 200
    assert response.json()["session_id"]
    assert isinstance(response.json()["created_at"], float)
    assert response.json()["created_at"] > 0
    stored = client.get(f"/api/sessions/{response.json()['session_id']}").json()
    assert stored["created_at"].endswith("+00:00")


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
    body = second.json()
    assert body["kind"] == "answer"
    assert body["session_id"] == session_id
    assert body["turn_id"]
    assert body["confidence"] == "medium"
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
    assert turns[-1]["chart"] == {
        "type": "vertical_bar",
        "title": "Item count",
        "category_field": None,
        "category_label": None,
        "value_fields": ["count"],
        "value_labels": {},
        "reason": "comparison",
    }
    assert turns[-1]["trace"] == [
        {
            "step": "RetrieveSchema",
            "status": "success",
            "message": "Schema matched.",
            "sequence": 1,
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


def test_query_failure_sanitizes_public_response_and_persisted_turn(api, caplog):
    client, _, _ = api

    class FailingGraph:
        async def ARun(self, **kwargs):
            raise RuntimeError("database password leaked")

    client.app.state.query_service._graph_factory = FailingGraph
    response = client.post(
        "/api/query", json={"question": "Fail safely", "database_id": "demo"}
    )

    assert response.status_code == 200
    assert response.json()["kind"] == "error"
    assert response.json()["code"] == "query_failed"
    assert "database password leaked" not in response.json()["message"]
    assert response.json()["retryable"] is True
    assert response.json()["suggestions"] == ["重试", "换一种问法"]
    assert "database password leaked" not in json.dumps(response.json()["trace"])
    session_id = client.get("/api/sessions").json()[0]["id"]
    turn = client.get(f"/api/sessions/{session_id}").json()["turns"][0]
    assert turn["question"] == "Fail safely"
    assert turn["response_kind"] == "error"
    assert turn["error"]["code"] == "query_failed"
    assert "database password leaked" not in json.dumps(turn["trace"])
    assert "database password leaked" in caplog.text


def test_query_sanitizes_returned_agent_error_and_discards_sensitive_fields(api):
    client, _, _ = api
    secret = "returned-agent-secret-7391"

    class ErrorGraph:
        async def ARun(self, **kwargs):
            return {
                "answer": f"unsafe answer {secret}",
                "sql": f"SELECT '{secret}'",
                "columns": ["secret"],
                "rows": [{"secret": secret}],
                "chart": {"title": secret},
                "trace": [{"message": secret}],
                "error": {"detail": secret},
            }

    client.app.state.query_service._graph_factory = ErrorGraph

    response = client.post(
        "/api/query", json={"question": "Return safely", "database_id": "demo"}
    )

    assert response.status_code == 200
    body = response.json()
    assert secret not in json.dumps(body)
    assert body["kind"] == "error"
    assert body["code"] == "query_failed"
    assert body["message"] == "查询失败，请稍后重试或换一种问法。"
    assert body["retryable"] is True
    session_id = client.get("/api/sessions").json()[0]["id"]
    turn = client.get(f"/api/sessions/{session_id}").json()["turns"][0]
    assert secret not in json.dumps(turn)
    assert turn["answer"] is None
    assert turn["sql"] is None
    assert turn["result_preview"] == []
    assert turn["chart"] is None
    assert turn["error"]["code"] == "query_failed"
    assert turn["trace"] == body["trace"]


def test_query_normalizes_non_json_graph_values_for_response_and_history(api):
    client, _, _ = api
    item_id = UUID("12345678-1234-5678-1234-567812345678")
    created_at = datetime(2026, 7, 15, 10, 30, tzinfo=timezone.utc)

    class RichGraph:
        async def ARun(self, **kwargs):
            return {
                "answer": "Normalized.",
                "sql": "SELECT 1",
                "columns": ["amount", "created_at", "id", "payload"],
                "rows": [
                    {
                        "amount": Decimal("12.50"),
                        "created_at": created_at,
                        "id": item_id,
                        "payload": b"\xff\x00",
                    }
                ],
                "chart": None,
                "trace": [{"at": created_at}],
                "error": None,
            }

    client.app.state.query_service._graph_factory = RichGraph

    response = client.post(
        "/api/query", json={"question": "Rich values", "database_id": "demo"}
    )

    assert response.status_code == 200
    expected_row = {
        "amount": "12.50",
        "created_at": "2026-07-15T10:30:00+00:00",
        "id": str(item_id),
        "payload": "/wA=",
    }
    assert response.json()["rows"] == [expected_row]
    session_id = client.get("/api/sessions").json()[0]["id"]
    turn = client.get(f"/api/sessions/{session_id}").json()["turns"][0]
    assert turn["result_preview"] == [expected_row]
    assert turn["chart"] is None
    assert turn["trace"] == [
        {
            "step": "agent",
            "status": "success",
            "message": '{"at": "2026-07-15T10:30:00+00:00"}',
            "sequence": 1,
        }
    ]


def test_concurrent_queries_for_one_session_serialize_and_share_context(api):
    client, _, _ = api
    session_id = client.post("/api/sessions?database_id=demo").json()["session_id"]
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    calls = []

    class BlockingGraph:
        async def ARun(self, question, database_id, session_context=None):
            calls.append((question, session_context))
            if question == "First":
                first_entered.set()
                await asyncio.to_thread(release_first.wait)
            else:
                second_entered.set()
            return {
                "answer": question,
                "sql": f"SELECT '{question}'",
                "columns": [],
                "rows": [],
                "chart": None,
                "trace": [],
                "error": None,
            }

    client.app.state.query_service._graph_factory = BlockingGraph
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            client.post,
            "/api/query",
            json={"question": "First", "database_id": "demo", "session_id": session_id},
        )
        assert first_entered.wait(timeout=2)
        second = executor.submit(
            client.post,
            "/api/query",
            json={"question": "Second", "database_id": "demo", "session_id": session_id},
        )
        try:
            assert not second_entered.wait(timeout=0.1)
            manager = client.app.state.query_service._locks
            assert list(manager._entries) == [session_id]
            entry = manager._entries[session_id]
            assert entry["refs"] == 2
            assert entry["lock"].locked()
        finally:
            release_first.set()
        assert first.result(timeout=2).status_code == 200
        assert second.result(timeout=2).status_code == 200

    assert calls == [
        ("First", {}),
        (
            "Second",
            {"last_question": "First", "last_sql": "SELECT 'First'"},
        ),
    ]
    assert client.app.state.query_service._locks._entries == {}


def test_delete_waits_for_in_flight_query_then_deletes_session(api):
    client, _, _ = api
    session_id = client.post("/api/sessions?database_id=demo").json()["session_id"]
    query_entered = threading.Event()
    release_query = threading.Event()

    class BlockingGraph:
        async def ARun(self, **kwargs):
            query_entered.set()
            await asyncio.to_thread(release_query.wait)
            return {
                "answer": "Finished.",
                "sql": "SELECT 1",
                "columns": [],
                "rows": [],
                "chart": None,
                "trace": [],
                "error": None,
            }

    client.app.state.query_service._graph_factory = BlockingGraph
    with ThreadPoolExecutor(max_workers=2) as executor:
        query = executor.submit(
            client.post,
            "/api/query",
            json={"question": "Slow", "database_id": "demo", "session_id": session_id},
        )
        assert query_entered.wait(timeout=2)
        delete = executor.submit(client.delete, f"/api/sessions/{session_id}")
        try:
            with pytest.raises(TimeoutError):
                delete.result(timeout=0.1)
        finally:
            release_query.set()
        assert query.result(timeout=2).status_code == 200
        assert delete.result(timeout=2).status_code == 200

    assert client.get(f"/api/sessions/{session_id}").status_code == 404
    assert client.app.state.query_service._locks._entries == {}


def test_missing_delete_releases_lock_registry_entry(api):
    client, _, _ = api

    response = client.delete("/api/sessions/arbitrary-missing-id")

    assert response.status_code == 404
    assert client.app.state.query_service._locks._entries == {}


def test_missing_session_replacement_query_releases_all_lock_entries(api):
    client, _, _ = api

    response = client.post(
        "/api/query",
        json={
            "question": "Replace missing",
            "database_id": "demo",
            "session_id": "arbitrary-missing-id",
        },
    )

    assert response.status_code == 200
    assert client.app.state.query_service._locks._entries == {}
    sessions = client.get("/api/sessions").json()
    assert len(sessions) == 1
    assert sessions[0]["id"] != "arbitrary-missing-id"
