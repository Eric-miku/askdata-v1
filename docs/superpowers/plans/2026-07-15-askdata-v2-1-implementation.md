# AskData V2.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved demo-ready SQLite V2.1 experience with persistent conversations, trustworthy SQL selection, clarification, optional hybrid retrieval, streaming operational traces, and deterministic charts while preserving the existing frontend.

**Architecture:** Add explicit pipeline services around the existing ReAct SQL generator. Deterministic code owns contracts, persistence, checks, retries, candidate selection, streaming, and chart policy; LLM calls remain focused on interpretation, SQL generation/repair, targeted semantic review, and final answer synthesis. Each task below is a vertical, independently testable slice.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic 2, sqlite3/aiosqlite, sqlglot, OpenAI-compatible embeddings, optional Milvus, pytest, React 18, TypeScript, Zustand, Ant Design, ECharts, Vitest.

---

## File Map

### Backend files to create

- `backend/askdata/api/response_models.py` — discriminated response, clarification, chart, confidence, and trace models.
- `backend/askdata/api/session_store.py` — transactional SQLite application store.
- `backend/askdata/api/query_service.py` — shared non-streaming/streaming query orchestration and persistence.
- `backend/askdata/agent/intent.py` — intent contract and answerability types.
- `backend/askdata/agent/sql_quality.py` — static checks, result checks, and candidate scoring.
- `backend/askdata/agent/pipeline.py` — V2 staged pipeline and execution budget.
- `backend/askdata/agent/ambiguity.py` — structured material-ambiguity decision service.
- `backend/askdata/tools/embedding_client.py` — configurable OpenAI-compatible embedding client.
- `backend/askdata/tools/vector_store.py` — vector-store protocol, disabled implementation, and Milvus adapter.
- `backend/askdata/tools/hybrid_retriever.py` — rank fusion, schema backbone, join expansion, coverage check, and fallback.
- `backend/askdata/tools/chart_builder.py` — deterministic `ChartSpec` selection.
- `backend/askdata/eval/demo_suite.py` — curated V2 behavior-suite runner and metrics.

### Backend files to modify

- `backend/askdata/api/schemas.py` — question/clarification request union.
- `backend/askdata/api/routes.py` — session list/reopen, discriminated query, and stream routes.
- `backend/askdata/api/app.py` — initialize and close the application session store.
- `backend/askdata/core/config.py` — application database, embedding, Milvus, and retrieval settings.
- `backend/askdata/agent/react_sql_agent.py` — return executed candidates; stop independently selecting prose/SQL.
- `backend/askdata/agent/graph.py` — delegate to the staged pipeline while retaining the one-shot compatibility path.
- `backend/askdata/tools/retriever.py` — expose lexical candidates and schema metadata to the hybrid retriever.
- `backend/askdata/cli.py` — schema-index build and demo-suite commands.
- `pyproject.toml` — `aiosqlite` plus optional Milvus dependency.

### Frontend files to create

- `frontend/src/components/ConversationDrawer.tsx` — persisted-session list and reopen UI patterned on the database drawer.
- `frontend/src/components/ClarificationPrompt.tsx` — inline option and custom-answer flow.
- `frontend/src/components/ChartPanel.tsx` — validated ChartSpec-to-ECharts adapter.
- `frontend/src/api/queryStream.ts` — POST event-stream parser with abort support.

### Frontend files to modify

- `frontend/src/types/query.ts` — discriminated responses, session summaries, stream events, and expanded turn states.
- `frontend/src/api/query.ts` — list/get sessions and resolve clarification.
- `frontend/src/store/queryStore.ts` — persisted sessions, streaming progress, clarification continuation, and cancellation.
- `frontend/src/components/AppSidebar.tsx` — add the history-rail action and drawer host.
- `frontend/src/components/AgentTrace.tsx` — operational labels and live-event behavior.
- `frontend/src/components/QueryResultView.tsx` — render clarification, partial, chart, and error states.
- `frontend/src/pages/QueryResultDemo.tsx` — load/open histories and pass new handlers.
- `frontend/src/styles.css` — only native additions using existing tokens and geometry.

### Tests to create

- `tests/test_response_models.py`
- `tests/test_session_store.py`
- `tests/test_query_service.py`
- `tests/test_intent.py`
- `tests/test_sql_quality.py`
- `tests/test_pipeline.py`
- `tests/test_ambiguity.py`
- `tests/test_embedding_client.py`
- `tests/test_hybrid_retriever.py`
- `tests/test_chart_builder.py`
- `tests/test_query_stream.py`
- `tests/test_demo_suite.py`
- `frontend/src/components/ConversationDrawer.test.tsx`
- `frontend/src/components/ClarificationPrompt.test.tsx`
- `frontend/src/components/ChartPanel.test.tsx`
- `frontend/src/api/queryStream.test.ts`

## Task 1: Freeze V2 API and Trace Contracts

**Files:**
- Create: `backend/askdata/api/response_models.py`
- Modify: `backend/askdata/api/schemas.py`
- Test: `tests/test_response_models.py`

- [ ] **Step 1: Write failing response-contract tests**

```python
from pydantic import TypeAdapter, ValidationError
import pytest

from askdata.api.response_models import QueryResponse
from askdata.api.schemas import QueryRequest


def test_answer_response_uses_discriminator_and_session_identity():
    parsed = TypeAdapter(QueryResponse).validate_python({
        "kind": "answer",
        "session_id": "s1",
        "turn_id": "t1",
        "answer": "Three rows.",
        "sql": "SELECT 3 AS count",
        "columns": ["count"],
        "rows": [{"count": 3}],
        "chart": None,
        "confidence": "high",
        "trace": [],
    })
    assert parsed.kind == "answer"


def test_query_request_accepts_exactly_one_input_kind():
    assert QueryRequest(question="How many?", database_id="demo").question == "How many?"
    assert QueryRequest(
        database_id="demo",
        session_id="s1",
        clarification={"clarification_id": "c1", "option_id": "net"},
    ).clarification.option_id == "net"
    with pytest.raises(ValidationError):
        QueryRequest(question="x", database_id="demo", clarification={
            "clarification_id": "c1", "option_id": "net"
        })
```

- [ ] **Step 2: Run the tests and confirm the missing models fail**

Run: `uv run pytest tests/test_response_models.py -q`
Expected: FAIL because `response_models` and the clarification request do not exist.

- [ ] **Step 3: Implement the minimal contracts**

```python
# backend/askdata/api/response_models.py
from typing import Annotated, Any, Literal
from pydantic import BaseModel, Field

Confidence = Literal["high", "medium", "low"]


class TraceEvent(BaseModel):
    step: str
    status: Literal["started", "success", "retry", "warning", "error"]
    message: str
    sequence: int = 0


class ChartSpec(BaseModel):
    type: Literal["line", "vertical_bar", "horizontal_bar", "pie", "scatter"]
    title: str
    category_field: str | None = None
    value_fields: list[str] = Field(default_factory=list)
    category_label: str | None = None
    value_labels: dict[str, str] = Field(default_factory=dict)
    reason: Literal["time_series", "comparison", "ranking", "proportion", "correlation"]


class ResponseBase(BaseModel):
    session_id: str
    turn_id: str
    trace: list[TraceEvent] = Field(default_factory=list)


class AnswerResponse(ResponseBase):
    kind: Literal["answer"] = "answer"
    answer: str
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    chart: ChartSpec | None = None
    confidence: Confidence


class ClarificationOption(BaseModel):
    id: str
    label: str
    description: str | None = None


class ClarificationResponse(ResponseBase):
    kind: Literal["clarification"] = "clarification"
    clarification_id: str
    question: str
    options: list[ClarificationOption]
    recommended_option_id: str | None = None


class PartialResponse(ResponseBase):
    kind: Literal["partial"] = "partial"
    answer: str
    limitations: list[str]
    suggestions: list[str]
    confidence: Confidence
    sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    chart: ChartSpec | None = None


class ErrorResponse(ResponseBase):
    kind: Literal["error"] = "error"
    code: str
    message: str
    retryable: bool
    suggestions: list[str] = Field(default_factory=list)


QueryResponse = Annotated[
    AnswerResponse | ClarificationResponse | PartialResponse | ErrorResponse,
    Field(discriminator="kind"),
]
```

```python
# backend/askdata/api/schemas.py
from typing import Any
from pydantic import BaseModel, Field, model_validator


class ClarificationResolution(BaseModel):
    clarification_id: str
    option_id: str | None = None
    text: str | None = None

    @model_validator(mode="after")
    def require_one_resolution(self):
        if bool(self.option_id) == bool(self.text):
            raise ValueError("Provide exactly one of option_id or text")
        return self


class QueryRequest(BaseModel):
    database_id: str
    session_id: str | None = None
    question: str | None = None
    clarification: ClarificationResolution | None = None

    @model_validator(mode="after")
    def require_one_input(self):
        if bool(self.question and self.question.strip()) == bool(self.clarification):
            raise ValueError("Provide exactly one of question or clarification")
        if self.question:
            self.question = self.question.strip()
        return self
```

- [ ] **Step 4: Run focused and compatibility tests**

Run: `uv run pytest tests/test_response_models.py tests/test_query_route.py -q`
Expected: new tests PASS; `test_query_route.py` may fail only where it still constructs the legacy response and will be updated in Task 4.

- [ ] **Step 5: Commit the contracts**

```bash
git add backend/askdata/api/response_models.py backend/askdata/api/schemas.py tests/test_response_models.py
git commit -m "feat(api): define V2 query contracts"
```

## Task 2: Add the Transactional SQLite Session Store

**Files:**
- Create: `backend/askdata/api/session_store.py`
- Modify: `backend/askdata/core/config.py`
- Modify: `backend/askdata/api/app.py`
- Modify: `pyproject.toml`
- Test: `tests/test_session_store.py`

- [ ] **Step 1: Add failing persistence and restart tests**

```python
import pytest
from askdata.api.session_store import SessionStore


@pytest.mark.asyncio
async def test_sessions_and_turns_survive_store_restart(tmp_path):
    path = tmp_path / "askdata-app.sqlite"
    first = SessionStore(path)
    await first.Initialize()
    session_id = await first.CreateSession("demo")
    await first.SaveTurn(session_id, {
        "id": "t1", "question": "How many?", "response_kind": "answer",
        "answer": "3", "sql": "SELECT 3", "result_preview": [{"count": 3}],
        "chart": None, "confidence": "high", "error": None, "trace": [],
    })
    await first.Close()

    second = SessionStore(path)
    await second.Initialize()
    restored = await second.GetSession(session_id)
    assert restored["turns"][0]["answer"] == "3"
    await second.Close()


@pytest.mark.asyncio
async def test_delete_session_cascades_turns(tmp_path):
    store = SessionStore(tmp_path / "app.sqlite")
    await store.Initialize()
    session_id = await store.CreateSession("demo")
    await store.DeleteSession(session_id)
    assert await store.GetSession(session_id) is None
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/test_session_store.py -q`
Expected: FAIL because `SessionStore` does not exist.

- [ ] **Step 3: Add the dependency and configuration**

```toml
# pyproject.toml
dependencies = [
    "fastapi>=0.111.0",
    "uvicorn[standard]>=0.30.1",
    "pydantic>=2.7.4",
    "pydantic-settings>=2.3.4",
    "sqlalchemy>=2.0.31",
    "sqlglot>=25.5.0",
    "langgraph>=0.1.3",
    "openai>=1.34.0",
    "typer>=0.12.0",
    "tqdm>=4.66.0",
    "aiosqlite>=0.20.0",
]

[project.optional-dependencies]
vector = ["pymilvus>=2.4.0"]

[dependency-groups]
dev = ["pytest>=8.2.0", "pytest-asyncio>=0.23.0"]
```

```python
# backend/askdata/core/config.py additions
APP_DATABASE_PATH: str = "data/askdata-app.sqlite"
EMBEDDING_API_URL: str = ""
EMBEDDING_API_KEY: str = ""
EMBEDDING_MODEL: str = "BAAI/bge-m3"
EMBEDDING_DIMENSION: int = 1024
MILVUS_URI: str = ""
MILVUS_COLLECTION: str = "askdata_schema_chunks"
VECTOR_RETRIEVAL_ENABLED: bool = True
```

- [ ] **Step 4: Implement `SessionStore` with one connection and serialized writes**

Implement `Initialize`, `Close`, `CreateSession`, `ListSessions`, `GetSession`, `DeleteSession`, `SaveTurn`, `CreateClarification`, and `ResolveClarification`. Use `aiosqlite.connect`, `PRAGMA foreign_keys=ON`, `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`, explicit commits, ISO UTC timestamps, JSON serialization, and an `asyncio.Lock` around writes. Use the exact table definitions from the approved design.

The public interface is `SessionStore(path)`, `Initialize()`, `Close()`, `CreateSession(database_id, title="")`, `ListSessions(limit=50)`, `GetSession(session_id)`, `DeleteSession(session_id)`, `SaveTurn(session_id, turn)`, `CreateClarification(turn_id, prompt, options)`, and `ResolveClarification(session_id, clarification_id, resolution)`. Return JSON-ready dictionaries at the API boundary and keep SQL row conversion private.

- [ ] **Step 5: Wire store lifetime into FastAPI**

```python
# backend/askdata/api/app.py
@asynccontextmanager
async def lifespan(application: FastAPI):
    store = SessionStore(project_path(settings.APP_DATABASE_PATH))
    await store.Initialize()
    application.state.session_store = store
    yield
    await store.Close()
```

- [ ] **Step 6: Verify persistence tests**

Run: `uv sync && uv run pytest tests/test_session_store.py -q`
Expected: PASS.

- [ ] **Step 7: Commit persistence**

```bash
git add pyproject.toml uv.lock backend/askdata/core/config.py backend/askdata/api/app.py backend/askdata/api/session_store.py tests/test_session_store.py
git commit -m "feat(api): persist conversations in SQLite"
```

## Task 3: Expose Session List, Reopen, and Delete APIs

**Files:**
- Modify: `backend/askdata/api/routes.py`
- Test: `tests/test_query_route.py`

- [ ] **Step 1: Replace global-manager assumptions with failing app-store route tests**

```python
def test_session_routes_list_and_reopen(client):
    created = client.post("/api/sessions", params={"database_id": "demo"}).json()
    listed = client.get("/api/sessions").json()
    assert listed[0]["session_id"] == created["session_id"]
    reopened = client.get(f"/api/sessions/{created['session_id']}").json()
    assert reopened["database_id"] == "demo"


def test_unknown_session_returns_404(client):
    response = client.get("/api/sessions/missing")
    assert response.status_code == 404
```

The test fixture must create a temporary `SessionStore`, assign it to `app.state.session_store`, and close it after the client.

- [ ] **Step 2: Run the new route tests**

Run: `uv run pytest tests/test_query_route.py -q`
Expected: FAIL because list/reopen routes and app-state access do not exist.

- [ ] **Step 3: Implement request-scoped store access and routes**

```python
def _Store(request: Request) -> SessionStore:
    return request.app.state.session_store


@router.get("/sessions")
async def list_sessions(request: Request, limit: int = Query(50, ge=1, le=100)):
    return await _Store(request).ListSessions(limit)


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    session = await _Store(request).GetSession(session_id)
    if session is None:
        raise HTTPException(404, f"Session '{session_id}' not found")
    return session
```

Update create/delete routes to use `_Store(request)` and remove the `session_manager` global import.

- [ ] **Step 4: Run route and store tests**

Run: `uv run pytest tests/test_session_store.py tests/test_query_route.py -q`
Expected: PASS.

- [ ] **Step 5: Commit session APIs**

```bash
git add backend/askdata/api/routes.py tests/test_query_route.py
git commit -m "feat(api): list and reopen persisted sessions"
```

## Task 4: Add the Shared Query Service and Preserve Legacy Queries

**Files:**
- Create: `backend/askdata/api/query_service.py`
- Modify: `backend/askdata/api/routes.py`
- Test: `tests/test_query_service.py`
- Test: `tests/test_query_route.py`

- [ ] **Step 1: Write failing answer and error persistence tests**

```python
@pytest.mark.asyncio
async def test_query_service_adds_identity_and_persists_final_answer(tmp_path):
    store = await make_store(tmp_path)
    graph = FakeGraph(result={
        "answer": "3", "sql": "SELECT 3 AS count", "columns": ["count"],
        "rows": [{"count": 3}], "chart": None, "trace": [], "error": None,
    })
    service = QueryService(store=store, graph_factory=lambda: graph)
    response = await service.Run(QueryRequest(question="How many?", database_id="demo"))
    assert response.kind == "answer"
    assert response.session_id
    restored = await store.GetSession(response.session_id)
    assert restored["turns"][0]["sql"] == "SELECT 3 AS count"


@pytest.mark.asyncio
async def test_query_service_returns_structured_error_without_fake_answer(tmp_path):
    service = QueryService(store=await make_store(tmp_path), graph_factory=FailingGraph)
    response = await service.Run(QueryRequest(question="x", database_id="demo"))
    assert response.kind == "error"
    assert response.code == "query_failed"
    assert not hasattr(response, "answer")
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/test_query_service.py -q`
Expected: FAIL because `QueryService` is missing.

- [ ] **Step 3: Implement one service for both HTTP paths**

```python
class QueryService:
    def __init__(self, store: SessionStore, graph_factory=AgentGraph):
        self.store = store
        self.graph_factory = graph_factory

    async def Run(self, request: QueryRequest, emit=None) -> QueryResponse:
        session_id = await self._ResolveSession(request)
        turn_id = str(uuid.uuid4())
        try:
            result = await self.graph_factory().ARun(
                question=request.question or "",
                database_id=request.database_id,
                session_context=await self._Context(session_id),
                emit=emit,
            )
            response = self._MapResult(session_id, turn_id, result)
        except Exception:
            response = ErrorResponse(
                session_id=session_id, turn_id=turn_id, code="query_failed",
                message="这次查询没有完成。", retryable=True,
                suggestions=["重试", "换一种问法"], trace=[],
            )
        await self._Persist(request, response)
        return response
```

Keep exception details in server logs/trace diagnostics, not the user-facing message. `_MapResult` converts legacy graph dictionaries to `AnswerResponse` until the staged pipeline lands.

- [ ] **Step 4: Make `POST /api/query` delegate to `QueryService`**

```python
@router.post("/query", response_model=QueryResponse)
async def execute_query(request: QueryRequest, http_request: Request):
    return await QueryService(_Store(http_request)).Run(request)
```

- [ ] **Step 5: Run focused and full backend tests**

Run: `uv run pytest tests/test_query_service.py tests/test_query_route.py -q`
Expected: PASS.

Run: `uv run pytest -q`
Expected: all existing and new backend tests PASS.

- [ ] **Step 6: Commit query-service compatibility**

```bash
git add backend/askdata/api/query_service.py backend/askdata/api/routes.py tests/test_query_service.py tests/test_query_route.py
git commit -m "refactor(api): centralize query response persistence"
```

## Task 5: Add Intent Contracts, SQL Checks, and Candidate Ledger

**Files:**
- Create: `backend/askdata/agent/intent.py`
- Create: `backend/askdata/agent/sql_quality.py`
- Modify: `backend/askdata/agent/answer_shape.py`
- Test: `tests/test_intent.py`
- Test: `tests/test_sql_quality.py`

- [ ] **Step 1: Write failing deterministic quality tests**

```python
from askdata.agent.intent import IntentContract
from askdata.agent.sql_quality import CandidateLedger, EvaluateStaticSql, EvaluateResult


def test_count_contract_rejects_listing_sql():
    intent = IntentContract(shape="scalar", metrics=["count"], expected_max_rows=1)
    report = EvaluateStaticSql(intent, "SELECT name FROM items", {"items": {"name", "id"}})
    assert "missing_count_aggregation" in report.failures


def test_ranking_contract_requires_order_and_limit():
    intent = IntentContract(shape="ranking", order="descending", expected_max_rows=5)
    report = EvaluateStaticSql(intent, "SELECT name, score FROM schools", {
        "schools": {"name", "score"}
    })
    assert {"missing_order", "missing_limit"} <= set(report.failures)


def test_candidate_ledger_prefers_complete_older_candidate_to_recent_inspection():
    ledger = CandidateLedger()
    ledger.Add(executed_candidate("SELECT COUNT(*) AS count FROM items", coverage=1.0))
    ledger.Add(executed_candidate("SELECT id FROM items", coverage=0.4))
    assert ledger.SelectBest().sql.startswith("SELECT COUNT")
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `uv run pytest tests/test_intent.py tests/test_sql_quality.py -q`
Expected: FAIL because the new types are missing.

- [ ] **Step 3: Implement the intent and report types**

```python
class IntentContract(BaseModel):
    shape: Literal["scalar", "listing", "ranking", "ratio", "grouped", "comparison"]
    entities: list[str] = Field(default_factory=list)
    output_attributes: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    grouping: list[str] = Field(default_factory=list)
    order: Literal["ascending", "descending"] | None = None
    expected_max_rows: int | None = None
    time_condition: str | None = None
```

```python
class QualityReport(BaseModel):
    passed: bool
    failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    coverage: float = 0.0


class SqlCandidate(BaseModel):
    sql: str
    columns: list[str] = Field(default_factory=list)
    rows: list[dict] = Field(default_factory=list)
    static_report: QualityReport
    result_report: QualityReport | None = None
    execution_error: str | None = None
    sequence: int


class CandidateLedger:
    def __init__(self):
        self._candidates: list[SqlCandidate] = []

    def Add(self, candidate: SqlCandidate) -> None:
        self._candidates.append(candidate)

    def SelectBest(self) -> SqlCandidate | None:
        eligible = [item for item in self._candidates if item.execution_error is None]
        if not eligible:
            return None
        return max(eligible, key=lambda item: (
            item.result_report.coverage if item.result_report else 0.0,
            not item.static_report.failures,
            not (item.result_report and item.result_report.failures),
            -len(item.static_report.warnings),
            -item.sequence,
        ))
```

Use sqlglot AST inspection for aggregation, projection, tables, columns, grouping, ordering, limits, and join connectivity. Reuse `CheckAnswerShape` messages by mapping them to stable failure codes.

- [ ] **Step 4: Implement result checks and explicit ordering**

`EvaluateResult` must distinguish a legitimate empty result from wrong shape, detect null-only output, compare row count to `expected_max_rows`, and calculate covered intent elements. `CandidateLedger.SelectBest` must sort by successful execution, coverage, zero failures, fewer warnings, directness, and only then sequence.

- [ ] **Step 5: Run deterministic quality tests**

Run: `uv run pytest tests/test_answer_shape.py tests/test_intent.py tests/test_sql_quality.py -q`
Expected: PASS.

- [ ] **Step 6: Commit quality primitives**

```bash
git add backend/askdata/agent/intent.py backend/askdata/agent/sql_quality.py backend/askdata/agent/answer_shape.py tests/test_intent.py tests/test_sql_quality.py
git commit -m "feat(agent): add intent and SQL quality gates"
```

## Task 6: Refactor ReAct Into the Staged Pipeline

**Files:**
- Create: `backend/askdata/agent/pipeline.py`
- Modify: `backend/askdata/agent/react_sql_agent.py`
- Modify: `backend/askdata/agent/graph.py`
- Test: `tests/test_pipeline.py`
- Modify: `tests/test_react_sql_agent.py`
- Modify: `tests/test_agent_graph.py`

- [ ] **Step 1: Write failing final-candidate consistency and budget tests**

```python
def test_pipeline_synthesizes_answer_after_selecting_final_candidate():
    react = FakeReact(candidates=[count_candidate(), inspection_candidate()])
    analyzer = RecordingAnalyzer()
    result = StagedSqlPipeline(react=react, analyzer=analyzer).Run(context())
    assert result["sql"] == count_candidate().sql
    assert analyzer.sql_seen == result["sql"]
    assert analyzer.rows_seen == result["rows"]


def test_pipeline_never_executes_more_than_six_candidates():
    runner = AlwaysFailingRunner()
    result = StagedSqlPipeline(react=InfiniteReact(), runner=runner).Run(context())
    assert runner.call_count == 6
    assert result["kind"] == "error"


def test_pipeline_stops_repeated_identical_sql_early():
    runner = AlwaysFailingRunner()
    StagedSqlPipeline(react=RepeatingReact("SELECT missing FROM items"), runner=runner).Run(context())
    assert runner.call_count == 1
```

- [ ] **Step 2: Verify tests fail**

Run: `uv run pytest tests/test_pipeline.py -q`
Expected: FAIL because `StagedSqlPipeline` is missing.

- [ ] **Step 3: Change ReAct to yield candidates instead of a competing final selection**

Add a `GenerateCandidates(question, schema_prompt, session_context) -> list[SqlCandidateDraft]` path that retains tool-call messages and execution repair behavior but delegates execution accounting and selection to the pipeline. Keep `Run` temporarily as a compatibility wrapper used by old tests.

```python
class SqlCandidateDraft(BaseModel):
    sql: str
    reason: str = ""
    referenced_context: list[str] = Field(default_factory=list)


def GenerateCandidates(self, question, schema_prompt, session_context=None):
    messages = self._BuildMessages(question, schema_prompt, session_context)
    return self._CollectSqlDrafts(messages)
```

- [ ] **Step 4: Implement the six-execution pipeline**

`StagedSqlPipeline.Run` must classify each failure as `syntax_or_safety`, `schema_grounding`, `answer_shape`, `empty_or_suspicious`, or `repeated_no_progress`; emit operational events; execute through `query_runner`; add every result to the ledger; trigger retrieval expansion only for grounding failure; stop on verified success; select one final candidate; and call `ResultAnalyzer.Analyze` only after selection.

Use the budget sequence from the approved design: initial, two targeted repairs, retrieval expansion, alternate plan, final candidate.

- [ ] **Step 5: Delegate `AgentGraph` to the staged pipeline**

```python
def Run(self, question, database_id, session_context=None, emit=None):
    context = self._Retriever().index.Retrieve(database_id, question)
    return self._Pipeline().Run(
        question=question,
        retrieval=context,
        session_context=session_context,
        emit=emit,
    )
```

Retain the one-shot path only when the supplied LLM client has no `Chat` method.

- [ ] **Step 6: Run agent tests**

Run: `uv run pytest tests/test_pipeline.py tests/test_react_sql_agent.py tests/test_agent_graph.py -q`
Expected: PASS, including explicit answer/SQL/result consistency.

- [ ] **Step 7: Commit the staged pipeline**

```bash
git add backend/askdata/agent/pipeline.py backend/askdata/agent/react_sql_agent.py backend/askdata/agent/graph.py tests/test_pipeline.py tests/test_react_sql_agent.py tests/test_agent_graph.py
git commit -m "refactor(agent): stage SQL generation and selection"
```

## Task 7: Add Material Ambiguity and Clarification Continuation

**Files:**
- Create: `backend/askdata/agent/ambiguity.py`
- Modify: `backend/askdata/agent/pipeline.py`
- Modify: `backend/askdata/api/query_service.py`
- Test: `tests/test_ambiguity.py`
- Modify: `tests/test_query_service.py`

- [ ] **Step 1: Write failing materiality tests**

```python
def test_two_revenue_metrics_require_clarification():
    result = AmbiguityGate(FakeInterpreter([
        interpretation("gross", metric="gross_revenue"),
        interpretation("net", metric="net_revenue"),
    ])).Check("show revenue", schema_with_both_revenues())
    assert result.state == "materially_ambiguous"
    assert [option.id for option in result.options] == ["gross", "net"]


def test_one_schema_supported_interpretation_proceeds():
    result = AmbiguityGate(FakeInterpreter([
        interpretation("enrollment", metric="Enrollment")
    ])).Check("top schools", school_schema())
    assert result.state == "clear"


def test_missing_student_entity_is_unanswerable_not_ambiguous():
    result = AmbiguityGate(FakeInterpreter([])).Check("list student names", school_only_schema())
    assert result.state == "unanswerable"
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/test_ambiguity.py -q`
Expected: FAIL because `AmbiguityGate` is missing.

- [ ] **Step 3: Implement structured interpretation and materiality comparison**

```python
class Interpretation(BaseModel):
    id: str
    label: str
    entities: list[str]
    metric: str | None = None
    filters: list[str] = Field(default_factory=list)
    grouping: list[str] = Field(default_factory=list)
    time_range: str | None = None
    supported_by: list[str] = Field(default_factory=list)


class AmbiguityDecision(BaseModel):
    state: Literal["clear", "resolvable_from_context", "materially_ambiguous", "unanswerable"]
    resolved_question: str | None = None
    options: list[ClarificationOption] = Field(default_factory=list)
    missing_concepts: list[str] = Field(default_factory=list)
```

The LLM must return interpretations through a tool schema. Code verifies each referenced entity/metric against retrieved schema/evidence and asks only when two supported interpretations differ in entity, metric, filter, aggregation, grouping, time, or ranking.

- [ ] **Step 4: Persist and resume clarification in `QueryService`**

For a clarification response, save the original turn and a pending clarification. For continuation, call `ResolveClarification`, reject missing/resolved IDs, combine the stored original question with the selected interpretation, continue the same `turn_id`, and update that turn rather than creating a second analytical turn.

- [ ] **Step 5: Run ambiguity and service tests**

Run: `uv run pytest tests/test_ambiguity.py tests/test_query_service.py -q`
Expected: PASS.

- [ ] **Step 6: Commit clarification behavior**

```bash
git add backend/askdata/agent/ambiguity.py backend/askdata/agent/pipeline.py backend/askdata/api/query_service.py tests/test_ambiguity.py tests/test_query_service.py
git commit -m "feat(agent): clarify materially ambiguous questions"
```

## Task 8: Add Configurable Embeddings and Hybrid Retrieval

**Files:**
- Create: `backend/askdata/tools/embedding_client.py`
- Create: `backend/askdata/tools/vector_store.py`
- Create: `backend/askdata/tools/hybrid_retriever.py`
- Modify: `backend/askdata/tools/retriever.py`
- Modify: `backend/askdata/cli.py`
- Test: `tests/test_embedding_client.py`
- Test: `tests/test_hybrid_retriever.py`
- Modify: `tests/test_retriever.py`

- [ ] **Step 1: Write failing embedding validation and fallback tests**

```python
def test_embedding_client_rejects_wrong_dimension():
    client = EmbeddingClient(api=FakeEmbeddingApi([[0.1, 0.2]]), model="BAAI/bge-m3", dimension=1024)
    with pytest.raises(EmbeddingConfigurationError, match="expected 1024"):
        client.Embed(["schema"])


def test_hybrid_retriever_falls_back_to_lexical_when_vector_service_fails():
    retriever = HybridRetriever(lexical=FakeLexical([school_chunk()]), vector=FailingVectorStore())
    result = retriever.Retrieve("demo", "list schools")
    assert result.chunks[0].table_name == "schools"
    assert any(event.status == "warning" for event in result.trace)


def test_value_chunk_bridges_business_term_to_code():
    vector = FakeVectorStore([value_chunk("EdOpsCode", "SSS = State Special School")])
    result = HybridRetriever(lexical=FakeLexical([]), vector=vector).Retrieve(
        "california_schools", "State Special Schools"
    )
    assert "SSS = State Special School" in result.prompt
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/test_embedding_client.py tests/test_hybrid_retriever.py -q`
Expected: FAIL because hybrid retrieval modules are missing.

- [ ] **Step 3: Implement the embedding client and store protocol**

```python
class EmbeddingClient:
    def __init__(self, base_url, api_key, model, dimension, client=None):
        self.client = client or OpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.dimension = dimension

    def Embed(self, texts: list[str]) -> list[list[float]]:
        response = self.client.embeddings.create(model=self.model, input=texts)
        vectors = [item.embedding for item in response.data]
        if any(len(vector) != self.dimension for vector in vectors):
            raise EmbeddingConfigurationError(
                f"Embedding dimension mismatch: expected {self.dimension}"
            )
        return vectors
```

```python
class VectorStore(Protocol):
    def Search(self, database_id: str, vectors: list[list[float]], top_k: int) -> list[RankedChunk]:
        raise NotImplementedError

    def Upsert(self, chunks: list[SchemaChunk], vectors: list[list[float]]) -> None:
        raise NotImplementedError


class DisabledVectorStore:
    def Search(self, database_id, vectors, top_k):
        return []
```

`MilvusVectorStore` imports `pymilvus` lazily and filters every search by `database_id`.

- [ ] **Step 4: Expose lexical candidates and canonical schema chunks**

Modify `BirdSchemaIndex` so it can return ranked lexical tables/columns plus complete schema metadata. Add chunk builders for schema, value-semantic, evidence, and example sources. Always construct a schema backbone of all table/column names and keys.

- [ ] **Step 5: Implement reciprocal-rank fusion and coverage recovery**

```python
def ReciprocalRankFusion(rankings: list[list[RankedChunk]], k: int = 60) -> list[RankedChunk]:
    scores: dict[str, float] = {}
    by_id: dict[str, RankedChunk] = {}
    for ranking in rankings:
        for rank, chunk in enumerate(ranking, start=1):
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (k + rank)
            by_id[chunk.id] = chunk
    return sorted(by_id.values(), key=lambda item: scores[item.id], reverse=True)
```

Search original and context-resolved questions separately, fuse lexical/dense/value/evidence rankings, add join neighbors, check entity/metric/filter/group/time coverage, and allow one terminology-expansion pass when coverage is incomplete.

- [ ] **Step 6: Add an explicit index-build CLI**

```text
uv run askdata index-schema --database-id california_schools
```

The command prints chunk counts by source type, configured model, returned dimension, collection name, and source version. It must fail without modifying the collection when validation fails.

- [ ] **Step 7: Run retrieval tests**

Run: `uv run pytest tests/test_embedding_client.py tests/test_hybrid_retriever.py tests/test_retriever.py -q`
Expected: PASS.

- [ ] **Step 8: Commit hybrid retrieval**

```bash
git add backend/askdata/tools/embedding_client.py backend/askdata/tools/vector_store.py backend/askdata/tools/hybrid_retriever.py backend/askdata/tools/retriever.py backend/askdata/cli.py tests/test_embedding_client.py tests/test_hybrid_retriever.py tests/test_retriever.py
git commit -m "feat(retrieval): add optional hybrid schema search"
```

## Task 9: Add Deterministic Chart Specifications

**Files:**
- Create: `backend/askdata/tools/chart_builder.py`
- Modify: `backend/askdata/agent/pipeline.py`
- Test: `tests/test_chart_builder.py`

- [ ] **Step 1: Write failing chart-policy tests**

```python
def test_ranking_builds_horizontal_bar():
    spec = ChartBuilder().Build(
        question="top five schools by enrollment",
        intent=IntentContract(shape="ranking", expected_max_rows=5),
        columns=["School", "Enrollment"],
        rows=[{"School": "A", "Enrollment": 10}],
    )
    assert spec.type == "horizontal_bar"
    assert spec.category_field == "School"
    assert spec.value_fields == ["Enrollment"]


def test_share_with_too_many_categories_stays_table_only():
    rows = [{"category": str(i), "share": i} for i in range(7)]
    assert ChartBuilder().Build("share by category", ratio_intent(), ["category", "share"], rows) is None


def test_chart_never_contains_raw_echarts_options():
    spec = ChartBuilder().Build(
        question="top schools",
        intent=IntentContract(shape="ranking", expected_max_rows=5),
        columns=["School", "Enrollment"],
        rows=[{"School": "A", "Enrollment": 10}],
    )
    assert "formatter" not in spec.model_dump()
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/test_chart_builder.py -q`
Expected: FAIL because `ChartBuilder` is missing.

- [ ] **Step 3: Implement the approved deterministic policy**

Implement field-type inference from returned values and the exact priority: time series, ranking, explicit proportion with at most six non-negative categories, correlation with at least five rows, category comparison, otherwise `None`. Validate referenced fields exist in `columns` and use the approved `ChartSpec` model.

- [ ] **Step 4: Attach charts only after final candidate selection**

```python
selected = ledger.SelectBest()
answer = analyzer.Analyze(question, selected.sql, selected.columns, selected.rows)
chart = chart_builder.Build(question, intent, selected.columns, selected.rows)
```

- [ ] **Step 5: Run chart and pipeline tests**

Run: `uv run pytest tests/test_chart_builder.py tests/test_pipeline.py -q`
Expected: PASS.

- [ ] **Step 6: Commit deterministic charts**

```bash
git add backend/askdata/tools/chart_builder.py backend/askdata/agent/pipeline.py tests/test_chart_builder.py tests/test_pipeline.py
git commit -m "feat(charts): build validated chart specifications"
```

## Task 10: Stream Operational Trace Events

**Files:**
- Modify: `backend/askdata/api/query_service.py`
- Modify: `backend/askdata/api/routes.py`
- Test: `tests/test_query_stream.py`

- [ ] **Step 1: Write a failing stream-order test**

```python
def test_query_stream_emits_ordered_trace_then_final(client):
    with client.stream("POST", "/api/query/stream", json={
        "question": "How many?", "database_id": "demo"
    }) as response:
        body = "".join(response.iter_text())
    assert response.headers["content-type"].startswith("text/event-stream")
    assert body.index("event: trace") < body.index("event: final")
    assert '"sequence":1' in body
    assert '"kind":"answer"' in body
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/test_query_stream.py -q`
Expected: FAIL with 404.

- [ ] **Step 3: Add a bounded event queue to `QueryService`**

```python
async def Stream(self, request: QueryRequest):
    queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue(maxsize=100)

    async def emit(event: TraceEvent):
        await queue.put(("trace", event.model_dump()))

    task = asyncio.create_task(self.Run(request, emit=emit))
    try:
        while True:
            if task.done() and queue.empty():
                response = await task
                yield EncodeSse("final", response.model_dump(mode="json"))
                break
            event = await queue.get()
            if event:
                yield EncodeSse(event[0], event[1])
    finally:
        if not task.done():
            task.cancel()
```

`EncodeSse` must compactly JSON-encode one event, reject embedded newlines in the event name, and terminate every frame with two newlines.

- [ ] **Step 4: Add the streaming route**

```python
@router.post("/query/stream")
async def stream_query(request: QueryRequest, http_request: Request):
    service = QueryService(_Store(http_request))
    return StreamingResponse(
        service.Stream(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 5: Run stream parity tests**

Run: `uv run pytest tests/test_query_stream.py tests/test_query_service.py tests/test_query_route.py -q`
Expected: PASS; the final streamed response equals the non-streaming response except generated IDs.

- [ ] **Step 6: Commit streaming**

```bash
git add backend/askdata/api/query_service.py backend/askdata/api/routes.py tests/test_query_stream.py
git commit -m "feat(api): stream operational query events"
```

## Task 11: Add Frontend V2 Types and Stream Client

**Files:**
- Modify: `frontend/src/types/query.ts`
- Create: `frontend/src/api/queryStream.ts`
- Modify: `frontend/src/api/query.ts`
- Create: `frontend/src/api/queryStream.test.ts`
- Modify: `frontend/src/api/query.test.ts`

- [ ] **Step 1: Write failing stream parser tests**

```typescript
it("parses split SSE frames and returns the final response", async () => {
  const chunks = [
    'event: trace\ndata: {"step":"RetrieveSchema","status":"success",',
    '"message":"ok","sequence":1}\n\nevent: final\ndata: {"kind":"answer",',
    '"session_id":"s1","turn_id":"t1","answer":"3","sql":"SELECT 3",',
    '"columns":[],"rows":[],"chart":null,"confidence":"high","trace":[]}\n\n',
  ];
  const events: QueryStreamEvent[] = [];
  const result = await queryStream(request, events.push, fakeFetch(chunks));
  expect(events[0].type).toBe("trace");
  expect(result.kind).toBe("answer");
});

it("aborts the fetch when its signal is cancelled", async () => {
  const controller = new AbortController();
  controller.abort();
  await expect(queryStream(request, vi.fn(), fetch, controller.signal)).rejects.toMatchObject({
    name: "AbortError",
  });
});
```

- [ ] **Step 2: Verify failure**

Run: `cd frontend && npm test -- --run src/api/queryStream.test.ts`
Expected: FAIL because the stream client is missing.

- [ ] **Step 3: Define discriminated frontend types**

```typescript
export type Confidence = "high" | "medium" | "low";
export type ResponseKind = "answer" | "clarification" | "partial" | "error";
export type ChatTurnStatus =
  | "loading"
  | "awaiting_clarification"
  | "success"
  | "partial"
  | "error";

export interface TraceEvent {
  step: string;
  status: "started" | "success" | "retry" | "warning" | "error";
  message: string;
  sequence: number;
}

export type QueryResponse =
  | AnswerResponse
  | ClarificationResponse
  | PartialResponse
  | ErrorResponse;
```

Define interfaces matching every backend field exactly, including `ChartSpec`, clarification options, session summaries, and restored turns.

- [ ] **Step 4: Implement the incremental SSE parser**

Use `fetch`, `response.body.getReader()`, `TextDecoder`, a persistent buffer, `\n\n` frame boundaries, and separate `event:`/`data:` parsing. Call `onEvent` for trace and clarification frames; return the parsed `final`; throw a stable error for a stream that closes without `final`.

- [ ] **Step 5: Add session API calls**

```typescript
export const listSessions = () => client.get<SessionSummary[]>("/sessions").then(r => r.data);
export const getSession = (id: string) =>
  client.get<RestoredSession>(`/sessions/${encodeURIComponent(id)}`).then(r => r.data);
```

- [ ] **Step 6: Run API tests**

Run: `cd frontend && npm test -- --run src/api/queryStream.test.ts src/api/query.test.ts`
Expected: PASS.

- [ ] **Step 7: Commit frontend contracts**

```bash
git add frontend/src/types/query.ts frontend/src/api/queryStream.ts frontend/src/api/queryStream.test.ts frontend/src/api/query.ts frontend/src/api/query.test.ts
git commit -m "feat(frontend): add V2 query and stream contracts"
```

## Task 12: Add Persistent History Without Replacing the Existing Rail

**Files:**
- Create: `frontend/src/components/ConversationDrawer.tsx`
- Create: `frontend/src/components/ConversationDrawer.test.tsx`
- Modify: `frontend/src/components/AppSidebar.tsx`
- Modify: `frontend/src/components/Icons.tsx`
- Modify: `frontend/src/store/queryStore.ts`
- Modify: `frontend/src/store/queryStore.test.ts`
- Modify: `frontend/src/pages/QueryResultDemo.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Write failing history drawer and store tests**

```typescript
it("opens a persisted session and restores its turns", async () => {
  const api = createApi({
    listSessions: vi.fn().mockResolvedValue([{ session_id: "s1", title: "Schools", database_id: "demo" }]),
    getSession: vi.fn().mockResolvedValue(restoredSession),
  });
  const store = createQueryStore(api);
  await store.getState().loadSessions();
  await store.getState().openSession("s1");
  expect(store.getState().sessionId).toBe("s1");
  expect(store.getState().turns[0].question).toBe("How many?");
});

it("restores focus after Escape closes the conversation drawer", async () => {
  // render, open with the history rail button, press Escape, assert trigger focus
});
```

- [ ] **Step 2: Verify failure**

Run: `cd frontend && npm test -- --run src/store/queryStore.test.ts src/components/ConversationDrawer.test.tsx`
Expected: FAIL because history state and component do not exist.

- [ ] **Step 3: Extend the store without deleting history on database change**

Add `sessions`, `sessionsLoading`, `sessionsError`, `loadSessions`, and `openSession`. Change `selectDatabase` and `newChat` to clear the active local conversation without calling `deleteSession`; only an explicit delete action calls the API.

- [ ] **Step 4: Implement `ConversationDrawer` by following the existing drawer contract**

Reuse the same focus-trap algorithm, Escape restoration, scrim, search interaction, width, and token classes as the database drawer. Do not import Ant Design Drawer and do not add a permanent sidebar.

- [ ] **Step 5: Add one history icon to the existing 56px rail**

`AppSidebar` owns both drawer triggers but ensures only one drawer is open. `QueryResultDemo` passes sessions and `onOpenSession` from the store.

- [ ] **Step 6: Add only token-based CSS**

Use `var(--bg-subtle)`, `var(--surface-raised)`, `var(--text*)`, `var(--border*)`, `var(--accent)`, 308px desktop geometry, and the existing 720px mobile breakpoint. Do not change existing typography or layout declarations.

- [ ] **Step 7: Run history and existing sidebar tests**

Run: `cd frontend && npm test -- --run src/components/ConversationDrawer.test.tsx src/components/AppSidebar.test.tsx src/store/queryStore.test.ts src/pages/QueryResultDemo.test.tsx`
Expected: PASS.

- [ ] **Step 8: Commit history integration**

```bash
git add frontend/src/components/ConversationDrawer.tsx frontend/src/components/ConversationDrawer.test.tsx frontend/src/components/AppSidebar.tsx frontend/src/components/Icons.tsx frontend/src/store/queryStore.ts frontend/src/store/queryStore.test.ts frontend/src/pages/QueryResultDemo.tsx frontend/src/styles.css
git commit -m "feat(frontend): reopen persisted conversations"
```

## Task 13: Render Clarification, Live Operations, Partial Results, and Charts

**Files:**
- Create: `frontend/src/components/ClarificationPrompt.tsx`
- Create: `frontend/src/components/ClarificationPrompt.test.tsx`
- Create: `frontend/src/components/ChartPanel.tsx`
- Create: `frontend/src/components/ChartPanel.test.tsx`
- Modify: `frontend/src/components/AgentTrace.tsx`
- Modify: `frontend/src/components/AgentTrace.test.tsx`
- Modify: `frontend/src/components/QueryResultView.tsx`
- Modify: `frontend/src/components/QueryResultView.test.tsx`
- Modify: `frontend/src/store/queryStore.ts`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Write failing state-rendering tests**

```typescript
it("renders clarification choices inline and submits the selected option", async () => {
  const user = userEvent.setup();
  const onResolve = vi.fn();
  render(<ClarificationPrompt response={grossNetClarification} onResolve={onResolve} />);
  await user.click(screen.getByRole("button", { name: "Net revenue" }));
  expect(onResolve).toHaveBeenCalledWith("c1", { option_id: "net" });
});

it("maps a ranking ChartSpec to a horizontal ECharts bar", () => {
  const { container } = render(<ChartPanel spec={rankingSpec} rows={rankingRows} />);
  expect(mockedReactECharts).toHaveBeenCalledWith(
    expect.objectContaining({ option: expect.objectContaining({ yAxis: expect.any(Object) }) }),
    expect.anything(),
  );
  expect(container.querySelector(".chart-panel")).toBeInTheDocument();
});

it("labels trace as operational execution rather than thought process", () => {
  render(<AgentTrace steps={[traceEvent]} />);
  expect(screen.getByText("执行过程")).toBeVisible();
  expect(screen.queryByText("思考过程")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Verify failure**

Run: `cd frontend && npm test -- --run src/components/ClarificationPrompt.test.tsx src/components/ChartPanel.test.tsx src/components/AgentTrace.test.tsx src/components/QueryResultView.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Make the store consume the streaming endpoint**

On `sendMessage`, create a loading turn and an `AbortController`. Append trace events in sequence. Map final kinds to `success`, `awaiting_clarification`, `partial`, or `error`. Add `resolveClarification(turnId, clarificationId, resolution)` and `cancelActiveQuery`. Preserve retry behavior for stable error responses.

- [ ] **Step 4: Implement inline clarification**

Render 2–4 full-width, restrained text buttons under the AskData identity. Add one “其他” action that reveals a small text field. Use semantic buttons, visible focus, `aria-describedby`, and prevent double submission while loading. Do not use a modal.

- [ ] **Step 5: Implement safe ChartSpec adapters**

Create one pure option builder per supported type. Read values only through fields named in the validated spec and returned rows. Use current CSS variables via computed theme colors or a small fixed token mapping; never evaluate code from the API. Set `aria-label` and include a short textual chart summary for screen readers.

- [ ] **Step 6: Extend `QueryResultView` in the existing vertical order**

Order: identity, loading/clarification/answer, partial warning, operational trace, SQL panel, chart panel, existing result table. Preserve the current error block and Markdown rendering.

- [ ] **Step 7: Add native CSS only**

Use existing font stacks, spacing rhythm, borders, `--warning`, `--accent`, `--surface*`, the 900px content width, current responsive breakpoint, and reduced-motion rule. Do not add gradients, a new shell, tabs, or proprietary fonts.

- [ ] **Step 8: Run focused and full frontend verification**

Run: `cd frontend && npm test -- --run`
Expected: all frontend tests PASS.

Run: `cd frontend && npm run build`
Expected: TypeScript and Vite build exit 0.

- [ ] **Step 9: Commit V2 result states**

```bash
git add frontend/src/components/ClarificationPrompt.tsx frontend/src/components/ClarificationPrompt.test.tsx frontend/src/components/ChartPanel.tsx frontend/src/components/ChartPanel.test.tsx frontend/src/components/AgentTrace.tsx frontend/src/components/AgentTrace.test.tsx frontend/src/components/QueryResultView.tsx frontend/src/components/QueryResultView.test.tsx frontend/src/store/queryStore.ts frontend/src/styles.css
git commit -m "feat(frontend): render V2 query states and charts"
```

## Task 14: Add the Versioned Demo Evaluation Suite

**Files:**
- Create: `backend/askdata/eval/demo_suite.py`
- Create: `tests/fixtures/v2_demo_cases.json`
- Create: `tests/test_demo_suite.py`
- Modify: `backend/askdata/cli.py`
- Modify: `benchmarks/README.md`

- [ ] **Step 1: Add a failing metrics test with representative fixtures**

```json
[
  {"id":"ranking","category":"clear","question":"top five schools by enrollment","expected_kind":"answer","expected_chart":"horizontal_bar"},
  {"id":"revenue","category":"ambiguous","question":"show revenue","expected_kind":"clarification"},
  {"id":"sss","category":"semantic_mapping","question":"list State Special Schools","expected_context":"SSS = State Special School"},
  {"id":"missing_students","category":"unanswerable","question":"list student names","expected_kind":"error"}
]
```

```python
def test_demo_metrics_report_false_clarification_and_proxy_query_rates():
    report = DemoSuite(cases()).Compare(predictions())
    assert report["clarification_precision"] == 1.0
    assert report["false_clarification_rate"] == 0.0
    assert report["proxy_query_rate"] == 0.0
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/test_demo_suite.py -q`
Expected: FAIL because `DemoSuite` is missing.

- [ ] **Step 3: Implement deterministic suite metrics**

Report case counts, pass rates by category, clarification precision/recall, false clarification, unanswerable precision/recall, proxy-query rate, ChartSpec validity, retrieval table/column recall@K, stream parity, restart persistence, p50/p95 latency, LLM calls, SQL executions, and token usage where available. Never infer a passing result from missing fields.

- [ ] **Step 4: Add the CLI command**

```text
uv run askdata eval-demo --cases tests/fixtures/v2_demo_cases.json --out reports/v2-demo.json
```

The command exits nonzero if any golden journey fails and prints a concise category table.

- [ ] **Step 5: Run demo and existing BIRD metric tests**

Run: `uv run pytest tests/test_demo_suite.py tests/test_metrics.py tests/test_runner.py -q`
Expected: PASS.

- [ ] **Step 6: Document comparable evaluation commands**

Update `benchmarks/README.md` with `eval-demo`, the fixed BIRD manifest command, and the rule that strict and relaxed execution accuracy are always reported together.

- [ ] **Step 7: Commit evaluation tooling**

```bash
git add backend/askdata/eval/demo_suite.py backend/askdata/cli.py tests/fixtures/v2_demo_cases.json tests/test_demo_suite.py benchmarks/README.md
git commit -m "feat(eval): add V2 demo regression suite"
```

## Task 15: Integrated Verification and Demo Evidence

**Files:**
- Modify: `README.md`
- Create: `reports/v2-demo-summary.md`

- [ ] **Step 1: Run the complete backend suite**

Run: `uv run pytest -q`
Expected: zero failures.

- [ ] **Step 2: Run the complete frontend suite and build**

Run: `cd frontend && npm test -- --run && npm run build`
Expected: zero test failures and build exit 0.

- [ ] **Step 3: Run the five golden journeys**

Run: `uv run askdata eval-demo --cases tests/fixtures/v2_demo_cases.json --out reports/v2-demo.json`
Expected: all five required journey categories pass, including zero proxy queries.

- [ ] **Step 4: Exercise vector-service outage**

Run the semantic-mapping cases once with the configured service and once with `VECTOR_RETRIEVAL_ENABLED=false`. Record whether the lexical fallback answered, clarified, or returned unanswerable; it must not crash or fabricate a result.

- [ ] **Step 5: Rerun the fixed BIRD baseline**

Run:

```bash
uv run askdata eval-bird \
  --question-manifest benchmarks/bird-minidev-v4pro-seed42-100.json \
  --out reports/bird-v2.1-100.json
```

Expected: a complete report containing strict and relaxed execution accuracy. Do not set a pass/fail improvement threshold until the integrated result exists.

- [ ] **Step 6: Verify restart persistence manually through the API**

Create a session, complete a query, stop and restart the server, list sessions, and reopen the same session. Record the session ID and observed turn count in `reports/v2-demo-summary.md` without storing secrets or full database rows.

- [ ] **Step 7: Update setup and feature documentation**

Document application-database location, optional `uv sync --extra vector`, embedding/Milvus environment variables, index build, streaming endpoint, session behavior, ChartSpec contract, and fallback behavior in `README.md`.

- [ ] **Step 8: Commit verified evidence**

```bash
git add README.md reports/v2-demo-summary.md
git commit -m "docs: record AskData V2.1 verification"
```

## Final Review Checklist

- [ ] Every response has a valid discriminator, session ID, turn ID, and structured trace.
- [ ] Clarification is asked only for material, schema-supported alternatives.
- [ ] Missing business entities never produce proxy SQL.
- [ ] The returned answer, SQL, rows, and chart use the same selected candidate.
- [ ] No request executes more than six SQL candidates.
- [ ] Vector-service failures degrade to lexical retrieval without failing the query.
- [ ] Chart payloads contain no executable code or arbitrary ECharts options.
- [ ] The existing frontend shell, typography stacks, tokens, responsive behavior, and accessibility patterns remain intact.
- [ ] Sessions survive restart and are reopenable from the rail drawer.
- [ ] Streaming and non-streaming endpoints return equivalent final results.
- [ ] Strict and relaxed BIRD execution accuracy are both reported from the fixed manifest.
