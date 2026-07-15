from contextlib import asynccontextmanager
import asyncio
import json
from pathlib import Path
import sys
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.api import routes
from askdata.api.query_service import EncodeSse, QueryService
from askdata.api.schemas import QueryRequest
from askdata.api.session_store import SessionStore


class StreamGraph:
    async def ARun(self, question, database_id, session_context=None):
        return {
            "answer": "共有 3 条。",
            "sql": "SELECT COUNT(*) AS count FROM items",
            "columns": ["count"],
            "rows": [{"count": 3}],
            "chart": None,
            "trace": [
                {
                    "step": "RetrieveSchema",
                    "status": "success",
                    "message": "raw schema detail",
                },
                {
                    "step": "ExecuteSql",
                    "status": "success",
                    "message": "raw SQL and database details",
                },
            ],
            "error": None,
        }


@pytest.fixture
def client(tmp_path):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = SessionStore(tmp_path / "sessions.sqlite")
        await store.Initialize()
        app.state.session_store = store
        app.state.query_service = QueryService(store, graph_factory=StreamGraph)
        try:
            yield
        finally:
            await store.Close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(routes.router, prefix="/api")
    with TestClient(app) as test_client:
        yield test_client


def _frames(body: str):
    parsed = []
    for frame in body.split("\n\n"):
        if not frame:
            continue
        lines = frame.splitlines()
        parsed.append(
            (
                lines[0].removeprefix("event: "),
                json.loads(lines[1].removeprefix("data: ")),
            )
        )
    return parsed


def test_query_stream_emits_ordered_trace_then_final(client):
    with client.stream(
        "POST",
        "/api/query/stream",
        json={"question": "How many?", "database_id": "demo"},
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = _frames(body)
    assert [name for name, _ in frames] == ["trace", "trace", "final"]
    assert [payload["sequence"] for name, payload in frames if name == "trace"] == [1, 2]
    assert frames[-1][1]["kind"] == "answer"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"


def test_encode_sse_is_compact_single_line_and_rejects_newline_event_names():
    frame = EncodeSse("trace", {"message": "first\nsecond", "sequence": 1})

    assert frame == (
        'event: trace\n'
        'data: {"message":"first\\nsecond","sequence":1}\n\n'
    )
    assert frame.count("\n\n") == 1
    with pytest.raises(ValueError, match="event name"):
        EncodeSse("trace\nevent: final", {})
    with pytest.raises(ValueError, match="event name"):
        EncodeSse("trace\rfinal", {})


def test_stream_trace_is_curated_and_final_matches_non_streaming_response(client):
    direct = client.post(
        "/api/query", json={"question": "How many?", "database_id": "demo"}
    ).json()
    with client.stream(
        "POST",
        "/api/query/stream",
        json={
            "question": "How many?",
            "database_id": "demo",
            "session_id": direct["session_id"],
        },
    ) as response:
        body = "".join(response.iter_text())

    frames = _frames(body)
    trace_payloads = [payload for name, payload in frames if name == "trace"]
    final_payloads = [payload for name, payload in frames if name == "final"]
    assert len(final_payloads) == 1
    assert trace_payloads == final_payloads[0]["trace"]
    assert "raw schema detail" not in json.dumps(trace_payloads)
    assert "raw SQL and database details" not in json.dumps(trace_payloads)
    streamed = final_payloads[0]
    for generated_field in ("turn_id",):
        direct.pop(generated_field)
        streamed.pop(generated_field)
    assert streamed == direct


@pytest.mark.asyncio
async def test_stream_converts_unexpected_task_exception_to_one_safe_final(tmp_path):
    secret = "stream-task-secret-7391"
    store = SessionStore(tmp_path / "sessions.sqlite")
    await store.Initialize()

    class ExplodingService(QueryService):
        async def Run(self, request, emit=None):
            raise RuntimeError(secret)

    service = ExplodingService(store)
    body = "".join(
        [
            frame
            async for frame in service.Stream(
                QueryRequest(question="fail", database_id="demo")
            )
        ]
    )

    frames = _frames(body)
    assert [name for name, _ in frames] == ["error", "final"]
    assert frames[-1][1]["kind"] == "error"
    assert frames[-1][1]["code"] == "query_failed"
    assert secret not in body
    await store.Close()


@pytest.mark.asyncio
async def test_stream_consumer_cancellation_cancels_query_and_releases_lock(tmp_path):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class BlockingGraph:
        async def ARun(self, question, database_id, session_context=None):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    store = SessionStore(tmp_path / "sessions.sqlite")
    await store.Initialize()
    service = QueryService(store, graph_factory=BlockingGraph)
    stream = service.Stream(QueryRequest(question="wait", database_id="demo"))
    consumer = asyncio.create_task(stream.__anext__())
    await asyncio.wait_for(started.wait(), timeout=1)

    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    await asyncio.wait_for(cancelled.wait(), timeout=1)

    assert service._locks._entries == {}
    await store.Close()


@pytest.mark.asyncio
async def test_stream_uses_queue_with_maxsize_100(tmp_path, monkeypatch):
    import askdata.api.query_service as query_service_module

    sizes = []
    real_queue = asyncio.Queue

    def recording_queue(maxsize=0):
        sizes.append(maxsize)
        return real_queue(maxsize=maxsize)

    monkeypatch.setattr(query_service_module.asyncio, "Queue", recording_queue)
    store = SessionStore(tmp_path / "sessions.sqlite")
    await store.Initialize()
    service = QueryService(store, graph_factory=StreamGraph)

    frames = [
        frame
        async for frame in service.Stream(
            QueryRequest(question="bounded", database_id="demo")
        )
    ]

    assert sizes == [100]
    assert sum(frame.startswith("event: final\n") for frame in frames) == 1
    await store.Close()


@pytest.mark.asyncio
async def test_stream_emits_live_worker_trace_before_graph_finishes(tmp_path):
    worker_started = threading.Event()
    release_worker = threading.Event()
    worker_finished = threading.Event()

    class ThreadedLiveGraph:
        async def ARun(self, question, database_id, session_context=None, emit=None):
            def run():
                worker_started.set()
                emit(
                    {
                        "step": "RetrieveSchema",
                        "status": "started",
                        "message": "secret schema and chain of thought",
                    }
                )
                release_worker.wait(timeout=2)
                worker_finished.set()
                return stream_graph_result()

            return await asyncio.to_thread(run)

    store = SessionStore(tmp_path / "sessions.sqlite")
    await store.Initialize()
    service = QueryService(store, graph_factory=ThreadedLiveGraph)
    stream = service.Stream(QueryRequest(question="live", database_id="demo"))
    first_frame_task = asyncio.create_task(stream.__anext__())
    try:
        assert await asyncio.to_thread(worker_started.wait, 1)
        first_frame = await asyncio.wait_for(first_frame_task, timeout=1)
        assert first_frame.startswith("event: trace\n")
        first_payload = _frames(first_frame)[0][1]
        assert first_payload["step"] == "RetrieveSchema"
        assert first_payload["status"] == "started"
        assert first_payload["sequence"] == 1
        assert "secret schema" not in first_frame
        assert not worker_finished.is_set()
    finally:
        release_worker.set()

    remaining = [frame async for frame in stream]
    assert worker_finished.is_set()
    assert sum(frame.startswith("event: final\n") for frame in remaining) == 1
    await store.Close()


@pytest.mark.asyncio
async def test_disconnect_releases_lock_while_to_thread_worker_exits_cooperatively(
    tmp_path,
):
    release_worker = threading.Event()
    worker_finished = threading.Event()

    class CooperativeThreadGraph:
        async def ARun(self, question, database_id, session_context=None, emit=None):
            def run():
                emit({"step": "RetrieveSchema", "status": "started", "message": "raw"})
                release_worker.wait(timeout=2)
                emit({"step": "ExecuteSql", "status": "success", "message": "raw"})
                worker_finished.set()
                return stream_graph_result()

            return await asyncio.to_thread(run)

    store = SessionStore(tmp_path / "sessions.sqlite")
    await store.Initialize()
    service = QueryService(store, graph_factory=CooperativeThreadGraph)
    stream = service.Stream(QueryRequest(question="cancel", database_id="demo"))

    assert (await stream.__anext__()).startswith("event: trace\n")
    await stream.aclose()
    assert service._locks._entries == {}

    release_worker.set()
    assert await asyncio.to_thread(worker_finished.wait, 1)
    await asyncio.sleep(0)
    assert service._locks._entries == {}
    await store.Close()


def stream_graph_result():
    return {
        "answer": "共有 3 条。",
        "sql": "SELECT COUNT(*) AS count FROM items",
        "columns": ["count"],
        "rows": [{"count": 3}],
        "chart": None,
        "trace": [
            {
                "step": "RetrieveSchema",
                "status": "started",
                "message": "secret schema and chain of thought",
            }
        ],
        "error": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("graph_result", "terminal_name", "response_kind"),
    [
        (
            {
                "kind": "clarification",
                "question": "Which revenue metric?",
                "options": [
                    {"id": "gross", "label": "Gross revenue"},
                    {"id": "net", "label": "Net revenue"},
                ],
                "trace": [
                    {
                        "step": "RetrieveSchema",
                        "status": "success",
                        "message": "raw",
                    }
                ],
            },
            "clarification",
            "clarification",
        ),
        ({"error": {"private": "secret"}}, "error", "error"),
    ],
)
async def test_stream_uses_distinct_terminal_event_for_non_answer_response(
    tmp_path, graph_result, terminal_name, response_kind
):
    class TerminalGraph:
        async def ARun(self, question, database_id, session_context=None):
            return graph_result

    store = SessionStore(tmp_path / "sessions.sqlite")
    await store.Initialize()
    service = QueryService(store, graph_factory=TerminalGraph)

    body = "".join(
        [
            frame
            async for frame in service.Stream(
                QueryRequest(question="terminal", database_id="demo")
            )
        ]
    )

    frames = _frames(body)
    terminal_frames = [frame for frame in frames if frame[0] != "trace"]
    assert [name for name, _ in terminal_frames] == [terminal_name, "final"]
    assert terminal_frames[0][1]["kind"] == response_kind
    assert terminal_frames[-1][1]["kind"] == response_kind
    assert frames[-1][0] == "final"
    assert sum(name == "final" for name, _ in frames) == 1
    await store.Close()
