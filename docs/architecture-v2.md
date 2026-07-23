# AskData V2 Architecture

## Canonical V2 Query Path

```text
Browser UI
  -> frontend/src/store/queryStore.ts
  -> frontend/src/api/queryStream.ts or frontend/src/api/query.ts
  -> POST /api/query or POST /api/query/stream
  -> backend/askdata/api/routes.py
  -> backend/askdata/api/query_service.py
  -> backend/askdata/agent/graph.py
  -> backend/askdata/retrieval/retriever.py
  -> backend/askdata/agent/question_analyzer.py
  -> backend/askdata/retrieval/value_linker.py
  -> backend/askdata/agent/ambiguity.py
  -> backend/askdata/agent/pipeline.py
  -> backend/askdata/agent/react_sql_agent.py
  -> backend/askdata/db/query_runner.py
  -> backend/askdata/agent/sql_quality.py
  -> backend/askdata/analysis/result_analyzer.py
  -> backend/askdata/analysis/chart_builder.py
  -> backend/askdata/api/response_models.py
```

## Runtime Package Boundaries

- `backend/askdata/agent/`: Text2SQL planning, ambiguity handling, prompt construction, ReAct generation, staged recovery, and SQL quality checks.
- `backend/askdata/retrieval/`: Runtime schema retrieval, hybrid vector retrieval, embedding client, vector store adapter, and value linking.
- `backend/askdata/analysis/`: Final answer explanation and chart recommendation.
- `backend/askdata/api/`: HTTP routes, request/response contracts, streaming service, and persistent session storage.
- `backend/askdata/db/query_runner.py`: V2 ReAct SQL execution path. It preserves model SQL and only caps returned previews.
- `backend/askdata/db/adapters/`: SQLite/MySQL/PostgreSQL execution adapters resolved internally by `query_runner.Execute()`.
- `backend/askdata/db/error_normalizer.py`: Converts database-specific errors into stable `error_code` values such as `unknown_table` and `unknown_column`.
- `backend/askdata/db/executor.py`: Generic SQLAlchemy executor path for team infrastructure. It is not the V2 ReAct loop executor.
- `data-processing/`: Offline BIRD data preparation and schema/vector index construction.

## Legacy and Compatibility Boundaries

- `backend/askdata/api/session_store.py` is the V2 persistent session store.
- `backend/askdata/api/session_manager.py` is legacy/in-memory compatibility.
- `backend/askdata/api/response_models.py` is the V2 discriminated query response contract.
- `backend/askdata/api/schemas.py` contains request schemas and remaining legacy compatibility models.
- `backend/askdata/db/executor.py` may coexist with `db/query_runner.py`, but the ReAct agent uses `query_runner.Execute()`.
- `query_runner.Execute(sql, database_path)` keeps its historical signature. For SQLite, `database_path` is a file path; for registered external databases, the same argument can be a `database_id` resolved by the adapter registry.

## RAG Boundary

- Offline indexing belongs in `data-processing`.
- Runtime retrieval belongs in `backend/askdata/retrieval`.
- If embedding or Milvus is unavailable, runtime retrieval must fall back to lexical retrieval rather than blocking the query path.
