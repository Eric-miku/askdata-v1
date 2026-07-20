# AskData Backend Instructions

## Responsibilities

The backend converts a natural-language question into SQLite SQL, executes it, and returns the answer, SQL, columns, rows, trace, and optional chart through the existing camelCase API contract.

The main flow is:

```text
API / CLI -> AgentGraph -> SemanticRetriever -> ReActSqlAgent -> query_runner
                                    |
                                    +-> BirdSchemaIndex
```

- `askdata/data/bird_io.py` is the only application-side BIRD data adapter.
- `SemanticRetriever` performs schema grounding and evidence injection.
- `ReActSqlAgent` may inspect values before producing final SQL, but keeps at most two successful candidates and runs at most eight iterations.
- `EvalRunner` evaluates the same application pipeline used by the API.

## BIRD Data Contract

The team-owned `data-processing/` project remains the only BIRD producer. Do not copy its preparation implementation into the backend CLI.

The preferred application inputs are:

```text
data/bird/processed/
  databases.json
  questions.jsonl
  schemas/<database_id>.json
  schema_prompts/<database_id>.md
```

Use snake_case internally. Legacy `questions.json` and inline camelCase schemas are compatibility fallbacks only. Resolve declared paths from the project root, verify every SQLite file exists, and open evaluation databases read-only so a bad path cannot create an empty database.

## Evaluation

Use the versioned manifest for comparable runs:

```bash
uv run askdata eval-bird \
  --model-name deepseek-v4-pro \
  --question-manifest benchmarks/bird-minidev-v4pro-seed42-100.json \
  --out reports/bird-eval-v4pro-100.json
```

Always report strict and relaxed execution accuracy together. Do not improve relaxed EA by weakening the comparer. Full reports and SQLite data remain gitignored; commit only manifests and short benchmark summaries.

## Development Checks

```bash
uv sync
uv run askdata --help
uv run askdata databases
uv run pytest -q
cd frontend && npm run build
```

On macOS, run `bash scripts/setup-dev-env.sh` once if a synchronized Documents directory marks `.venv` or editable-install `.pth` files as hidden. Normal commands must not require repeated `uv pip install -e .` calls.

## Known Limitation

Answer-shape checks validate count/list/scalar/rate output structure but do not yet prove that a requested business entity exists in the schema. A question about student names in `california_schools`, for example, can cause an invalid proxy query over school or administrator names.

The ReAct text answer and selected SQL candidate are also returned independently. Until an explicit answerability and answer/SQL consistency guard is added, inspect the trace when the prose answer contradicts the SQL or rows.
