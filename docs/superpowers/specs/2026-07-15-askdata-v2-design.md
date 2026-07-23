# AskData V2 Design

**Date:** 2026-07-15

**Status:** Approved design; pending written-spec review

**Target release:** V2.1 — demo-ready SQLite intelligence

## 1. Purpose

AskData V2.1 will deliver a convincing, auditable end-to-end demonstration for both product stakeholders and technical evaluators. It will preserve the existing frontend, improve the trustworthiness of natural-language-to-SQL behavior, add clarification and charts, persist conversations across restarts, and optionally enhance schema retrieval through remote embeddings and Milvus.

V2.1 remains SQLite-only and single-user. MySQL, PostgreSQL, authentication, and multi-user ownership are explicitly deferred.

## 2. Success Criteria

The primary success criterion is a polished end-to-end demonstration that also exposes verifiable technical evidence.

The demonstration must support these five journeys:

1. A clear ranking question produces verified SQL, an answer, a horizontal chart, and a table.
2. A materially ambiguous metric such as gross versus net revenue produces an inline clarification and resumes after selection.
3. A vocabulary mismatch such as “State Special School” resolves to a coded database value such as `EdOpsCode = 'SSS'`.
4. A server restart preserves conversations that can be listed and reopened.
5. A request for an entity absent from the schema produces an honest unanswerable response rather than proxy SQL over the wrong entity.

Latency has no hard release threshold, but p50, p95, LLM calls, SQL executions, and token usage must be reported.

## 3. Scope

### 3.1 V2.1 scope

- Persistent single-user conversations and session reopening.
- Explicit answer, clarification, partial, and error response states.
- A hybrid staged query pipeline around the existing ReAct SQL loop.
- Intent contracts, deterministic SQL checks, result checks, and candidate selection.
- Material-ambiguity detection and resumable clarification.
- Optional hybrid retrieval using token matching plus remote dense embeddings and Milvus.
- Value-aware schema context, evidence retrieval, join expansion, and coverage recovery.
- Structured operational traces, including a streaming frontend endpoint.
- Deterministic, validated chart specifications rendered with ECharts.
- Integration into the existing frontend design without replacing its shell or visual language.
- Evaluation for SQL correctness, retrieval, clarification, answer consistency, charts, persistence, and fallback behavior.

### 3.2 Deferred to V2.2

- MySQL and PostgreSQL adapters.
- Dialect-specific schema introspection and integration suites.
- Calibrated cross-encoder reranking.
- Retrieval feedback and learned alias expansion.

### 3.3 Deferred to V2.3

- Authentication and authorization.
- Per-user session ownership.
- Quotas, audit retention, distributed workers, and scalable session storage.

## 4. Architecture

V2.1 uses a hybrid staged pipeline. Deterministic orchestration controls ordering, retry budgets, SQL safety, response contracts, persistence, and chart validation. The LLM is used only where language or semantic judgment is necessary.

```text
Request + persisted session
  -> answerability and material-ambiguity gate
  -> hybrid context retrieval
  -> focused ReAct SQL generation and repair
  -> deterministic SQL quality gate
  -> safe SQLite execution
  -> result verification and candidate ledger
  -> select one final executed candidate
  -> answer synthesis from that candidate only
  -> deterministic ChartSpec selection
  -> persist final turn
  -> answer | clarification | partial | error
```

The existing `ReActSqlAgent` remains responsible for SQL generation, inspection queries, and targeted repair. It does not own persistence, response-state selection, chart generation, or global retry policy.

### 4.1 LLM responsibilities

- Generate plausible interpretations when ambiguity may be material.
- Generate and repair SQL grounded in retrieved context.
- Perform targeted structured semantic review when deterministic checks cannot resolve alignment.
- Synthesize a natural-language answer from the selected final result.

### 4.2 Deterministic responsibilities

- Workflow ordering and retry budgets.
- SQL parsing, safety, and schema-reference validation.
- Answer-shape and result-shape checks.
- Candidate recording and selection.
- Response schemas and stream-event schemas.
- Session transactions.
- Chart selection constraints and ChartSpec validation.

## 5. Response and Streaming Contracts

### 5.1 Discriminated query responses

Every response includes `kind`, `session_id`, `turn_id`, and a structured `trace`.

```text
AnswerResponse(kind="answer")
  answer, sql, columns, rows, chart, confidence, trace

ClarificationResponse(kind="clarification")
  clarification_id, question, options, recommended_option_id?, trace

PartialResponse(kind="partial")
  answer, limitations, suggestions, sql?, columns?, rows?, chart?, confidence, trace

ErrorResponse(kind="error")
  code, message, retryable, suggestions, trace
```

An error response must not contain fabricated data. A partial response is valid only when an executed candidate answers a verified subset of the request and the missing requirements are explicit.

### 5.2 Request compatibility

`POST /api/query` continues to accept the existing question request:

```json
{
  "question": "Show the top five schools by enrollment",
  "database_id": "california_schools",
  "session_id": "optional-session-id"
}
```

Clarification continuation uses the same endpoint with a clarification payload. Exactly one of `question` or `clarification` is accepted.

```json
{
  "database_id": "finance",
  "session_id": "session-id",
  "clarification": {
    "clarification_id": "clarification-id",
    "option_id": "net"
  }
}
```

Free-text clarification uses `text` instead of `option_id`. The backend verifies that the clarification is pending and belongs to the specified session.

### 5.3 Streaming endpoint

The existing non-streaming endpoint remains available for CLI, evaluation, tests, and simple clients.

The frontend uses:

```text
POST /api/query/stream
Content-Type: application/json
Response: text/event-stream
```

The stream emits only structured operational events:

```text
event: trace
data: {"step":"RetrieveSchema","status":"success","message":"...","sequence":1}

event: final
data: {discriminated QueryResponse}
```

Supported event types are `trace`, `clarification`, `final`, and `error`. Sequence numbers are monotonic. A disconnected client triggers best-effort cancellation of remaining agent work. Raw chain-of-thought is never emitted or stored.

## 6. Persistent Sessions

Application sessions live in a dedicated SQLite database, separate from the selected analytical databases.

### 6.1 Tables

```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    database_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    response_kind TEXT NOT NULL,
    answer TEXT,
    sql TEXT,
    result_preview_json TEXT,
    chart_json TEXT,
    confidence TEXT,
    error_json TEXT,
    trace_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE clarifications (
    id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL UNIQUE REFERENCES turns(id) ON DELETE CASCADE,
    prompt TEXT NOT NULL,
    options_json TEXT NOT NULL,
    resolution_json TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
```

The store enables foreign keys, WAL mode, a busy timeout, and transactional writes. It persists only the bounded result preview returned to the client, not an unbounded query result.

### 6.2 Session API

- `GET /api/sessions` lists recent conversations.
- `POST /api/sessions` creates a conversation.
- `GET /api/sessions/{id}` returns a session and ordered turns.
- `DELETE /api/sessions/{id}` deletes a session and cascades its turns.

There is no `user_id` in V2.1 because the release is explicitly single-user.

## 7. Ambiguity and Answerability

Retrieval uncertainty and user ambiguity are separate conditions. Low similarity alone must not trigger a clarification.

The ambiguity gate returns one of:

```text
CLEAR
RESOLVABLE_FROM_CONTEXT
MATERIALLY_AMBIGUOUS
UNANSWERABLE_FROM_SCHEMA
```

A clarification is allowed only when all of these conditions hold:

1. At least two interpretations are supported by schema or business evidence.
2. Neither interpretation clearly dominates after session context and documented defaults are applied.
3. Choosing between them changes the selected entity, metric, filter, aggregation, grouping, time range, or ranking direction.
4. The database can answer both interpretations.

The gate produces structured candidate interpretations and their material SQL differences. It does not use the original draft rule that asks whenever any independent ambiguity score exceeds `0.6`. Thresholds and margins will be calibrated using a labeled ambiguity set rather than treated as universal constants.

If a requested business entity is absent from the schema, the result is `UNANSWERABLE_FROM_SCHEMA`. The system must name the missing concept and may suggest available alternatives, but it must not generate proxy SQL over a different entity.

## 8. Hybrid Retrieval

### 8.1 Model

The preferred dense embedding model is `BAAI/bge-m3`, configured rather than hardcoded:

```env
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DIMENSION=1024
```

BGE-M3 is multilingual and supports Chinese and English. Its model card documents 1024-dimensional embeddings, long inputs, and hybrid-retrieval use: <https://huggingface.co/BAAI/bge-m3>.

The current repository does not identify the model served by the team embedding endpoint. Startup validation must verify the configured model and returned dimension. A dimension or model mismatch disables vector retrieval with a trace warning rather than breaking queries.

### 8.2 Indexed chunks

The index contains separate, attributable chunk types:

- Schema chunks: table and column names, descriptions, types, keys, and join neighbors.
- Value-semantic chunks: bounded categorical values and code meanings.
- Evidence chunks: BIRD evidence, business instructions, aliases, and metric definitions.
- Example chunks: validated question-to-SQL examples, clearly marked as examples rather than schema facts.

Every chunk has a stable identifier and metadata including database, table, column, source type, and source version. Retrieval is filtered by the selected `database_id`.

The index does not embed every database row. Profiling is limited to safe low-cardinality values, code mappings, numeric ranges, null ratios, date ranges, and a bounded set of representative strings.

### 8.3 Question representations

The system Unicode-normalizes the question while preserving its original language, quoted values, numbers, and dates. It embeds the original question and, when needed, a context-resolved rewrite as separate queries.

Retrieval combines:

- Existing lexical and identifier matching.
- Dense-vector results.
- Value-semantic and evidence matches.
- Primary-key, foreign-key, and join-neighbor expansion.

Rank fusion combines ranked lists because lexical and vector scores are not assumed to be calibrated to one scale. The design does not use the draft's fixed `0.3 * token + 0.7 * vector` formula.

### 8.4 Recall safeguards

- Always include a schema backbone containing all table names, column names, primary keys, and foreign keys for the selected database.
- Retrieve broadly, then rerank or prune for prompt construction.
- Take the union of lexical, dense, value, and evidence results.
- Expand selected tables through join neighbors.
- Check coverage of entity, metric, filters, grouping, time, comparison, and ranking requirements.
- When coverage is weak, perform at most one HyDE or terminology-expansion recovery pass.
- If the remote embedding or Milvus service is unavailable, fall back to the lexical retriever and emit a structured warning.

## 9. SQL Quality, Candidate Selection, and Fallback

### 9.1 Intent contract

Before SQL generation, the pipeline creates a structured intent contract containing requested entities, output attributes, metrics, filters, grouping level, ordering, expected cardinality, and time conditions.

### 9.2 Static quality gate

Every SQL candidate is checked before execution for:

- Successful parsing and read-only safety.
- Existing tables and columns.
- Requested versus selected columns.
- Aggregation and grouping alignment.
- Limit and ordering appropriateness.
- Join connectivity and unnecessary joins.
- Existing answer-shape rules.

Deterministic checks run for every candidate. A targeted structured LLM semantic review runs only when alignment cannot be decided from the AST, schema, and intent contract. A separate LLM self-scoring call is not mandatory before every execution.

### 9.3 Result quality gate

After execution, checks cover:

- Empty versus legitimately empty results.
- Expected scalar, list, ranking, ratio, or grouped shape.
- Suspicious counts, null-only outputs, and missing requested coverage.
- Whether a successful inspection query is being mistaken for the final answer.

### 9.4 Candidate ledger

Every candidate records SQL, result preview, referenced context, static checks, execution outcome, result checks, covered intent elements, and failure reasons.

Candidate selection prioritizes:

1. SQL safety and successful execution.
2. Complete intent coverage.
3. No unresolved static or result warnings.
4. Directness and minimal unnecessary output.

Recency is not allowed to override semantic correctness.

The selected candidate becomes immutable before answer synthesis. The natural-language answer, chart, table, and returned SQL all derive from that exact candidate. This removes the V1 risk of returning prose and SQL from different candidates.

### 9.5 Recovery budget

The pipeline allows at most six SQL executions:

1. Initial candidate.
2. Targeted repair.
3. Second targeted repair when the failure class changed or progress was made.
4. Retrieval expansion and regeneration for grounding failure.
5. Alternate query plan for empty or suspicious results.
6. Final corrected candidate.

Successful verification exits early. Repeated identical SQL or identical failure classes without progress terminate early. LLM calls and SQL executions are counted separately.

### 9.6 Confidence

Confidence is reported as `high`, `medium`, or `low` and is derived from retrieval coverage, static checks, execution success, and result checks. It is not an uncalibrated number invented by the answer model.

## 10. Chart Design

The model does not call a `generate_chart` tool and does not emit arbitrary ECharts configuration. After final candidate selection, deterministic code produces a validated product-level `ChartSpec`.

```json
{
  "type": "horizontal_bar",
  "title": "Top 5 schools by enrollment",
  "category_field": "School",
  "value_fields": ["Enrollment"],
  "category_label": "School",
  "value_labels": {"Enrollment": "Students"},
  "reason": "ranking"
}
```

The specification contains no JavaScript callbacks, raw formatter code, colors, or arbitrary ECharts options.

Selection policy:

- Time plus metric becomes a line chart.
- Category comparison becomes a vertical bar chart.
- Ranking becomes a horizontal bar chart.
- Explicit share or proportion with no more than six meaningful categories may become a pie chart.
- Two numeric measures with enough rows may become a scatter plot.
- Sparse or unsuitable results remain table-only.

The frontend adapter maps the specification to responsive, theme-aware ECharts options and renders from the same rows returned with the selected SQL candidate.

## 11. Existing Frontend Integration

The existing frontend is the source of truth. V2 must preserve:

- The fixed 56px application rail.
- The database drawer's focus trap, Escape behavior, search, scrim, and 308px geometry.
- The 900px welcome, conversation, and composer width.
- The vertical conversation and result flow.
- The warm rust accent and restrained dark/light tokens.
- Georgia/Songti reading typography, Inter/system UI typography, and SFMono/Consolas code typography.
- Markdown answers, the collapsible trace, generated SQL panel, table behavior, mobile rail, reduced-motion behavior, and keyboard composer.

Styrene and Tiempos are proprietary. V2.1 does not bundle or imitate them without licensed assets. The current legal fallback stacks remain in use; licensed files may later be introduced through typography tokens.

### 11.1 Component additions

- Add a rail history action and a conversation drawer patterned on `AppSidebar`'s database drawer.
- Extend `QueryResultView` to render discriminated response states.
- Render clarifications inline under the AskData identity using restrained text choices, with a custom-answer path.
- Rename the visible trace label from “思考过程” to “执行过程”.
- Add a `ChartPanel` after the SQL panel and before the existing result table.
- Add a subtle warning block for partial results using existing warning tokens.
- Retain the existing error block and retry affordance for error responses.

No modal clarification flow, blue dashboard shell, tab-heavy result card, proprietary font copy, gradient, or decorative animation is introduced.

### 11.2 Frontend state

Turn state expands to:

```text
loading | awaiting_clarification | success | partial | error
```

The active turn accumulates streamed operational trace events. A clarification resolution continues the same turn and records the user's selection before the final response replaces the pending state.

Session state adds a recent-session list, loading/error state, and `openSession(session_id)`. Changing databases creates a new conversation; it no longer silently deletes persisted history.

The untracked legacy alternatives `DatabaseSelector.tsx` and `QueryInput.tsx` are not part of the active frontend path and are not used for V2.

## 12. Error Handling

- Invalid or unsafe SQL becomes a repairable candidate failure and is never executed.
- A remote retrieval failure falls back to lexical retrieval.
- A malformed LLM structured response is retried once with schema feedback, then falls back to a deterministic safe state.
- A missing pending clarification returns a stable non-retryable error.
- Session-store write failure prevents a response from being reported as durably saved and emits an operational error.
- Stream disconnection triggers best-effort cancellation; a completed persisted result remains reopenable.
- Chart validation failure falls back to table-only and does not fail the query.
- If no trustworthy candidate exists, return an error rather than a fabricated best effort.

## 13. Evaluation

### 13.1 Existing BIRD evaluation

Use a fixed versioned manifest and report strict and relaxed execution accuracy together. Also report valid SQL rate and exact match. The integrated V2.1 baseline must be rerun before an accuracy-improvement target is claimed.

### 13.2 Retrieval evaluation

- Gold table recall at K.
- Gold column recall at K.
- Evidence/value mapping recall.
- Coverage-recovery success rate.
- Remote-outage fallback success.

### 13.3 Ambiguity and answerability evaluation

- Clarification precision and recall.
- False-clarification rate on clear questions.
- Material-choice coverage.
- Unanswerable detection precision and recall.
- Proxy-query rate for missing entities, which must be zero in the golden suite.

### 13.4 Consistency and product evaluation

- Answer/SQL/result consistency.
- Static and result-gate pass rates.
- Candidate retry distribution.
- Valid ChartSpec rate and table-only fallback correctness.
- Session restart and reopen behavior.
- Stream-event ordering and final-response parity with the non-streaming endpoint.
- Existing backend and frontend tests, frontend build, and accessibility-focused component tests.

### 13.5 Demo regression suite

Create a versioned curated suite covering clear questions, ambiguous questions, missing entities, coded-value semantic mappings, empty results, partial results, chart types, persistence, streaming, and remote retrieval outage.

## 14. V2.1 Implementation Slices

1. **Baseline and contracts:** resolve branch integration, freeze discriminated responses, trace events, and ChartSpec while retaining the legacy query path.
2. **Persistence and history:** add the SQLite store, session list/reopen endpoints, history drawer, and restart test.
3. **Pipeline consistency:** add intent contracts, gates, candidate ledger, retry budget, and final answer binding.
4. **Clarification:** add material-ambiguity classification, pending clarification persistence, inline choices, and continuation.
5. **Hybrid retrieval:** add the configurable BGE-M3 client, schema/value/evidence index, fusion, coverage recovery, and fallback.
6. **Streaming trace:** add the frontend stream endpoint, event consumption, disconnection handling, and operational trace updates.
7. **Chart integration:** add deterministic ChartSpec construction, the ECharts adapter, responsive rendering, and validation fallback.
8. **Demo hardening:** run the golden journeys, outage exercises, regression suite, fixed BIRD evaluation, and final frontend polish.

Each slice must leave the repository in a tested, reviewable state. Implementation plans must identify exact files, tests written before behavior changes, verification commands, and review checkpoints.

## 15. Non-Goals and Guardrails

- Do not replace the frontend shell or visual language.
- Do not expose chain-of-thought.
- Do not treat vector similarity as proof of intent.
- Do not hide missing entities behind proxy queries.
- Do not let a model emit executable chart code or arbitrary ECharts options.
- Do not add MySQL, PostgreSQL, authentication, or multi-user ownership to V2.1.
- Do not weaken BIRD comparison metrics to report a higher score.
- Do not claim performance improvements without rerunning the fixed integrated baseline.
