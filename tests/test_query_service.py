import asyncio
import base64
import json
from datetime import date, datetime, time, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
import sys
from uuid import UUID

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.api.query_service import QueryClarificationUnsupported, QueryService
from askdata.api.response_models import AnswerResponse, ErrorResponse
from askdata.api.schemas import QueryRequest
from askdata.api.session_store import SessionStore


def successful_result(**overrides):
    result = {
        "answer": "共有 3 条。",
        "sql": "SELECT COUNT(*) AS count FROM items",
        "columns": ["count"],
        "rows": [{"count": 3}],
        "chart": None,
        "trace": [
            "开始查询",
            {"step": "ExecuteSql", "status": "success", "message": "完成"},
        ],
        "error": None,
    }
    result.update(overrides)
    return result


class RecordingGraph:
    def __init__(self, result=None, error=None):
        self.result = result or successful_result()
        self.error = error
        self.calls = []

    async def ARun(self, question, database_id, session_context=None):
        self.calls.append((question, database_id, session_context))
        if self.error is not None:
            raise self.error
        return self.result


async def initialized_store(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite")
    await store.Initialize()
    return store


@pytest.mark.asyncio
async def test_success_maps_legacy_result_to_answer_and_persists(tmp_path):
    store = await initialized_store(tmp_path)
    graph = RecordingGraph()
    service = QueryService(store, graph_factory=lambda: graph)

    response = await service.Run(QueryRequest(database_id="demo", question="多少条？"))

    assert isinstance(response, AnswerResponse)
    assert response.kind == "answer"
    assert response.session_id
    assert response.turn_id
    assert response.answer == "共有 3 条。"
    assert response.sql == "SELECT COUNT(*) AS count FROM items"
    assert response.columns == ["count"]
    assert response.rows == [{"count": 3}]
    assert response.chart is None
    assert response.confidence == "medium"
    assert [event.sequence for event in response.trace] == [1, 2]
    assert response.trace[0].message == "开始查询"
    session = await store.GetSession(response.session_id)
    assert session["turns"][0]["id"] == response.turn_id
    assert session["turns"][0]["response_kind"] == "answer"
    assert session["turns"][0]["result_preview"] == [{"count": 3}]
    assert session["turns"][0]["confidence"] == "medium"
    assert session["turns"][0]["trace"] == [
        event.model_dump(mode="json") for event in response.trace
    ]
    await store.Close()


@pytest.mark.asyncio
@pytest.mark.parametrize("returned_error", [False, True])
async def test_graph_failures_are_safe_in_response_and_storage(
    tmp_path, caplog, returned_error
):
    secret = "database-password-secret-7391"
    if returned_error:
        graph = RecordingGraph(
            successful_result(
                answer=secret,
                sql=f"SELECT '{secret}'",
                rows=[{"secret": secret}],
                chart={"title": secret},
                trace=[{"message": secret}],
                error={"detail": secret},
            )
        )
    else:
        graph = RecordingGraph(error=RuntimeError(secret))
    store = await initialized_store(tmp_path)
    service = QueryService(store, graph_factory=lambda: graph)

    response = await service.Run(QueryRequest(database_id="demo", question="失败"))

    assert isinstance(response, ErrorResponse)
    assert response.kind == "error"
    assert response.code == "query_failed"
    assert response.message == "查询失败，请稍后重试或换一种问法。"
    assert response.retryable is True
    assert response.suggestions == ["重试", "换一种问法"]
    assert secret not in json.dumps(response.model_dump(mode="json"), ensure_ascii=False)
    session = await store.GetSession(response.session_id)
    turn = session["turns"][0]
    assert turn["response_kind"] == "error"
    assert secret not in json.dumps(turn, ensure_ascii=False)
    assert turn["error"]["code"] == "query_failed"
    if not returned_error:
        assert secret in caplog.text
    await store.Close()


@pytest.mark.asyncio
async def test_missing_or_cross_database_session_is_replaced_without_history_loss(tmp_path):
    store = await initialized_store(tmp_path)
    old_session_id = await store.CreateSession("old-db")
    await store.SaveTurn(
        old_session_id,
        {
            "id": "old-turn",
            "question": "old question",
            "kind": "answer",
            "answer": "old answer",
            "sql": "SELECT 1",
            "result_preview": [],
            "confidence": "medium",
            "trace": [],
        },
    )
    graph = RecordingGraph()
    service = QueryService(store, graph_factory=lambda: graph)

    response = await service.Run(
        QueryRequest(database_id="new-db", session_id=old_session_id, question="new")
    )

    assert response.session_id != old_session_id
    assert (await store.GetSession(old_session_id))["turns"][0]["question"] == "old question"
    assert (await store.GetSession(response.session_id))["database_id"] == "new-db"
    assert graph.calls == [("new", "new-db", {})]
    await store.Close()


@pytest.mark.asyncio
async def test_missing_supplied_session_is_replaced(tmp_path):
    store = await initialized_store(tmp_path)
    graph = RecordingGraph()
    service = QueryService(store, graph_factory=lambda: graph)

    response = await service.Run(
        QueryRequest(database_id="demo", session_id="missing", question="new")
    )

    assert response.session_id != "missing"
    assert await store.GetSession("missing") is None
    assert (await store.GetSession(response.session_id))["turns"][0]["question"] == "new"
    assert service._locks._entries == {}
    await store.Close()


@pytest.mark.asyncio
async def test_nominal_success_without_sql_returns_safe_no_trustworthy_sql(tmp_path):
    store = await initialized_store(tmp_path)
    graph = RecordingGraph(successful_result(sql=None, answer="metadata secret"))
    service = QueryService(store, graph_factory=lambda: graph)

    response = await service.Run(QueryRequest(database_id="demo", question="no sql"))

    assert isinstance(response, ErrorResponse)
    assert response.code == "no_trustworthy_sql"
    assert "metadata secret" not in json.dumps(response.model_dump(mode="json"))
    turn = (await store.GetSession(response.session_id))["turns"][0]
    assert turn["response_kind"] == "error"
    assert turn["error"]["code"] == "no_trustworthy_sql"
    await store.Close()


@pytest.mark.asyncio
async def test_same_session_runs_serialize_and_second_sees_saved_context(tmp_path):
    store = await initialized_store(tmp_path)
    session_id = await store.CreateSession("demo")
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()
    calls = []

    class BlockingGraph:
        async def ARun(self, question, database_id, session_context=None):
            calls.append((question, session_context))
            if question == "first":
                first_entered.set()
                await release_first.wait()
            else:
                second_entered.set()
            return successful_result(answer=question, sql=f"SELECT '{question}'", trace=[])

    service = QueryService(store, graph_factory=BlockingGraph)
    first = asyncio.create_task(
        service.Run(QueryRequest(database_id="demo", session_id=session_id, question="first"))
    )
    await first_entered.wait()
    second = asyncio.create_task(
        service.Run(QueryRequest(database_id="demo", session_id=session_id, question="second"))
    )
    await asyncio.sleep(0)
    assert not second_entered.is_set()
    release_first.set()
    await asyncio.gather(first, second)

    assert calls == [
        ("first", {}),
        ("second", {"last_question": "first", "last_sql": "SELECT 'first'"}),
    ]
    assert service._locks._entries == {}
    await store.Close()


@pytest.mark.asyncio
async def test_delete_waits_for_in_flight_run_then_deletes(tmp_path):
    store = await initialized_store(tmp_path)
    session_id = await store.CreateSession("demo")
    entered = asyncio.Event()
    release = asyncio.Event()

    class BlockingGraph:
        async def ARun(self, **kwargs):
            entered.set()
            await release.wait()
            return successful_result(trace=[])

    service = QueryService(store, graph_factory=BlockingGraph)
    run = asyncio.create_task(
        service.Run(QueryRequest(database_id="demo", session_id=session_id, question="slow"))
    )
    await entered.wait()
    delete = asyncio.create_task(service.DeleteSession(session_id))
    await asyncio.sleep(0)
    assert not delete.done()
    release.set()

    response, deleted = await asyncio.gather(run, delete)
    assert response.kind == "answer"
    assert deleted is True
    assert await store.GetSession(session_id) is None
    assert service._locks._entries == {}
    await store.Close()


@pytest.mark.asyncio
async def test_rich_values_normalize_deterministically_for_response_and_storage(tmp_path):
    class State(Enum):
        READY = "ready"

    item_id = UUID("12345678-1234-5678-1234-567812345678")
    timestamp = datetime(2026, 7, 15, 10, 30, tzinfo=timezone.utc)
    row = {
        "amount": Decimal("12.50"),
        "timestamp": timestamp,
        "date": date(2026, 7, 15),
        "time": time(10, 30),
        "id": item_id,
        "state": State.READY,
        "payload": b"\xff\x00",
    }
    store = await initialized_store(tmp_path)
    service = QueryService(
        store,
        graph_factory=lambda: RecordingGraph(
            successful_result(
                columns=list(row),
                rows=[row],
                chart={
                    "type": "vertical_bar",
                    "title": b"\xff\x00",
                    "category_field": None,
                    "value_fields": ["amount"],
                    "value_labels": {"amount": Decimal("12.50")},
                    "reason": "comparison",
                },
                trace=[
                    {"step": "rich", "status": "success", "message": timestamp}
                ],
            )
        ),
    )

    response = await service.Run(QueryRequest(database_id="demo", question="rich"))

    expected = {
        "amount": "12.50",
        "timestamp": "2026-07-15T10:30:00+00:00",
        "date": "2026-07-15",
        "time": "10:30:00",
        "id": str(item_id),
        "state": "ready",
        "payload": base64.b64encode(b"\xff\x00").decode("ascii"),
    }
    assert response.rows == [expected]
    turn = (await store.GetSession(response.session_id))["turns"][0]
    assert turn["result_preview"] == [expected]
    assert response.chart.title == "/wA="
    assert response.chart.value_labels == {"amount": "12.50"}
    assert turn["chart"] == response.chart.model_dump(mode="json")
    assert response.trace[0].message == "2026-07-15T10:30:00+00:00"
    assert turn["trace"] == [response.trace[0].model_dump(mode="json")]
    await store.Close()


@pytest.mark.asyncio
async def test_response_kind_survives_get_session(tmp_path):
    store = await initialized_store(tmp_path)
    service = QueryService(store, graph_factory=lambda: RecordingGraph())

    response = await service.Run(QueryRequest(database_id="demo", question="kind"))

    turn = (await store.GetSession(response.session_id))["turns"][0]
    assert turn["response_kind"] == response.kind == "answer"
    await store.Close()


@pytest.mark.asyncio
async def test_clarification_raises_stable_domain_error(tmp_path):
    store = await initialized_store(tmp_path)
    service = QueryService(store, graph_factory=lambda: RecordingGraph())
    request = QueryRequest(
        database_id="demo",
        clarification={"clarification_id": "clarify-1", "option_id": "2024"},
    )

    with pytest.raises(QueryClarificationUnsupported, match="Only question queries are supported"):
        await service.Run(request)
    await store.Close()
