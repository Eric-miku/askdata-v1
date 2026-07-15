from contextlib import asynccontextmanager
import asyncio
import json
from pathlib import Path
import sys

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
    assert [name for name, _ in frames] == ["final"]
    assert frames[0][1]["kind"] == "error"
    assert frames[0][1]["code"] == "query_failed"
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
