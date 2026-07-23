# Multi-Database Adapter Design

Date: 2026-07-23

## Goal

Add MySQL and PostgreSQL execution support without changing the V2 Text2SQL agent, pipeline, or `query_runner.Execute(sql, database_path)` public signature.

## Core Decision

Keep this existing contract:

```python
def Execute(sql: str, database_path: str) -> dict:
    ...
```

The adapter system is resolved internally by `query_runner.py`. Existing ReAct agent code, staged pipeline code, and current tests should keep working.

## Package Structure

```text
backend/askdata/db/
  query_runner.py
  validator.py
  error_normalizer.py
  adapters/
    __init__.py
    base.py
    sqlite.py
    mysql.py
    postgresql.py
    registry.py
```

## Adapter Interface

Use an abstract base class rather than a `Protocol`:

```python
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

ABC enforcement happens when an incomplete subclass is instantiated. That is stricter than structural typing and is appropriate for runtime database adapters.

## Registry

Use `database_id` as the registry key:

```python
_REGISTRY: dict[str, DatabaseAdapter] = {}


def Register(database_id: str, adapter: DatabaseAdapter) -> None:
    _REGISTRY[database_id] = adapter


def Resolve(database_id_or_path: str) -> DatabaseAdapter:
    if database_id_or_path in _REGISTRY:
        return _REGISTRY[database_id_or_path]
    return SQLiteAdapter(database_id_or_path)
```

This preserves the current SQLite path behavior while enabling registered MySQL/PostgreSQL databases.

## Configuration Loading

Add optional startup/config loading from:

```text
data/database_connections.json
```

Example:

```json
{
  "sales_mysql": {
    "dialect": "mysql",
    "url": "mysql+pymysql://user:pass@host:3306/db"
  },
  "analytics_pg": {
    "dialect": "postgresql",
    "url": "postgresql+psycopg://user:pass@host:5432/db"
  }
}
```

The file should be optional. If absent, SQLite behavior is unchanged.

## Error Normalization

Adapters must normalize backend-specific errors into stable error codes:

```python
{
  "success": False,
  "sql": normalized_sql,
  "error": "unknown_table: schools",
  "error_code": "unknown_table"
}
```

Minimum normalized codes:

- `unknown_table`
- `unknown_column`
- `ambiguous_column`
- `syntax_error`
- `timeout`
- `database_error`

Examples:

```text
SQLite:      no such table: schools
MySQL:       1146 Table doesn't exist
PostgreSQL: 42P01 relation does not exist

Normalized:
unknown_table: schools
```

`StagedSqlPipeline._ClassifyExecution()` should prefer `error_code` when available and keep the current string fallback for compatibility.

## Implementation Order

1. Extract current SQLite `query_runner.Execute()` behavior into `SQLiteAdapter`.
2. Add registry with SQLite fallback.
3. Route `query_runner.Execute()` through `Resolve(database_path).Execute(sql)`.
4. Add `error_normalizer.py`.
5. Add MySQL and PostgreSQL adapters using SQLAlchemy engines.
6. Add optional JSON connection loader.
7. Add tests for:
   - SQLite behavior preserved.
   - Registry resolves registered adapters by database_id.
   - Unknown path falls back to SQLite.
   - MySQL/PostgreSQL adapters validate and call SQLAlchemy execution using injected fake engines.
   - Error normalization produces stable `error_code`.
   - Pipeline classifies `error_code` before string matching.

## Non-Goals

- Do not change frontend database selection in this pass.
- Do not rewrite `SemanticRetriever` to introspect live MySQL/PostgreSQL schemas in this pass.
- Do not remove `db/executor.py`.
- Do not change `query_runner.Execute()` signature.
- Do not require MySQL/PostgreSQL services in tests.

## Acceptance Criteria

- Existing SQLite query runner tests pass.
- Full backend test suite passes with vector retrieval disabled.
- New adapter tests pass without external DB services.
- `query_runner.Execute()` signature remains unchanged.
- `StagedSqlPipeline` continues to work without direct dialect awareness.
