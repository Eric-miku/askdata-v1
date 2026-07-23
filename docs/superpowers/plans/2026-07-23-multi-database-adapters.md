# Multi-Database Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add SQLite/MySQL/PostgreSQL database adapter infrastructure while preserving `query_runner.Execute(sql, database_path)` and existing V2 agent behavior.

**Architecture:** `query_runner.Execute()` remains the only execution entry used by the ReAct pipeline. It resolves a `DatabaseAdapter` internally through a registry keyed by `database_id` with SQLite path fallback. Each adapter validates SQL, executes a bounded preview, introspects schema, and normalizes database-specific errors into stable `error_code` values.

**Tech Stack:** Python 3.10+, SQLAlchemy, sqlglot, pytest, FastAPI project conventions.

---

## File Structure

Create:

- `backend/askdata/db/error_normalizer.py`
- `backend/askdata/db/adapters/__init__.py`
- `backend/askdata/db/adapters/base.py`
- `backend/askdata/db/adapters/sqlite.py`
- `backend/askdata/db/adapters/mysql.py`
- `backend/askdata/db/adapters/postgresql.py`
- `backend/askdata/db/adapters/registry.py`
- `tests/test_db_adapters.py`

Modify:

- `backend/askdata/db/query_runner.py`
- `backend/askdata/db/__init__.py`
- `backend/askdata/agent/pipeline.py`
- `backend/askdata/core/config.py`
- `docs/architecture-v2.md`

## Task 1: Error Normalization

**Files:**
- Create: `backend/askdata/db/error_normalizer.py`
- Test: `tests/test_db_adapters.py`

- [ ] **Step 1: Add tests for normalized database errors**

Create `tests/test_db_adapters.py` with:

```python
from askdata.db.error_normalizer import NormalizeDatabaseError


def test_normalizes_sqlite_unknown_table():
    error = NormalizeDatabaseError("sqlite", "no such table: schools")
    assert error.code == "unknown_table"
    assert error.message == "unknown_table: schools"


def test_normalizes_mysql_unknown_table():
    error = NormalizeDatabaseError(
        "mysql", "(1146, \"Table 'demo.schools' doesn't exist\")"
    )
    assert error.code == "unknown_table"
    assert error.message == "unknown_table: schools"


def test_normalizes_postgres_unknown_table():
    error = NormalizeDatabaseError(
        "postgresql", "psycopg.errors.UndefinedTable: relation \"schools\" does not exist"
    )
    assert error.code == "unknown_table"
    assert error.message == "unknown_table: schools"


def test_normalizes_unknown_column_and_syntax_error():
    column = NormalizeDatabaseError("sqlite", "no such column: schools.name")
    syntax = NormalizeDatabaseError("postgresql", "syntax error at or near \"FROM\"")
    assert column.code == "unknown_column"
    assert column.message == "unknown_column: schools.name"
    assert syntax.code == "syntax_error"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
VECTOR_RETRIEVAL_ENABLED=false UV_CACHE_DIR=/tmp/askdata-uv-cache uv run pytest tests/test_db_adapters.py -q
```

Expected: fail because `askdata.db.error_normalizer` does not exist.

- [ ] **Step 3: Implement error normalizer**

Create `backend/askdata/db/error_normalizer.py` with:

```python
"""Normalize backend-specific database errors into stable agent-facing codes."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class NormalizedDatabaseError:
    code: str
    message: str


def _clean_identifier(value: str) -> str:
    value = value.strip().strip("\"'`")
    if "." in value:
        value = value.rsplit(".", 1)[-1]
    return value.strip().strip("\"'`")


def NormalizeDatabaseError(dialect: str, error: object) -> NormalizedDatabaseError:
    text = str(error or "")
    lowered = text.casefold()

    table = _match_first(
        text,
        [
            r"no such table:\s*([^\s,)]+)",
            r"table ['\"]?([^'\"]+)['\"]? doesn't exist",
            r"relation ['\"]?([^'\"]+)['\"]? does not exist",
        ],
    )
    if table:
        name = _clean_identifier(table)
        return NormalizedDatabaseError("unknown_table", f"unknown_table: {name}")

    column = _match_first(
        text,
        [
            r"no such column:\s*([^\s,)]+)",
            r"unknown column ['\"]?([^'\"]+)['\"]?",
            r"column ['\"]?([^'\"]+)['\"]? does not exist",
        ],
    )
    if column:
        name = _clean_identifier(column)
        return NormalizedDatabaseError("unknown_column", f"unknown_column: {name}")

    if "ambiguous column" in lowered or "column reference" in lowered and "ambiguous" in lowered:
        return NormalizedDatabaseError("ambiguous_column", "ambiguous_column")
    if "syntax error" in lowered or "you have an error in your sql syntax" in lowered:
        return NormalizedDatabaseError("syntax_error", "syntax_error")
    if "timeout" in lowered or "timed out" in lowered or "statement timeout" in lowered:
        return NormalizedDatabaseError("timeout", "timeout")
    return NormalizedDatabaseError("database_error", f"database_error: {text}")


def _match_first(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
VECTOR_RETRIEVAL_ENABLED=false UV_CACHE_DIR=/tmp/askdata-uv-cache uv run pytest tests/test_db_adapters.py -q
```

Expected: error normalization tests pass.

## Task 2: Adapter ABC, SQLite Adapter, and Registry

**Files:**
- Create: `backend/askdata/db/adapters/base.py`
- Create: `backend/askdata/db/adapters/sqlite.py`
- Create: `backend/askdata/db/adapters/registry.py`
- Create: `backend/askdata/db/adapters/__init__.py`
- Modify: `backend/askdata/db/query_runner.py`
- Modify: `backend/askdata/db/__init__.py`
- Test: `tests/test_db_adapters.py`

- [ ] **Step 1: Add adapter behavior tests**

Append to `tests/test_db_adapters.py`:

```python
import sqlite3

from askdata.db.adapters.base import DatabaseAdapter
from askdata.db.adapters.registry import ClearRegistryForTests, Register, Resolve
from askdata.db.adapters.sqlite import SQLiteAdapter
from askdata.db.query_runner import Execute
from askdata.db.validator import SQLValidator


def test_database_adapter_abc_rejects_incomplete_subclass():
    class Incomplete(DatabaseAdapter):
        dialect = "broken"

    try:
        Incomplete()
    except TypeError as exc:
        assert "abstract" in str(exc).casefold()
    else:
        raise AssertionError("Incomplete DatabaseAdapter subclass should not instantiate")


def test_sqlite_adapter_preserves_query_runner_behavior(tmp_path):
    db_path = tmp_path / "demo.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE items(id INTEGER, name TEXT)")
        connection.execute("INSERT INTO items VALUES (1, 'a')")

    result = SQLiteAdapter(str(db_path)).Execute("SELECT id, name FROM items")

    assert result["success"] is True
    assert result["columns"] == ["id", "name"]
    assert result["rows"] == [{"id": 1, "name": "a"}]


def test_query_runner_falls_back_to_sqlite_path(tmp_path):
    db_path = tmp_path / "demo.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE items(id INTEGER)")
        connection.execute("INSERT INTO items VALUES (1)")

    result = Execute("SELECT id FROM items", str(db_path))

    assert result["success"] is True
    assert result["rows"] == [{"id": 1}]


def test_registry_resolves_registered_database_id():
    class FakeAdapter(DatabaseAdapter):
        dialect = "sqlite"

        def Validate(self, sql):
            return SQLValidator(dialect="sqlite").validate(sql)

        def Execute(self, sql, *, preview_limit=100):
            return {"success": True, "sql": sql, "columns": ["ok"], "rows": [{"ok": 1}]}

        def IntrospectSchema(self):
            return {"tables": []}

    ClearRegistryForTests()
    Register("registered_demo", FakeAdapter())

    result = Execute("SELECT 1 AS ok", "registered_demo")

    assert result["success"] is True
    assert result["rows"] == [{"ok": 1}]
    ClearRegistryForTests()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
VECTOR_RETRIEVAL_ENABLED=false UV_CACHE_DIR=/tmp/askdata-uv-cache uv run pytest tests/test_db_adapters.py tests/test_query_runner.py -q
```

Expected: fail because adapter modules do not exist.

- [ ] **Step 3: Implement base adapter**

Create `backend/askdata/db/adapters/base.py`:

```python
"""Database adapter interface for Text2SQL execution backends."""

from __future__ import annotations

from abc import ABC, abstractmethod

from askdata.db.validator import ValidationResult


class DatabaseAdapter(ABC):
    dialect: str

    @abstractmethod
    def Validate(self, sql: str) -> ValidationResult:
        ...

    @abstractmethod
    def Execute(self, sql: str, *, preview_limit: int = 100) -> dict:
        ...

    @abstractmethod
    def IntrospectSchema(self) -> dict:
        ...
```

- [ ] **Step 4: Implement SQLite adapter**

Create `backend/askdata/db/adapters/sqlite.py` by moving the current lightweight execution logic from `query_runner.py` into `SQLiteAdapter.Execute()`. It must:

- use `SQLValidator(dialect="sqlite")`;
- preserve explicit model SQL;
- allow only SELECT/WITH/UNION;
- open SQLite read-only with `mode=ro`;
- fetch `preview_limit + 1`;
- return `truncated`;
- include `error_code` on failures using `NormalizeDatabaseError()`.

- [ ] **Step 5: Implement registry**

Create `backend/askdata/db/adapters/registry.py`:

```python
"""Database adapter registry keyed by database_id."""

from __future__ import annotations

from askdata.db.adapters.base import DatabaseAdapter
from askdata.db.adapters.sqlite import SQLiteAdapter


_REGISTRY: dict[str, DatabaseAdapter] = {}


def Register(database_id: str, adapter: DatabaseAdapter) -> None:
    key = (database_id or "").strip()
    if not key:
        raise ValueError("database_id must not be blank")
    _REGISTRY[key] = adapter


def Resolve(database_id_or_path: str) -> DatabaseAdapter:
    key = (database_id_or_path or "").strip()
    if key in _REGISTRY:
        return _REGISTRY[key]
    return SQLiteAdapter(key)


def ClearRegistryForTests() -> None:
    _REGISTRY.clear()
```

- [ ] **Step 6: Update query runner**

Replace `query_runner.Execute()` implementation with:

```python
def Execute(sql: str, database_path: str) -> dict:
    return Resolve(database_path).Execute(sql)
```

Keep existing `detect_file_encoding()` and `build_sqlite_engine()` functions for `SQLExecutor`.

- [ ] **Step 7: Export adapters**

Create `backend/askdata/db/adapters/__init__.py`:

```python
"""Database adapter implementations and registry."""

from askdata.db.adapters.base import DatabaseAdapter
from askdata.db.adapters.registry import Register, Resolve
from askdata.db.adapters.sqlite import SQLiteAdapter

__all__ = ["DatabaseAdapter", "Register", "Resolve", "SQLiteAdapter"]
```

Update `backend/askdata/db/__init__.py` to export `DatabaseAdapter`, `RegisterDatabaseAdapter`, and `ResolveDatabaseAdapter`.

- [ ] **Step 8: Run adapter and query runner tests**

Run:

```bash
VECTOR_RETRIEVAL_ENABLED=false UV_CACHE_DIR=/tmp/askdata-uv-cache uv run pytest tests/test_db_adapters.py tests/test_query_runner.py -q
```

Expected: tests pass.

## Task 3: MySQL/PostgreSQL Adapters and Config Loader

**Files:**
- Create: `backend/askdata/db/adapters/mysql.py`
- Create: `backend/askdata/db/adapters/postgresql.py`
- Modify: `backend/askdata/db/adapters/registry.py`
- Modify: `backend/askdata/core/config.py`
- Test: `tests/test_db_adapters.py`

- [ ] **Step 1: Add fake-engine tests**

Append tests that inject fake engines into `MySQLAdapter` and `PostgreSQLAdapter`, returning fake rows through a fake SQLAlchemy result object. Verify:

- SQL is validated with the adapter dialect;
- `Execute()` returns columns/rows/truncated;
- execution errors are normalized.

- [ ] **Step 2: Implement SQLAlchemy adapters**

Implement `MySQLAdapter` and `PostgreSQLAdapter` with constructor:

```python
def __init__(self, url: str = "", *, engine=None):
    ...
```

Use injected `engine` in tests. If `engine` is absent, create one with SQLAlchemy `create_engine(url, pool_pre_ping=True)`.

Execution pattern:

```python
with self.engine.connect() as connection:
    result = connection.execute(text(normalized))
    columns = list(result.keys())
    preview = result.fetchmany(preview_limit + 1)
```

Convert rows using `dict(row._mapping)` when available.

- [ ] **Step 3: Add optional JSON loader**

Add to `registry.py`:

```python
def LoadFromJson(path: str | Path) -> int:
    ...
```

It reads optional JSON, registers MySQL/PostgreSQL adapters by database_id, returns count, and raises `ValueError` for unsupported dialect.

Add `DATABASE_CONNECTIONS_PATH: str = "data/database_connections.json"` to settings.

- [ ] **Step 4: Run adapter tests**

Run:

```bash
VECTOR_RETRIEVAL_ENABLED=false UV_CACHE_DIR=/tmp/askdata-uv-cache uv run pytest tests/test_db_adapters.py -q
```

Expected: pass without real MySQL/PostgreSQL services.

## Task 4: Pipeline Error-Code Classification

**Files:**
- Modify: `backend/askdata/agent/pipeline.py`
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Add classification test**

Add a test where runner returns:

```python
{"success": False, "error": "whatever text", "error_code": "unknown_table"}
```

Expected ledger/failure class is `schema_grounding`.

- [ ] **Step 2: Update `_ClassifyExecution()`**

Change signature to accept either execution dict or error/code pair, then prefer `error_code`:

```python
if error_code in {"unknown_table", "unknown_column", "ambiguous_column"}:
    return "schema_grounding"
if error_code in {"syntax_error", "timeout", "database_error"}:
    return "syntax_or_safety"
```

Keep current string fallback.

- [ ] **Step 3: Run pipeline tests**

Run:

```bash
VECTOR_RETRIEVAL_ENABLED=false UV_CACHE_DIR=/tmp/askdata-uv-cache uv run pytest tests/test_pipeline.py -q
```

Expected: pass.

## Task 5: Docs, Full Verification, Commit

**Files:**
- Modify: `docs/architecture-v2.md`

- [ ] **Step 1: Update architecture doc**

Add `db/adapters/` and `error_normalizer.py` to execution boundary notes.

- [ ] **Step 2: Run import smoke check**

Run:

```bash
uv run python -c "from askdata.db.adapters import SQLiteAdapter; from askdata.db.adapters.mysql import MySQLAdapter; from askdata.db.adapters.postgresql import PostgreSQLAdapter; from askdata.db.query_runner import Execute; print('adapter imports ok')"
```

Expected:

```text
adapter imports ok
```

- [ ] **Step 3: Run full backend suite**

Run:

```bash
VECTOR_RETRIEVAL_ENABLED=false UV_CACHE_DIR=/tmp/askdata-uv-cache uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Run diff check**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 5: Commit**

Run:

```bash
git add backend tests docs/architecture-v2.md docs/superpowers/plans/2026-07-23-multi-database-adapters.md
git commit -m "feat: add multi-database adapters"
```

Expected: commit created.

## Self-Review

- Spec coverage: adapter ABC, registry keyed by database_id, unchanged `query_runner.Execute()` signature, error normalization, MySQL/PostgreSQL adapters, JSON loading, and pipeline classification are covered.
- Placeholder scan: the plan intentionally leaves implementation details concise for fake engine tests and adapter code, but all required behavior and commands are explicit.
- Type consistency: adapter methods use `Validate`, `Execute`, and `IntrospectSchema` consistently.
