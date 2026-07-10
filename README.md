# AskData V1 — NL2SQL Platform

Natural-language-to-SQL platform. Users ask questions in plain Chinese or English; the system generates SQL, executes against a database, and returns charts with explanations.

**Stack**: Python 3.10+ / FastAPI / LangGraph / SQLAlchemy / React + Ant Design + ECharts / Qwen3.5

## Quick Start

```bash
# Backend
cd backend && uv pip install -e .
uv run askdata serve          # start API at :8000

# Frontend
cd frontend && npm install && npm run dev   # start at :5173

# Evaluation
uv run askdata evaluate-bird --limit 100 --out reports/eval.json
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
    cli.py             # CLI entry: evaluate-bird
  frontend/            # React + TypeScript + Vite
    src/
      api/query.ts     #   Backend API client
      components/      #   DatabaseSelector, ResultTable
      pages/           #   QueryResultDemo
      store/           #   Zustand state
      types/query.ts   #   TypeScript types
  tests/               # 24 pytest tests across all modules
  5-1-data-preprocessing-deliverable/  # BIRD data prep pipeline
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

BIRD Mini-Dev dataset is preprocessed by the `5-1-data-preprocessing-deliverable` pipeline. To re-run:

```bash
cd 5-1-data-preprocessing-deliverable
python src/askdata/cli.py prepare-bird
```

This generates `data/bird/processed/` with `databases.json`, `questions.jsonl`, and per-DB schema files.

To convert for the eval runner (one-time):

```bash
python3 -c "
import json
# convert questions.jsonl → questions.json
# merge schema files into databases.json
"
```

The eval runner needs `databases.json` (with inline `tables` and `foreignKeys` arrays) and `questions.json` in `data/bird/processed/`.

## Testing

```bash
uv run python -m pytest tests/ -v    # 24 tests
```

## Key Design Decisions

- **PascalCase** for all NL2SQL-facing methods (`Build`, `Retrieve`, `Run`, `Compare`). Team `db/` modules use snake_case (`execute`, `validate`).
- **ReAct loop** with max 6 iterations; LLM self-corrects on SQL errors.
- **Column matching** in eval uses 4 tiers: strict (all columns match) → name (shared subset) → position (by index) → subset (try combinations).
- **Skills** are markdown files auto-loaded at agent start — no code changes needed to add patterns.
- **Business context** per database via `data/bird/instructions/<db>.md`.
