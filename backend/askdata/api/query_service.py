"""Application-scoped orchestration for natural-language queries."""

import asyncio
import base64
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any, AsyncIterator, Callable, Mapping

from pydantic import BaseModel

from askdata.agent.graph import AgentGraph
from askdata.api.response_models import (
    AnswerResponse,
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
    """Raised when a continuation is sent before Task 7 support exists."""


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

    async def Run(self, request: QueryRequest) -> QueryResponse:
        if request.clarification is not None or not request.question:
            raise QueryClarificationUnsupported("Only question queries are supported")

        session_id = request.session_id
        if not session_id:
            session_id = await self._store.CreateSession(request.database_id)

        while True:
            async with self._locks.Hold(session_id):
                session = await self._store.GetSession(session_id)
                if session is None or session["database_id"] != request.database_id:
                    replacement_required = True
                else:
                    return await self._RunInSession(request, session_id, session)

            if replacement_required:
                session_id = await self._store.CreateSession(request.database_id)

    async def DeleteSession(self, session_id: str) -> bool:
        async with self._locks.Hold(session_id):
            return await self._store.DeleteSession(session_id)

    async def _RunInSession(
        self, request: QueryRequest, session_id: str, session: Mapping[str, Any]
    ) -> QueryResponse:
        turn_id = str(uuid.uuid4())
        history = session.get("turns") or []
        context: dict[str, Any] = {}
        if history:
            context = {
                "last_question": history[-1].get("question"),
                "last_sql": history[-1].get("sql"),
            }

        try:
            result = await self._graph_factory().ARun(
                question=request.question,
                database_id=request.database_id,
                session_context=context,
            )
            normalized = _JsonSafe(result)
            if not isinstance(normalized, Mapping):
                response = self._QueryFailed(session_id, turn_id)
            elif "error" in normalized and normalized["error"] is not None:
                response = self._QueryFailed(session_id, turn_id)
            else:
                sql = normalized.get("sql")
                if not isinstance(sql, str) or not sql.strip():
                    response = self._NoTrustworthySql(session_id, turn_id)
                else:
                    rows = normalized.get("rows")
                    bounded_rows = rows[:_ROW_PREVIEW_LIMIT] if isinstance(rows, list) else []
                    response = AnswerResponse(
                        session_id=session_id,
                        turn_id=turn_id,
                        answer=str(normalized.get("answer", "")),
                        sql=sql.strip(),
                        columns=normalized.get("columns") or [],
                        rows=bounded_rows,
                        chart=normalized.get("chart"),
                        confidence="medium",
                        trace=self._NormalizeTrace(normalized.get("trace")),
                    )
        except Exception:
            logger.exception("Query execution failed")
            response = self._QueryFailed(session_id, turn_id)

        await self._store.SaveTurn(
            session_id,
            self._TurnForStorage(request.question, response),
        )
        return response

    @staticmethod
    def _NormalizeTrace(value: Any) -> list[TraceEvent]:
        items = value if isinstance(value, list) else [value]
        curated = []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            step = str(item.get("step") or "")
            message = _OPERATIONAL_TRACE_MESSAGES.get(step)
            if message is None:
                continue
            candidate_status = str(item.get("status") or "success")
            status = candidate_status if candidate_status in _TRACE_STATUSES else "warning"
            curated.append((step, status, message[:_TRACE_MESSAGE_LIMIT]))
            if len(curated) == _TRACE_EVENT_LIMIT:
                break

        if not curated:
            curated = [("QueryComplete", "success", "查询完成")]

        events = []
        for sequence, (step, status, message) in enumerate(curated, start=1):
            events.append(
                TraceEvent(
                    step=step,
                    status=status,
                    message=message,
                    sequence=sequence,
                )
            )
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
