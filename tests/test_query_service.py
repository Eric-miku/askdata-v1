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

from askdata.api.query_service import QueryService
from askdata.api.response_models import AnswerResponse, ClarificationResponse, ErrorResponse
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
    assert [event.sequence for event in response.trace] == [1]
    assert response.trace[0].step == "ExecuteSql"
    assert response.trace[0].message == "已执行查询"
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
@pytest.mark.parametrize("error_value", ["", {}, False, 0, []])
async def test_present_non_none_error_is_failure_even_when_falsey(tmp_path, error_value):
    store = await initialized_store(tmp_path)
    graph = RecordingGraph(successful_result(error=error_value))
    service = QueryService(store, graph_factory=lambda: graph)

    response = await service.Run(QueryRequest(database_id="demo", question="error"))

    assert isinstance(response, ErrorResponse)
    assert response.code == "query_failed"
    assert (await store.GetSession(response.session_id))["turns"][0]["response_kind"] == "error"
    await store.Close()


@pytest.mark.asyncio
@pytest.mark.parametrize("sql", [None, 123, False, "", "   ", "\n\t"])
async def test_missing_non_string_or_blank_sql_is_not_trustworthy(tmp_path, sql):
    store = await initialized_store(tmp_path)
    graph = RecordingGraph(successful_result(sql=sql))
    service = QueryService(store, graph_factory=lambda: graph)

    response = await service.Run(QueryRequest(database_id="demo", question="bad sql"))

    assert isinstance(response, ErrorResponse)
    assert response.code == "no_trustworthy_sql"
    await store.Close()


@pytest.mark.asyncio
async def test_sql_is_stripped_before_response_and_storage(tmp_path):
    store = await initialized_store(tmp_path)
    graph = RecordingGraph(successful_result(sql="  SELECT 1  "))
    service = QueryService(store, graph_factory=lambda: graph)

    response = await service.Run(QueryRequest(database_id="demo", question="strip"))

    assert response.sql == "SELECT 1"
    assert (await store.GetSession(response.session_id))["turns"][0]["sql"] == "SELECT 1"
    await store.Close()


@pytest.mark.asyncio
async def test_rows_and_operational_trace_are_bounded_with_storage_parity(tmp_path):
    secret = "trace-secret-7391"
    trace = [
        {
            "step": "ExecuteSql",
            "status": "success",
            "message": secret * 100,
        }
        for _ in range(60)
    ]
    store = await initialized_store(tmp_path)
    graph = RecordingGraph(
        successful_result(rows=[{"id": index} for index in range(150)], trace=trace)
    )
    service = QueryService(store, graph_factory=lambda: graph)

    response = await service.Run(QueryRequest(database_id="demo", question="bounded"))

    assert len(response.rows) == 100
    assert len(response.trace) == 50
    assert all(len(event.message) <= 300 for event in response.trace)
    assert secret not in json.dumps(response.model_dump(mode="json"))
    turn = (await store.GetSession(response.session_id))["turns"][0]
    assert turn["result_preview"] == response.rows
    assert turn["trace"] == [event.model_dump(mode="json") for event in response.trace]
    await store.Close()


@pytest.mark.asyncio
async def test_unsupported_content_beyond_preview_caps_is_not_inspected(tmp_path):
    unsupported = object()
    rows = [{"id": index} for index in range(100)] + [unsupported]
    trace = [
        {"step": "ExecuteSql", "status": "success", "message": "ignored"}
        for _ in range(50)
    ] + [unsupported]
    store = await initialized_store(tmp_path)
    graph = RecordingGraph(successful_result(rows=rows, trace=trace))
    service = QueryService(store, graph_factory=lambda: graph)

    response = await service.Run(QueryRequest(database_id="demo", question="bounded"))

    assert isinstance(response, AnswerResponse)
    assert response.rows == rows[:100]
    assert len(response.trace) == 50
    turn = (await store.GetSession(response.session_id))["turns"][0]
    assert turn["result_preview"] == rows[:100]
    assert turn["trace"] == [event.model_dump(mode="json") for event in response.trace]
    await store.Close()


@pytest.mark.asyncio
async def test_supplied_non_list_rows_returns_safe_query_failed(tmp_path):
    store = await initialized_store(tmp_path)
    graph = RecordingGraph(successful_result(rows={"id": 1}))
    service = QueryService(store, graph_factory=lambda: graph)

    response = await service.Run(QueryRequest(database_id="demo", question="malformed"))

    assert isinstance(response, ErrorResponse)
    assert response.code == "query_failed"
    turn = (await store.GetSession(response.session_id))["turns"][0]
    assert turn["response_kind"] == "error"
    assert turn["result_preview"] == []
    await store.Close()


@pytest.mark.asyncio
@pytest.mark.parametrize("include_rows", [False, True])
async def test_missing_or_none_rows_is_an_empty_preview(tmp_path, include_rows):
    result = successful_result(rows=None)
    if not include_rows:
        result.pop("rows")
    store = await initialized_store(tmp_path)
    graph = RecordingGraph(result)
    service = QueryService(store, graph_factory=lambda: graph)

    response = await service.Run(QueryRequest(database_id="demo", question="empty"))

    assert isinstance(response, AnswerResponse)
    assert response.rows == []
    assert (await store.GetSession(response.session_id))["turns"][0]["result_preview"] == []
    await store.Close()


@pytest.mark.asyncio
async def test_trace_exposes_only_curated_operational_events(tmp_path):
    secret = "operational-secret-7391"
    graph = RecordingGraph(
        successful_result(
            trace=[
                f"Reason-1 {secret}",
                {"step": "Reason-2", "status": "success", "message": secret},
                {
                    "step": "GenerateSql",
                    "status": "success",
                    "message": f"SELECT '{secret}'",
                    "unknown": secret,
                },
                {
                    "step": "ValidateSql",
                    "status": "retry",
                    "message": f"database error {secret}",
                },
                {"step": "Unknown", "status": "error", "message": secret},
            ]
        )
    )
    store = await initialized_store(tmp_path)
    service = QueryService(store, graph_factory=lambda: graph)

    response = await service.Run(QueryRequest(database_id="demo", question="trace"))

    assert [(event.step, event.status, event.message) for event in response.trace] == [
        ("GenerateSql", "success", "已生成查询语句"),
        ("ValidateSql", "retry", "正在校验查询语句"),
    ]
    assert secret not in json.dumps(response.model_dump(mode="json"), ensure_ascii=False)
    turn = (await store.GetSession(response.session_id))["turns"][0]
    assert turn["trace"] == [event.model_dump(mode="json") for event in response.trace]
    assert secret not in json.dumps(turn, ensure_ascii=False)
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
    assert response.trace[0].step == "QueryComplete"
    assert response.trace[0].message == "查询完成"
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
async def test_material_clarification_persists_original_turn_and_pending_options(tmp_path):
    store = await initialized_store(tmp_path)
    graph = RecordingGraph(
        {
            "kind": "clarification",
            "question": "Which revenue definition should I use?",
            "options": [
                {
                    "id": "gross",
                    "label": "Gross revenue",
                    "description": "Before deductions",
                    "interpretation": {"metric": "gross_revenue"},
                },
                {
                    "id": "net",
                    "label": "Net revenue",
                    "description": "After deductions",
                    "interpretation": {"metric": "net_revenue"},
                },
            ],
            "trace": [{"step": "RetrieveSchema", "status": "success"}],
        }
    )
    service = QueryService(store, graph_factory=lambda: graph)

    response = await service.Run(QueryRequest(database_id="demo", question="show revenue"))

    assert isinstance(response, ClarificationResponse)
    assert response.question == "Which revenue definition should I use?"
    assert [option.id for option in response.options] == ["gross", "net"]
    session = await store.GetSession(response.session_id)
    assert len(session["turns"]) == 1
    turn = session["turns"][0]
    assert turn["id"] == response.turn_id
    assert turn["question"] == "show revenue"
    assert turn["response_kind"] == "clarification"
    assert turn["clarification"]["id"] == response.clarification_id
    assert turn["clarification"]["status"] == "pending"
    assert turn["clarification"]["options"][1]["interpretation"] == {
        "metric": "net_revenue"
    }
    await store.Close()


@pytest.mark.asyncio
async def test_unanswerable_schema_result_is_nonretryable_and_persisted(tmp_path):
    store = await initialized_store(tmp_path)
    graph = RecordingGraph(
        {
            "kind": "error",
            "error": "unanswerable_from_schema",
            "missing_concepts": ["students"],
            "trace": [],
        }
    )
    service = QueryService(store, graph_factory=lambda: graph)

    response = await service.Run(
        QueryRequest(database_id="demo", question="list student names")
    )

    assert isinstance(response, ErrorResponse)
    assert response.code == "unanswerable_from_schema"
    assert response.retryable is False
    assert "students" in response.message
    turn = (await store.GetSession(response.session_id))["turns"][0]
    assert turn["response_kind"] == "error"
    assert turn["error"]["code"] == "unanswerable_from_schema"
    await store.Close()


@pytest.mark.asyncio
async def test_option_continuation_resolves_and_updates_same_analytical_turn(tmp_path):
    store = await initialized_store(tmp_path)
    graph = RecordingGraph(
        {
            "kind": "clarification",
            "question": "Gross or net?",
            "options": [
                {"id": "gross", "label": "Gross revenue", "interpretation": {"metric": "gross_revenue"}},
                {"id": "net", "label": "Net revenue", "interpretation": {"metric": "net_revenue"}},
            ],
            "trace": [],
        }
    )
    service = QueryService(store, graph_factory=lambda: graph)
    pending = await service.Run(QueryRequest(database_id="demo", question="show revenue"))
    graph.result = successful_result(answer="Net is 8", sql="SELECT net_revenue FROM sales")

    response = await service.Run(
        QueryRequest(
            database_id="demo",
            session_id=pending.session_id,
            clarification={"clarification_id": pending.clarification_id, "option_id": "net"},
        )
    )

    assert isinstance(response, AnswerResponse)
    assert response.turn_id == pending.turn_id
    assert graph.calls[-1][0] == (
        "show revenue\nSelected interpretation: Net revenue. metric: net_revenue"
    )
    session = await store.GetSession(pending.session_id)
    assert len(session["turns"]) == 1
    turn = session["turns"][0]
    assert turn["id"] == pending.turn_id
    assert turn["question"] == "show revenue"
    assert turn["response_kind"] == "answer"
    assert turn["clarification"]["status"] == "resolved"
    assert turn["clarification"]["resolution"] == {"option_id": "net"}
    await store.Close()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "resolved", "wrong-session", "bad-option"])
async def test_invalid_or_nonpending_clarification_is_stable_nonretryable_error(tmp_path, case):
    store = await initialized_store(tmp_path)
    graph = RecordingGraph(
        {
            "kind": "clarification",
            "question": "Gross or net?",
            "options": [
                {"id": "gross", "label": "Gross revenue", "interpretation": {"metric": "gross_revenue"}},
                {"id": "net", "label": "Net revenue", "interpretation": {"metric": "net_revenue"}},
            ],
            "trace": [],
        }
    )
    service = QueryService(store, graph_factory=lambda: graph)
    pending = await service.Run(QueryRequest(database_id="demo", question="show revenue"))
    session_id = pending.session_id
    clarification_id = pending.clarification_id
    option_id = "gross"
    if case == "missing":
        clarification_id = "missing"
    elif case == "wrong-session":
        session_id = await store.CreateSession("demo")
    elif case == "bad-option":
        option_id = "unknown"
    elif case == "resolved":
        await store.ResolveClarification(
            pending.session_id, pending.clarification_id, {"option_id": "gross"}
        )
    calls_before = len(graph.calls)

    response = await service.Run(
        QueryRequest(
            database_id="demo",
            session_id=session_id,
            clarification={"clarification_id": clarification_id, "option_id": option_id},
        )
    )

    assert isinstance(response, ErrorResponse)
    assert response.code == "invalid_clarification"
    assert response.retryable is False
    assert len(graph.calls) == calls_before
    owner = await store.GetSession(pending.session_id)
    if case != "resolved":
        assert owner["turns"][0]["clarification"]["status"] == "pending"
    await store.Close()


@pytest.mark.asyncio
async def test_free_text_continuation_combines_original_question_and_reuses_turn(tmp_path):
    store = await initialized_store(tmp_path)
    graph = RecordingGraph(
        {
            "kind": "clarification",
            "question": "Which period?",
            "options": [
                {"id": "recent", "label": "Most recent year"},
                {"id": "all", "label": "All years"},
            ],
            "trace": [],
        }
    )
    service = QueryService(store, graph_factory=lambda: graph)
    pending = await service.Run(QueryRequest(database_id="demo", question="show revenue"))
    graph.result = successful_result()

    response = await service.Run(
        QueryRequest(
            database_id="demo",
            session_id=pending.session_id,
            clarification={
                "clarification_id": pending.clarification_id,
                "text": "Use fiscal year 2025",
            },
        )
    )

    assert response.turn_id == pending.turn_id
    assert graph.calls[-1][0] == "show revenue\nUser clarification: Use fiscal year 2025"
    assert len((await store.GetSession(pending.session_id))["turns"]) == 1
    await store.Close()
