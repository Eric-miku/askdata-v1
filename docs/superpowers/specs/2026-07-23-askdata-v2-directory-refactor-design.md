# AskData V2 Directory Refactor Design

Date: 2026-07-23

## Goal

Make the V2 backend easier to navigate by separating retrieval, analysis, and execution responsibilities. This is a structural cleanup only: behavior should stay the same, tests should continue to pass, and the V2 Text2SQL path should remain stable after the recent main merge.

## Current Problem

`backend/askdata/tools/` has become a catch-all directory:

- `retriever.py`, `hybrid_retriever.py`, `embedding_client.py`, `vector_store.py`, and `value_linker.py` are retrieval/RAG components.
- `analyzer.py` and `chart_builder.py` are answer/result presentation components.
- `skill_loader.py` is prompt skill loading.

This makes it harder to know which files are part of the core Text2SQL runtime and which files are support utilities. The project also has two SQL execution paths:

- `db/query_runner.py`: V2 ReAct Text2SQL execution path. Preserves model SQL and caps result preview.
- `db/executor.py`: team generic SQLAlchemy executor. Used by non-ReAct/team routes and infrastructure.

The refactor should make these boundaries explicit without changing runtime semantics.

## Scope

Implement a moderate, mechanical directory refactor:

```text
backend/askdata/
  retrieval/
    __init__.py
    semantic_retriever.py      # from tools/retriever.py
    hybrid_retriever.py        # from tools/hybrid_retriever.py
    embedding_client.py        # from tools/embedding_client.py
    vector_store.py            # from tools/vector_store.py
    value_linker.py            # from tools/value_linker.py

  analysis/
    __init__.py
    result_analyzer.py         # from tools/analyzer.py
    chart_builder.py           # from tools/chart_builder.py

  tools/
    __init__.py
    skill_loader.py            # remains here
```

`backend/askdata/tools/skill_loader.py` stays in `tools/` because it is a prompt utility rather than retrieval or result analysis.

## Explicit Non-Goals

- Do not rewrite the SQL agent architecture.
- Do not change RAG ranking behavior.
- Do not change ambiguity behavior.
- Do not change frontend state flow.
- Do not remove `db/executor.py` or `api/session_manager.py` yet; mark boundaries in docs/comments instead.
- Do not introduce compatibility shims unless tests or imports require them.

## Import Migration

Update imports from:

```python
askdata.tools.retriever
askdata.tools.hybrid_retriever
askdata.tools.embedding_client
askdata.tools.vector_store
askdata.tools.value_linker
askdata.tools.analyzer
askdata.tools.chart_builder
```

to:

```python
askdata.retrieval.semantic_retriever
askdata.retrieval.hybrid_retriever
askdata.retrieval.embedding_client
askdata.retrieval.vector_store
askdata.retrieval.value_linker
askdata.analysis.result_analyzer
askdata.analysis.chart_builder
```

Test imports should be updated to the new locations.

## Architecture Documentation

Add or update `docs/architecture-v2.md` with the canonical V2 flow:

```text
Frontend queryStore/queryStream
  -> API routes
  -> QueryService
  -> AgentGraph
  -> SemanticRetriever / HybridRetriever
  -> QuestionAnalyzer / ValueLinker / AmbiguityGate
  -> StagedSqlPipeline
  -> ReActSqlAgent
  -> query_runner.Execute
  -> SQLQuality / CandidateLedger
  -> response_models
```

The document should also state:

- `db/query_runner.py` is the V2 ReAct execution path.
- `db/executor.py` is the generic SQLAlchemy executor path.
- `api/response_models.py` is the V2 query response contract.
- `api/schemas.py` contains HTTP request and legacy compatibility schemas.
- `session_store.py` is persistent V2 session storage.
- `session_manager.py` is legacy/in-memory compatibility.
- `data-processing` owns offline schema/chunk/index construction.
- `backend/askdata/retrieval` owns runtime retrieval.

## Risk Controls

This refactor must be mechanical and verified in small steps:

1. Move files.
2. Update imports with `rg`.
3. Run Python import/syntax checks.
4. Run backend tests.
5. Run frontend tests if import/type paths changed on frontend-facing contracts.
6. Run `git diff --check`.

If tests show behavior regressions, revert the specific import/move and keep the architecture doc. Do not mix behavior fixes into the refactor unless required to restore existing behavior.

## Acceptance Criteria

- No unresolved old imports to moved modules remain.
- `uv run pytest -q` passes, or any failure is documented as unrelated to this refactor.
- `git diff --check` passes.
- `docs/architecture-v2.md` clearly identifies the canonical V2 path and legacy boundaries.
- The final commit separates structural refactor from any later behavior changes.
