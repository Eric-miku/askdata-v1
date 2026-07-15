# AskData V2.1 — Natural Language to SQL

AskData turns Chinese or English questions into read-only SQL, executes the selected query, and returns a typed answer, bounded result preview, operational trace, and optional chart specification. The backend is FastAPI; the frontend is React, TypeScript, Ant Design, and ECharts.

## Quick start

Python 3.10 or newer and Node.js are required.

```bash
# Backend dependencies and API, from the repository root
bash scripts/setup-dev-env.sh
uv run askdata serve                    # http://127.0.0.1:8000

# Frontend, in a second shell
cd frontend
npm install
npm run dev                             # http://127.0.0.1:5173
```

The application database defaults to `data/askdata-app.sqlite`. It stores sessions, turns, clarifications, chart specifications, and bounded result previews. Set `APP_DATABASE_PATH` to move it; use a writable path and back it up like any other SQLite database. WAL mode is enabled at startup. Conversation history survives application restarts and can be listed, reopened, or explicitly deleted from the history drawer.

## Configuration

Create `.env` in the repository root. Do not commit real keys.

```env
LLM_API_BASE=https://api.example.com/v1
LLM_API_KEY=replace-me
LLM_MODEL_NAME=your-tool-calling-model

BIRD_DATA_DIR=data/bird
BIRD_INSTRUCTIONS_DIR=data/bird/instructions
APP_DATABASE_PATH=data/askdata-app.sqlite

# Optional hybrid retrieval
VECTOR_RETRIEVAL_ENABLED=true
EMBEDDING_API_URL=https://embedding.example.com/v1
EMBEDDING_API_KEY=replace-if-required
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DIMENSION=1024
MILVUS_URI=http://127.0.0.1:19530
MILVUS_COLLECTION=askdata_schema_chunks
```

`MILVUS_URI` is the full URI consumed by the application; `MILVUS_HOST` alone is not read. The configured embedding model must return the declared model name, one vector per input in index order, and exactly `EMBEDDING_DIMENSION` finite values.

## BIRD data

The processed data contract is:

```text
data/bird/
  databases/<database_id>/<database_id>.sqlite
  processed/databases.json
  processed/questions.jsonl
  processed/schemas/<database_id>.json
  processed/schema_prompts/<database_id>.md
  instructions/<database_id>.md              # optional business mappings
```

Prepare Mini-Dev with the team-owned pipeline:

```bash
./data-processing/askdata prepare-bird \
  --raw-dir data/bird/raw/minidev \
  --db-dir data/bird/databases \
  --out-dir data/bird/processed \
  --demo-db-limit 11 \
  --demo-question-limit 500 \
  --validate-sql \
  --build-cache \
  --force
```

Metadata paths should either be absolute or valid relative to the repository root. If a processed bundle was created in another checkout, regenerate it or update its declared database paths before evaluation.

## Optional vector retrieval

Lexical schema retrieval is always available. To add semantic retrieval, install the optional Milvus client and index each database after its schema, evidence, values, or instructions change:

```bash
uv sync --extra vector
uv run askdata index-schema \
  --database-id california_schools \
  --processed-dir data/bird/processed
```

When the semantic retriever is built, AskData validates the embedding response and performs a Milvus probe. Runtime retrieval embeds the original question (and resolved conversational wording when different), searches attributable schema/value/evidence/example chunks, and fuses lexical and dense rankings with reciprocal-rank fusion. Foreign-key neighbors are added for join coverage.

If vector retrieval is disabled or its configuration is incomplete, AskData silently uses lexical retrieval. If a fully configured remote embedding or Milvus service fails validation or search, AskData falls back to lexical retrieval and emits a fixed safe warning. It never forwards remote exception details to the response. Low schema coverage may lead to a schema-supported clarification; when the requested entity is absent, the response is `unanswerable_from_schema` and no proxy SQL is executed.

## Query API

Submit either a new question or one clarification resolution:

```bash
curl -sS http://127.0.0.1:8000/api/query \
  -H 'Content-Type: application/json' \
  -d '{"question":"top five schools by enrollment","database_id":"california_schools"}'
```

`POST /api/query` uses a discriminated contract whose reserved `kind` values are `answer`, `clarification`, `partial`, and `error`. The current `QueryService` emits `answer`, `clarification`, or `error`; `partial` is reserved for compatible future producers and is already supported by the frontend and evaluation tooling. Every emitted response includes `session_id`, `turn_id`, and a curated operational `trace`. Answer responses may include SQL, columns, at most 100 preview rows, confidence, and a chart.

For live progress, send the same request to `POST /api/query/stream`. The response is `text/event-stream`: zero or more ordered `trace` frames may be followed by an optional `clarification` or `error` lifecycle frame, and every normally completed stream ends with exactly one `final` frame. There is no `partial` lifecycle frame. The `final` payload uses the same response contract as `/api/query`; clients should use it as the source of truth and close or abort the stream when navigating away.

Clarification responses contain a `clarification_id` and 2–4 schema-supported options. Resolve one with the existing session:

```json
{
  "database_id": "california_schools",
  "session_id": "<session-id>",
  "clarification": {
    "clarification_id": "<clarification-id>",
    "option_id": "<option-id>"
  }
}
```

Exactly one of `option_id` or free-text `text` is allowed. Clarifications are reserved for material alternatives; vague wording alone does not justify invented options.

## ChartSpec contract

Charts are declarative data contracts, not executable ECharts options:

```json
{
  "type": "line | vertical_bar | horizontal_bar | pie | scatter",
  "title": "Monthly revenue",
  "category_field": "month",
  "category_label": "Month",
  "value_fields": ["revenue"],
  "value_labels": {"revenue": "Revenue"},
  "reason": "time_series | comparison | ranking | proportion | correlation"
}
```

The frontend reads values only from returned row fields named by the validated specification. Unsupported shapes remain table-only, and empty results remain successful answers with an empty table. The reserved `partial` contract carries explicit limitations and suggestions when a compatible producer supplies it, but the current `QueryService` does not emit that state. Execution errors use stable safe error payloads and never expose database or model internals.

## Sessions and metadata

- `GET /api/sessions?limit=50` lists recent conversations.
- `GET /api/sessions/{session_id}` reopens a conversation and all persisted turns.
- `DELETE /api/sessions/{session_id}` explicitly deletes it.
- `GET /api/metadata/databases` lists discoverable SQLite databases.
- `GET /api/metadata/{database_id}/tables` returns table and column metadata.

Starting a new chat or switching databases does not delete persisted history. Concurrent turns for the same session are serialized, while separate sessions can proceed independently.

## Evaluation and verification

```bash
# Complete local gates
uv run pytest -q
cd frontend && npm test -- --run && npm run build

# Offline comparison; predictions must be captured separately from this system
uv run askdata eval-demo \
  --cases tests/fixtures/v2_demo_cases.json \
  --predictions reports/v2-demo-predictions.json \
  --out reports/v2-demo.json

# Fixed BIRD baseline; always report relaxed and strict execution accuracy together
uv run askdata eval-bird \
  --question-manifest benchmarks/bird-minidev-v4pro-seed42-100.json \
  --out reports/bird-v2.1-100.json
```

The embedded predictions in `tests/fixtures/v2_demo_cases.json` are test examples only and are not release evidence. See [benchmarks/README.md](benchmarks/README.md) for comparable evaluation rules and [reports/v2-demo-summary.md](reports/v2-demo-summary.md) for the latest integrated verification record.

## Project structure

```text
backend/askdata/
  agent/          intent, ambiguity, SQL candidate, verification, and fallback pipeline
  api/            FastAPI routes, typed responses, streaming, and persistent sessions
  db/             read-only validation and bounded SQLite execution
  eval/           deterministic demo comparison and BIRD evaluation
  tools/          lexical/hybrid retrieval, embeddings, Milvus, analysis, and charts
frontend/src/     React application, API clients, state, and accessible result views
tests/            backend unit and integration tests
data-processing/  BIRD preparation pipeline
```

## Safety boundaries

- SQL is parsed and restricted to one read-only statement before execution.
- The agent may attempt no more than six SQL candidates for one request.
- Result previews and operational traces are bounded and sanitized.
- Missing schema entities do not produce proxy queries.
- Vector retrieval is optional and fails closed to lexical retrieval.
- Charts contain no executable code or arbitrary ECharts configuration.
