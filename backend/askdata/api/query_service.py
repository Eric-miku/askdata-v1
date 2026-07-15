"""Application-scoped orchestration for natural-language queries."""

import asyncio
import base64
import inspect
import json
import logging
import threading
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any, AsyncIterator, Callable, Mapping

from pydantic import BaseModel

from askdata.agent.graph import AgentGraph
from askdata.api.response_models import (
    AnswerResponse,
    ClarificationOption,
    ClarificationResponse,
    ErrorResponse,
    QueryResponse,
    TraceEvent,
)
from askdata.api.schemas import QueryRequest
from askdata.api.session_store import SessionStore


logger = logging.getLogger(__name__)

_QUERY_FAILED_MESSAGE = "查询失败，请稍后重试或换一种问法。"
_QUERY_FAILED_SUGGESTIONS = ["重试", "换一种问法"]
_TRACE_STATUSES = {"started", "success", "retry", "warning", "error"}
_ROW_PREVIEW_LIMIT = 100
_TRACE_EVENT_LIMIT = 50
_TRACE_MESSAGE_LIMIT = 300
_OPERATIONAL_TRACE_MESSAGES = {
    "RetrieveSchema": "已匹配数据库结构",
    "GenerateSql": "已生成查询语句",
    "ValidateSql": "正在校验查询语句",
    "ExecuteSql": "已执行查询",
    "RepairSql": "已修复查询语句",
    "ReviewAnswerShape": "已检查回答结构",
    "AnalyzeResult": "已生成查询结果",
}


class QueryClarificationUnsupported(ValueError):
    """Compatibility exception retained for callers from before V2 clarification support."""


def EncodeSse(event_name: str, payload: Mapping[str, Any]) -> str:
    """Encode one compact server-sent event frame."""
    if "\r" in event_name or "\n" in event_name:
        raise ValueError("SSE event name must not contain a newline")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event_name}\ndata: {data}\n\n"


def _JsonSafe(value: Any) -> Any:
    """Return a deterministic JSON-compatible representation of agent output."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Enum):
        return _JsonSafe(value.value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, BaseModel):
        return _JsonSafe(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        return {
            str(key): _JsonSafe(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_JsonSafe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized = [_JsonSafe(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False),
        )
    raise TypeError(f"Unsupported query result value: {type(value).__name__}")


class _KeyedLockManager:
    """Reference-counted per-key locks; references include queued waiters."""

    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._entries: dict[str, dict[str, Any]] = {}

    @asynccontextmanager
    async def Hold(self, key: str) -> AsyncIterator[None]:
        async with self._guard:
            entry = self._entries.get(key)
            if entry is None:
                entry = {"lock": asyncio.Lock(), "refs": 0}
                self._entries[key] = entry
            entry["refs"] += 1

        acquired = False
        try:
            await entry["lock"].acquire()
            acquired = True
            yield
        finally:
            if acquired:
                entry["lock"].release()
            async with self._guard:
                entry["refs"] -= 1
                if entry["refs"] == 0 and self._entries.get(key) is entry:
                    del self._entries[key]


class QueryService:
    """Resolve sessions, invoke the graph, normalize results, and save turns."""

    def __init__(
        self,
        store: SessionStore,
        graph_factory: Callable[[], Any] = AgentGraph,
    ) -> None:
        self._store = store
        self._graph_factory = graph_factory
        self._locks = _KeyedLockManager()

    async def Run(
        self,
        request: QueryRequest,
        emit: Callable[[TraceEvent], Any] | None = None,
    ) -> QueryResponse:
        loop = asyncio.get_running_loop()
        emitted = 0
        emit_lock = threading.Lock()
        pending_emits: list[Any] = []

        def dispatch(event: TraceEvent) -> None:
            nonlocal emitted
            with emit_lock:
                if emitted >= _TRACE_EVENT_LIMIT:
                    return
                emitted += 1
                sequence = emitted
            delivered = event.model_copy(update={"sequence": sequence})
            result = emit(delivered) if emit is not None else None
            if inspect.isawaitable(result):
                try:
                    current_loop = asyncio.get_running_loop()
                except RuntimeError:
                    current_loop = None
                if current_loop is loop:
                    pending = loop.create_task(result)
                else:
                    pending = asyncio.run_coroutine_threadsafe(result, loop)
                with emit_lock:
                    pending_emits.append(pending)

        response = await self._Run(request, emit=dispatch if emit is not None else None)
        if emit is not None and emitted == 0:
            for event in response.trace:
                dispatch(event)

        with emit_lock:
            pending = list(pending_emits)
        for delivery in pending:
            if isinstance(delivery, asyncio.Future):
                await delivery
            else:
                await asyncio.wrap_future(delivery)
        return response

    async def Stream(self, request: QueryRequest) -> AsyncIterator[str]:
        """Stream sanitized operational events followed by exactly one final response."""
        queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue(
            maxsize=100
        )

        loop = asyncio.get_running_loop()
        accepting_events = threading.Event()
        accepting_events.set()

        def emit(event: TraceEvent) -> None:
            if not accepting_events.is_set():
                return

            def enqueue() -> None:
                if not accepting_events.is_set():
                    return
                try:
                    queue.put_nowait(("trace", event.model_dump(mode="json")))
                except asyncio.QueueFull:
                    logger.warning("Dropping excess streaming trace event")

            try:
                current_loop = asyncio.get_running_loop()
            except RuntimeError:
                current_loop = None
            if current_loop is loop:
                enqueue()
            else:
                try:
                    loop.call_soon_threadsafe(enqueue)
                except RuntimeError:
                    if accepting_events.is_set():
                        raise

        def terminal_name(response: QueryResponse) -> str:
            if isinstance(response, ClarificationResponse):
                return "clarification"
            if isinstance(response, ErrorResponse):
                return "error"
            return "final"

        async def produce() -> None:
            try:
                response = await self.Run(request, emit=emit)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Streaming query execution failed")
                response = self._QueryFailed(
                    request.session_id or "", str(uuid.uuid4())
                )
            barrier = loop.create_future()
            loop.call_soon(barrier.set_result, None)
            await barrier
            await queue.put(
                (terminal_name(response), response.model_dump(mode="json"))
            )
            await queue.put(None)

        task = asyncio.create_task(produce())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield EncodeSse(event[0], event[1])
        finally:
            accepting_events.clear()
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _Run(
        self,
        request: QueryRequest,
        emit: Callable[[TraceEvent], None] | None = None,
    ) -> QueryResponse:
        if request.clarification is not None:
            return await self._ContinueClarification(request, emit=emit)
        if not request.question:
            return self._InvalidClarification(request.session_id or "", "")

        session_id = request.session_id
        if not session_id:
            session_id = await self._store.CreateSession(request.database_id)

        while True:
            async with self._locks.Hold(session_id):
                session = await self._store.GetSession(session_id)
                if session is None or session["database_id"] != request.database_id:
                    replacement_required = True
                else:
                    return await self._RunInSession(
                        request, session_id, session, emit=emit
                    )

            if replacement_required:
                session_id = await self._store.CreateSession(request.database_id)

    async def DeleteSession(self, session_id: str) -> bool:
        async with self._locks.Hold(session_id):
            return await self._store.DeleteSession(session_id)

    async def _RunInSession(
        self,
        request: QueryRequest,
        session_id: str,
        session: Mapping[str, Any],
        *,
        turn_id: str | None = None,
        graph_question: str | None = None,
        stored_question: str | None = None,
        emit: Callable[[TraceEvent], None] | None = None,
    ) -> QueryResponse:
        turn_id = turn_id or str(uuid.uuid4())
        question = graph_question or request.question or ""
        original_question = stored_question or request.question or ""
        history = session.get("turns") or []
        context: dict[str, Any] = {}
        if history:
            context = {
                "last_question": history[-1].get("question"),
                "last_sql": history[-1].get("sql"),
            }

        try:
            result = await self._RunGraph(
                question=question,
                database_id=request.database_id,
                session_context=context,
                emit=emit,
            )
            if not isinstance(result, Mapping):
                response = self._QueryFailed(session_id, turn_id)
            elif result.get("kind") == "clarification":
                return await self._SaveClarification(
                    session_id, turn_id, original_question, result
                )
            elif result.get("error") == "unanswerable_from_schema":
                response = self._Unanswerable(
                    session_id, turn_id, result.get("missing_concepts")
                )
            elif "error" in result and result["error"] is not None:
                response = self._QueryFailed(session_id, turn_id)
            else:
                sql = result.get("sql")
                if not isinstance(sql, str) or not sql.strip():
                    response = self._NoTrustworthySql(session_id, turn_id)
                else:
                    rows = result.get("rows")
                    if rows is not None and not isinstance(rows, list):
                        response = self._QueryFailed(session_id, turn_id)
                    else:
                        bounded_rows = _JsonSafe((rows or [])[:_ROW_PREVIEW_LIMIT])
                        response = AnswerResponse(
                            session_id=session_id,
                            turn_id=turn_id,
                            answer=str(_JsonSafe(result.get("answer", ""))),
                            sql=sql.strip(),
                            columns=_JsonSafe(result.get("columns") or []),
                            rows=bounded_rows,
                            chart=_JsonSafe(result.get("chart")),
                            confidence="medium",
                            trace=self._NormalizeTrace(result.get("trace")),
                        )
        except Exception:
            logger.exception("Query execution failed")
            response = self._QueryFailed(session_id, turn_id)

        await self._store.SaveTurn(
            session_id,
            self._TurnForStorage(original_question, response),
        )
        return response

    async def _RunGraph(
        self,
        *,
        question: str,
        database_id: str,
        session_context: Mapping[str, Any],
        emit: Callable[[TraceEvent], None] | None,
    ) -> Any:
        graph = self._graph_factory()
        graph_emit: Callable[[Any], None] | None = None
        if emit is not None:

            def safe_graph_emit(raw_event: Any) -> None:
                event = self._NormalizeTraceItem(raw_event)
                if event is not None:
                    emit(event)

            graph_emit = safe_graph_emit

        run = graph.ARun
        parameters = inspect.signature(run).parameters
        accepts_emit = "emit" in parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        kwargs = {
            "question": question,
            "database_id": database_id,
            "session_context": dict(session_context),
        }
        if accepts_emit:
            kwargs["emit"] = graph_emit
        return await run(**kwargs)

    async def _SaveClarification(
        self,
        session_id: str,
        turn_id: str,
        original_question: str,
        result: Mapping[str, Any],
    ) -> QueryResponse:
        prompt = result.get("question")
        raw_options = result.get("options")
        if not isinstance(prompt, str) or not prompt.strip() or not isinstance(raw_options, list):
            response = self._QueryFailed(session_id, turn_id)
            await self._store.SaveTurn(
                session_id, self._TurnForStorage(original_question, response)
            )
            return response
        try:
            stored_options = _JsonSafe(raw_options)
            public_options = [
                ClarificationOption.model_validate(
                    {
                        "id": option.get("id"),
                        "label": option.get("label"),
                        "description": option.get("description"),
                    }
                )
                for option in stored_options
                if isinstance(option, Mapping)
            ]
            if len(public_options) < 2:
                raise ValueError("Clarification requires two supported options")
        except (TypeError, ValueError):
            response = self._QueryFailed(session_id, turn_id)
            await self._store.SaveTurn(
                session_id, self._TurnForStorage(original_question, response)
            )
            return response

        trace = self._NormalizeTrace(result.get("trace"))
        await self._store.SaveTurn(
            session_id,
            {
                "id": turn_id,
                "question": original_question,
                "response_kind": "clarification",
                "result_preview": [],
                "trace": [event.model_dump(mode="json") for event in trace],
            },
        )
        pending = await self._store.CreateClarification(
            turn_id, prompt.strip(), stored_options
        )
        return ClarificationResponse(
            session_id=session_id,
            turn_id=turn_id,
            clarification_id=pending["id"],
            question=prompt.strip(),
            options=public_options,
            recommended_option_id=result.get("recommended_option_id"),
            trace=trace,
        )

    async def _ContinueClarification(
        self,
        request: QueryRequest,
        emit: Callable[[TraceEvent], None] | None = None,
    ) -> QueryResponse:
        resolution = request.clarification
        session_id = request.session_id or ""
        if resolution is None or not session_id:
            return self._InvalidClarification(session_id, "")

        async with self._locks.Hold(session_id):
            session = await self._store.GetSession(session_id)
            if session is None or session.get("database_id") != request.database_id:
                return self._InvalidClarification(session_id, "")
            owner_turn = next(
                (
                    turn
                    for turn in session.get("turns") or []
                    if (turn.get("clarification") or {}).get("id")
                    == resolution.clarification_id
                ),
                None,
            )
            if owner_turn is None:
                return self._InvalidClarification(session_id, "")
            pending = owner_turn.get("clarification") or {}
            if pending.get("status") != "pending":
                return self._InvalidClarification(session_id, str(owner_turn.get("id") or ""))

            selected_option = None
            if resolution.option_id:
                selected_option = next(
                    (
                        option
                        for option in pending.get("options") or []
                        if isinstance(option, Mapping)
                        and option.get("id") == resolution.option_id
                    ),
                    None,
                )
                if selected_option is None:
                    return self._InvalidClarification(
                        session_id, str(owner_turn.get("id") or "")
                    )

            resolution_payload = (
                {"option_id": resolution.option_id}
                if resolution.option_id
                else {"text": resolution.text}
            )
            resolved = await self._store.ResolveClarification(
                session_id, resolution.clarification_id, resolution_payload
            )
            if resolved is None:
                return self._InvalidClarification(
                    session_id, str(owner_turn.get("id") or "")
                )

            original_question = str(owner_turn.get("question") or "")
            combined_question = self._CombinedQuestion(
                original_question, selected_option, resolution.text
            )
            return await self._RunInSession(
                request,
                session_id,
                session,
                turn_id=str(owner_turn["id"]),
                graph_question=combined_question,
                stored_question=original_question,
                emit=emit,
            )

    @staticmethod
    def _CombinedQuestion(
        original_question: str,
        option: Mapping[str, Any] | None,
        text: str | None,
    ) -> str:
        if text:
            return f"{original_question}\nUser clarification: {text}"
        option = option or {}
        detail_parts = []
        interpretation = option.get("interpretation")
        if isinstance(interpretation, Mapping):
            for key in (
                "metric", "filters", "aggregation", "grouping", "time_range", "ranking"
            ):
                value = interpretation.get(key)
                if value not in (None, "", []):
                    rendered = ", ".join(value) if isinstance(value, list) else str(value)
                    detail_parts.append(f"{key}: {rendered}")
        detail = "; ".join(detail_parts)
        suffix = f" {detail}" if detail else ""
        return (
            f"{original_question}\nSelected interpretation: "
            f"{option.get('label', option.get('id', ''))}.{suffix}"
        ).rstrip()

    @staticmethod
    def _NormalizeTraceItem(item: Any) -> TraceEvent | None:
        if not isinstance(item, Mapping):
            return None
        step = str(item.get("step") or "")
        message = _OPERATIONAL_TRACE_MESSAGES.get(step)
        if message is None:
            return None
        candidate_status = str(item.get("status") or "success")
        status = (
            candidate_status if candidate_status in _TRACE_STATUSES else "warning"
        )
        return TraceEvent(
            step=step,
            status=status,
            message=message[:_TRACE_MESSAGE_LIMIT],
            sequence=0,
        )

    @classmethod
    def _NormalizeTrace(cls, value: Any) -> list[TraceEvent]:
        curated = []
        for item in value if isinstance(value, list) else []:
            event = cls._NormalizeTraceItem(item)
            if event is not None:
                curated.append(event)
            if len(curated) == _TRACE_EVENT_LIMIT:
                break

        if not curated:
            curated = [
                TraceEvent(
                    step="QueryComplete",
                    status="success",
                    message="查询完成",
                    sequence=0,
                )
            ]

        events = []
        for sequence, event in enumerate(curated, start=1):
            events.append(event.model_copy(update={"sequence": sequence}))
        return events

    @staticmethod
    def _QueryFailed(session_id: str, turn_id: str) -> ErrorResponse:
        return ErrorResponse(
            session_id=session_id,
            turn_id=turn_id,
            code="query_failed",
            message=_QUERY_FAILED_MESSAGE,
            retryable=True,
            suggestions=_QUERY_FAILED_SUGGESTIONS,
            trace=[
                TraceEvent(
                    step="query",
                    status="error",
                    message=_QUERY_FAILED_MESSAGE,
                    sequence=1,
                )
            ],
        )

    @staticmethod
    def _NoTrustworthySql(session_id: str, turn_id: str) -> ErrorResponse:
        message = "未生成可信的 SQL，请换一种问法。"
        return ErrorResponse(
            session_id=session_id,
            turn_id=turn_id,
            code="no_trustworthy_sql",
            message=message,
            retryable=True,
            suggestions=_QUERY_FAILED_SUGGESTIONS,
            trace=[
                TraceEvent(
                    step="query",
                    status="error",
                    message=message,
                    sequence=1,
                )
            ],
        )

    @staticmethod
    def _InvalidClarification(session_id: str, turn_id: str) -> ErrorResponse:
        message = "该澄清请求不存在、已处理或不属于当前会话。"
        return ErrorResponse(
            session_id=session_id,
            turn_id=turn_id,
            code="invalid_clarification",
            message=message,
            retryable=False,
            suggestions=[],
            trace=[
                TraceEvent(
                    step="clarification",
                    status="error",
                    message=message,
                    sequence=1,
                )
            ],
        )

    @staticmethod
    def _Unanswerable(
        session_id: str, turn_id: str, missing_concepts: Any
    ) -> ErrorResponse:
        concepts = [str(item) for item in missing_concepts or []][:5]
        suffix = f" 缺少：{', '.join(concepts)}。" if concepts else ""
        message = f"当前数据库结构无法回答这个问题。{suffix}".strip()
        return ErrorResponse(
            session_id=session_id,
            turn_id=turn_id,
            code="unanswerable_from_schema",
            message=message,
            retryable=False,
            suggestions=["选择其他数据库", "询问当前数据库中已有的字段"],
            trace=[
                TraceEvent(
                    step="RetrieveSchema",
                    status="warning",
                    message="当前数据库缺少所需数据",
                    sequence=1,
                )
            ],
        )

    @staticmethod
    def _TurnForStorage(question: str, response: QueryResponse) -> dict[str, Any]:
        payload = response.model_dump(mode="json")
        turn = {
            "id": response.turn_id,
            "question": question,
            "response_kind": response.kind,
            "answer": payload.get("answer"),
            "sql": payload.get("sql"),
            "result_preview": payload.get("rows", []),
            "chart": payload.get("chart"),
            "confidence": payload.get("confidence"),
            "error": None,
            "trace": payload.get("trace", []),
        }
        if isinstance(response, ErrorResponse):
            turn["error"] = {
                "code": response.code,
                "message": response.message,
                "retryable": response.retryable,
                "suggestions": payload["suggestions"],
            }
        return turn
