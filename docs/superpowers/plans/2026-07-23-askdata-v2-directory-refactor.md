# AskData V2 Directory Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move retrieval and analysis modules out of the overloaded `tools/` package, move prompt skill loading into `agent/`, and document the canonical V2 architecture without changing runtime behavior.

**Architecture:** This is a mechanical package-boundary refactor. Runtime retrieval moves to `askdata.retrieval`, result presentation helpers move to `askdata.analysis`, and prompt skill loading moves to `askdata.agent`. Existing public behavior, API contracts, SQL execution, RAG ranking, and frontend state flow stay unchanged.

**Tech Stack:** Python 3.10+, FastAPI, pytest, ripgrep, Git.

---

## File Structure

Create:

- `backend/askdata/retrieval/__init__.py`
- `backend/askdata/analysis/__init__.py`
- `docs/architecture-v2.md`

Move:

- `backend/askdata/tools/retriever.py` -> `backend/askdata/retrieval/retriever.py`
- `backend/askdata/tools/hybrid_retriever.py` -> `backend/askdata/retrieval/hybrid_retriever.py`
- `backend/askdata/tools/embedding_client.py` -> `backend/askdata/retrieval/embedding_client.py`
- `backend/askdata/tools/vector_store.py` -> `backend/askdata/retrieval/vector_store.py`
- `backend/askdata/tools/value_linker.py` -> `backend/askdata/retrieval/value_linker.py`
- `backend/askdata/tools/analyzer.py` -> `backend/askdata/analysis/result_analyzer.py`
- `backend/askdata/tools/chart_builder.py` -> `backend/askdata/analysis/chart_builder.py`
- `backend/askdata/tools/skill_loader.py` -> `backend/askdata/agent/skill_loader.py`

Delete:

- `backend/askdata/tools/__init__.py`
- `backend/askdata/tools/` directory if empty

Modify imports in:

- `backend/askdata/**/*.py`
- `tests/**/*.py`
- `data-processing/**/*.py`

## Task 1: Move Files and Create Packages

**Files:**
- Create: `backend/askdata/retrieval/__init__.py`
- Create: `backend/askdata/analysis/__init__.py`
- Move all files listed in File Structure.

- [ ] **Step 1: Confirm clean worktree**

Run:

```bash
git status -sb
```

Expected:

```text
## feature/askdata-v2...origin/feature/askdata-v2
```

- [ ] **Step 2: Move retrieval modules**

Run:

```bash
mkdir -p backend/askdata/retrieval
git mv backend/askdata/tools/retriever.py backend/askdata/retrieval/retriever.py
git mv backend/askdata/tools/hybrid_retriever.py backend/askdata/retrieval/hybrid_retriever.py
git mv backend/askdata/tools/embedding_client.py backend/askdata/retrieval/embedding_client.py
git mv backend/askdata/tools/vector_store.py backend/askdata/retrieval/vector_store.py
git mv backend/askdata/tools/value_linker.py backend/askdata/retrieval/value_linker.py
```

Expected:

```text
backend/askdata/retrieval/ contains retriever.py, hybrid_retriever.py, embedding_client.py, vector_store.py, value_linker.py
```

- [ ] **Step 3: Move analysis modules**

Run:

```bash
mkdir -p backend/askdata/analysis
git mv backend/askdata/tools/analyzer.py backend/askdata/analysis/result_analyzer.py
git mv backend/askdata/tools/chart_builder.py backend/askdata/analysis/chart_builder.py
```

Expected:

```text
backend/askdata/analysis/ contains result_analyzer.py and chart_builder.py
```

- [ ] **Step 4: Move skill loader into agent**

Run:

```bash
git mv backend/askdata/tools/skill_loader.py backend/askdata/agent/skill_loader.py
```

Expected:

```text
backend/askdata/agent/skill_loader.py exists
```

- [ ] **Step 5: Add package initializers and remove empty tools package**

Create `backend/askdata/retrieval/__init__.py` with:

```python
"""Runtime schema retrieval, vector retrieval, and value-linking components."""
```

Create `backend/askdata/analysis/__init__.py` with:

```python
"""Result analysis and chart recommendation components."""
```

If `backend/askdata/tools/__init__.py` is the only remaining file in `backend/askdata/tools/`, run:

```bash
git rm backend/askdata/tools/__init__.py
```

- [ ] **Step 6: Verify moved file list**

Run:

```bash
rg --files backend/askdata/retrieval backend/askdata/analysis backend/askdata/agent | sort
```

Expected includes:

```text
backend/askdata/agent/skill_loader.py
backend/askdata/analysis/__init__.py
backend/askdata/analysis/chart_builder.py
backend/askdata/analysis/result_analyzer.py
backend/askdata/retrieval/__init__.py
backend/askdata/retrieval/embedding_client.py
backend/askdata/retrieval/hybrid_retriever.py
backend/askdata/retrieval/retriever.py
backend/askdata/retrieval/value_linker.py
backend/askdata/retrieval/vector_store.py
```

## Task 2: Update Python Imports

**Files:**
- Modify: `backend/**/*.py`
- Modify: `tests/**/*.py`
- Modify: `data-processing/**/*.py`

- [ ] **Step 1: Replace retrieval imports**

Replace:

```python
askdata.tools.retriever
askdata.tools.hybrid_retriever
askdata.tools.embedding_client
askdata.tools.vector_store
askdata.tools.value_linker
```

with:

```python
askdata.retrieval.retriever
askdata.retrieval.hybrid_retriever
askdata.retrieval.embedding_client
askdata.retrieval.vector_store
askdata.retrieval.value_linker
```

Use:

```bash
rg -n "askdata\\.tools\\.(retriever|hybrid_retriever|embedding_client|vector_store|value_linker)" backend tests data-processing
```

Expected after replacement:

```text
no matches
```

- [ ] **Step 2: Replace analysis imports**

Replace:

```python
askdata.tools.analyzer
askdata.tools.chart_builder
```

with:

```python
askdata.analysis.result_analyzer
askdata.analysis.chart_builder
```

Use:

```bash
rg -n "askdata\\.tools\\.(analyzer|chart_builder)" backend tests data-processing
```

Expected after replacement:

```text
no matches
```

- [ ] **Step 3: Replace skill loader imports**

Replace:

```python
askdata.tools.skill_loader
```

with:

```python
askdata.agent.skill_loader
```

Use:

```bash
rg -n "askdata\\.tools\\.skill_loader" backend tests data-processing
```

Expected after replacement:

```text
no matches
```

- [ ] **Step 4: Replace moved-module relative imports**

Inside moved modules, update internal imports:

```python
from askdata.tools.embedding_client import EmbeddingClient
from askdata.tools.hybrid_retriever import HybridRetriever, HybridSchemaIndex
from askdata.tools.vector_store import MilvusVectorStore
from askdata.tools.value_linker import ValueLinker
from askdata.tools.analyzer import ResultAnalyzer
from askdata.tools.chart_builder import ChartBuilder
from askdata.tools.skill_loader import SkillLoader
```

to their new packages:

```python
from askdata.retrieval.embedding_client import EmbeddingClient
from askdata.retrieval.hybrid_retriever import HybridRetriever, HybridSchemaIndex
from askdata.retrieval.vector_store import MilvusVectorStore
from askdata.retrieval.value_linker import ValueLinker
from askdata.analysis.result_analyzer import ResultAnalyzer
from askdata.analysis.chart_builder import ChartBuilder
from askdata.agent.skill_loader import SkillLoader
```

- [ ] **Step 5: Verify no old tools imports remain**

Run:

```bash
rg -n "askdata\\.tools" backend tests data-processing
```

Expected:

```text
no matches
```

## Task 3: Add V2 Architecture Documentation

**Files:**
- Create or modify: `docs/architecture-v2.md`

- [ ] **Step 1: Create architecture document**

Create `docs/architecture-v2.md` with these sections:

```markdown
# AskData V2 Architecture

## Canonical V2 Query Path

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

## Runtime Package Boundaries

- `backend/askdata/agent/`: Text2SQL planning, ambiguity handling, prompt construction, ReAct generation, staged recovery, and SQL quality checks.
- `backend/askdata/retrieval/`: Runtime schema retrieval, hybrid vector retrieval, embedding client, vector store adapter, and value linking.
- `backend/askdata/analysis/`: Final answer explanation and chart recommendation.
- `backend/askdata/api/`: HTTP routes, request/response contracts, streaming service, and persistent session storage.
- `backend/askdata/db/query_runner.py`: V2 ReAct SQL execution path. It preserves model SQL and only caps returned previews.
- `backend/askdata/db/executor.py`: Generic SQLAlchemy executor path for team infrastructure. It is not the V2 ReAct loop executor.
- `data-processing/`: Offline BIRD data preparation and schema/vector index construction.

## Legacy and Compatibility Boundaries

- `backend/askdata/api/session_store.py` is the V2 persistent session store.
- `backend/askdata/api/session_manager.py` is legacy/in-memory compatibility.
- `backend/askdata/api/response_models.py` is the V2 discriminated query response contract.
- `backend/askdata/api/schemas.py` contains request schemas and remaining legacy compatibility models.
- `backend/askdata/db/executor.py` may coexist with `db/query_runner.py`, but the ReAct agent uses `query_runner.Execute()`.

## RAG Boundary

- Offline indexing belongs in `data-processing`.
- Runtime retrieval belongs in `backend/askdata/retrieval`.
- If embedding or Milvus is unavailable, runtime retrieval must fall back to lexical retrieval rather than blocking the query path.
```

- [ ] **Step 2: Check architecture document for stale paths**

Run:

```bash
rg -n "backend/askdata/tools|semantic_retriever" docs/architecture-v2.md
```

Expected:

```text
no matches
```

## Task 4: Verify Imports, Tests, and Whitespace

**Files:**
- No planned source modifications beyond fixes required by verification.

- [ ] **Step 1: Run syntax/import smoke check**

Run:

```bash
uv run python -c "import askdata; from askdata.agent.graph import AgentGraph; from askdata.retrieval.retriever import SemanticRetriever; from askdata.analysis.chart_builder import ChartBuilder; print('imports ok')"
```

Expected:

```text
imports ok
```

- [ ] **Step 2: Run backend tests with vector retrieval disabled**

Run:

```bash
VECTOR_RETRIEVAL_ENABLED=false UV_CACHE_DIR=/tmp/askdata-uv-cache uv run pytest -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 3: Verify old imports are gone**

Run:

```bash
rg -n "askdata\\.tools" backend tests data-processing
```

Expected:

```text
no matches
```

- [ ] **Step 4: Verify tools package is removed**

Run:

```bash
test ! -d backend/askdata/tools
```

Expected: command exits with status 0.

- [ ] **Step 5: Run diff whitespace check**

Run:

```bash
git diff --check
```

Expected: no output.

## Task 5: Commit Refactor

**Files:**
- All moved files
- `docs/architecture-v2.md`
- Updated imports

- [ ] **Step 1: Review status**

Run:

```bash
git status -sb
git diff --stat
```

Expected:

```text
file moves dominate the diff
docs/architecture-v2.md is added
```

- [ ] **Step 2: Commit**

Run:

```bash
git add backend tests data-processing docs/architecture-v2.md
git commit -m "refactor: clarify V2 package boundaries"
```

Expected:

```text
[feature/askdata-v2 <sha>] refactor: clarify V2 package boundaries
```

- [ ] **Step 3: Confirm clean worktree**

Run:

```bash
git status -sb
```

Expected:

```text
## feature/askdata-v2...origin/feature/askdata-v2 [ahead ...]
```

## Self-Review

- Spec coverage: the plan covers all approved spec requirements: file moves, `retriever.py` name preservation, `skill_loader.py` movement to `agent/`, removal of empty `tools/`, import updates across backend/tests/data-processing, architecture docs, and verification.
- Placeholder scan: no placeholder tasks remain; every command includes expected results.
- Type consistency: new import paths are consistent across tasks.
