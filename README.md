# AskData V1 — NL2SQL Platform

Natural-language-to-SQL platform. Users ask questions in plain Chinese or English; the system generates SQL, executes against a database, and returns charts with explanations.

**Stack**: Python 3.13 / FastAPI / LangGraph / SQLAlchemy / React + Ant Design + ECharts / OpenAI-compatible LLM

## Quick Start

```bash
# Backend (run from the repository root)
bash scripts/setup-dev-env.sh
uv run askdata serve          # start API at :8000

# Frontend
cd frontend && npm install && npm run dev   # start at :5173

# Evaluation
uv run askdata eval-bird --limit 100 --seed 42 --out reports/eval.json
```

## Project Structure

```
askdata-v1/
  backend/askdata/
    agent/             # Agent orchestration
      graph.py         #   AgentGraph — main NL2SQL chain (ReAct or one-shot)
      react_sql_agent.py  #   ReActSqlAgent — tool-calling loop with self-repair
      prompts.py       #   SQL generation prompts with schema-linking checklist
      state.py         #   AgentState TypedDict
    api/               # FastAPI layer
      routes.py        #   /api/query, /api/metadata, session management
      schemas.py       #   QueryRequest / QueryResponse Pydantic models
      session_manager.py  #   Async in-memory sessions
      trace.py         #   Per-request step logger
      app.py           #   FastAPI app factory + CORS
    core/              # Config and LLM client
      config.py        #   Pydantic Settings (LLM_API_BASE, KEY, MODEL, BIRD_DATA_DIR)
      llm.py           #   LLMClient — OpenAI-compatible Complete() + Chat()
      paths.py         #   Project-root-relative path resolver
    db/                # SQL execution and safety
      executor.py      #   SQLExecutor — pagination, type inference, error codes
      validator.py     #   SQLValidator — sqlglot AST-based read-only enforcement
    tools/             # Schema retrieval and answer generation
      retriever.py     #   BirdSchemaIndex + SemanticRetriever
      analyzer.py      #   ResultAnalyzer — LLM Chinese explanation with fallback
      skill_loader.py  #   SkillLoader — reusable SQL pattern templates
    skills/            # Skill markdown files
      compare-periods.md / ratio-analysis.md / rank-top-bottom.md
    eval/              # BIRD benchmark evaluation
      metrics.py       #   BirdResultComparer (4-tier matching) + ExactMatch
      runner.py        #   EvalRunner — self-contained BIRD evaluation
    data/bird_io.py    # Native data-processing contract + legacy fallback
    cli.py             # CLI entry: eval-bird, databases, chat, serve
  frontend/            # React + TypeScript + Vite
    src/
      api/query.ts     #   Backend API client
      components/      #   DatabaseSelector, ResultTable
      pages/           #   QueryResultDemo
      store/           #   Zustand state
      types/query.ts   #   TypeScript types
  tests/               # Backend unit and integration tests
  data-processing/     # Team-owned BIRD data prep pipeline
  benchmarks/          # Versioned question manifests (no database files)
  data/bird/           # SQLite databases + processed schema (gitignored)
```

## Architecture

```
POST /api/query
  → AgentGraph.Run()
    → SemanticRetriever.Retrieve()     # schema + business context
    → ReActSqlAgent.Run()              # tool-calling loop
      → LLM.Chat() → run_query(sql)
        → SQLExecutor.execute()        # run against SQLite
      → error? → LLM repairs SQL
      → success? → LLM produces answer
    → ResultAnalyzer.Analyze()         # Chinese explanation
  → QueryResponse { answer, sql, columns, rows, chart, trace }
```

### Two execution modes

1. **ReAct mode** (default): `AgentGraph` delegates to `ReActSqlAgent` which runs a tool-calling loop. LLM calls `run_query`, reads results or errors, and iterates until it has an answer.

2. **One-shot fallback**: If ReAct is unavailable, `AgentGraph` uses a single LLM call for SQL generation with one repair attempt on failure. Includes Skills system context.

## Configuration (.env)

```env
LLM_API_BASE=http://localhost:9001/v1
LLM_API_KEY=your-key
LLM_MODEL_NAME=Qwen3.5-397B-A17B
BIRD_DATA_DIR=data/bird
BIRD_INSTRUCTIONS_DIR=data/bird/instructions
```

## Data Setup

BIRD Mini-Dev is prepared by the team-owned `data-processing` tool. From the repository root:

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

The application reads the native outputs directly: metadata in `databases.json`, structured schemas in `schemas/*.json`, and questions in `questions.jsonl`. Legacy inline camelCase schemas and `questions.json` remain read-only fallbacks.

For the historical 100-question comparison with `deepseek-v4-pro`:

```bash
uv run askdata eval-bird \
  --model-name deepseek-v4-pro \
  --question-manifest benchmarks/bird-minidev-v4pro-seed42-100.json \
  --out reports/bird-eval-v4pro-100.json
```

### macOS editable-install recovery

If `uv run askdata` reports `ModuleNotFoundError` after the repository was moved from `intern agents` to `intern-agents`, inspect `.venv/lib/python*/site-packages/*.pth`. Under a synchronized `Documents` directory, macOS can repeatedly mark a dot-directory virtual environment as hidden and make Python skip editable-install `.pth` files. Run the bootstrap once:

```bash
bash scripts/setup-dev-env.sh
uv run askdata --help
```

On macOS the script keeps uv's `.venv` path as a local symlink to the gitignored, non-dot `venv.nosync` directory. Other platforms run a normal `uv sync`. Normal development does not require `uv pip install -e .` before each command.

## Testing

```bash
uv run pytest -q
cd frontend && npm run build
```

## More Documentation

- [Backend instructions](backend/INSTRUCTIONS.md): architecture, BIRD data contract, evaluation rules, and known limitations.
- [BIRD benchmark](benchmarks/README.md): fixed 100-question manifest and comparable accuracy results.
- [Data processing](data-processing/README.md): team-owned BIRD preparation commands and output contract.

## Key Design Decisions

- **PascalCase** for all NL2SQL-facing methods (`Build`, `Retrieve`, `Run`, `Compare`). Team `db/` modules use snake_case (`execute`, `validate`).
- **ReAct loop** with max 8 iterations; LLM self-corrects on SQL and answer-shape warnings.
- **Column matching** in eval uses 4 tiers: strict (all columns match) → name (shared subset) → position (by index) → subset (try combinations).
- **Skills** are markdown files auto-loaded at agent start — no code changes needed to add patterns.
- **Business context** per database via `data/bird/instructions/<db>.md`.
