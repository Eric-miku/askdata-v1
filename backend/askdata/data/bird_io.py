"""Read the native data-processing BIRD contract into one internal shape."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypedDict

from askdata.core.paths import PROJECT_ROOT, project_path


class BirdColumnRecord(TypedDict):
    column_name: str
    display_name: str
    data_type: str
    is_primary_key: bool
    description: str


class BirdTableRecord(TypedDict):
    table_name: str
    display_name: str
    columns: list[BirdColumnRecord]


class BirdForeignKeyRecord(TypedDict):
    source_table: str
    source_column: str
    target_table: str
    target_column: str


class BirdDatabaseRecord(TypedDict):
    database_id: str
    database_path: str
    tables: list[BirdTableRecord]
    foreign_keys: list[BirdForeignKeyRecord]
    schema_prompt: str


class BirdQuestionRecord(TypedDict):
    question_id: str
    database_id: str
    question: str
    gold_sql: str
    evidence: str
    difficulty: str


def ResolveProcessedDir(processed_dir: str | Path | None) -> Path:
    base = project_path(processed_dir or "data/bird").resolve()
    if (base / "databases.json").is_file():
        return base
    nested = base / "processed"
    if (nested / "databases.json").is_file():
        return nested.resolve()
    raise FileNotFoundError(f"Missing BIRD processed databases file under: {base}")


def LoadProcessedDatabases(processed_dir: str | Path | None = None) -> list[BirdDatabaseRecord]:
    processed = ResolveProcessedDir(processed_dir)
    metadata = _ReadJsonArray(processed / "databases.json")
    databases: list[BirdDatabaseRecord] = []
    seen_ids: set[str] = set()
    for item in metadata:
        database_id = str(_Get(item, "database_id", "databaseId", "db_id", default="")).strip()
        if not database_id:
            raise ValueError("BIRD database record is missing database_id")
        if database_id in seen_ids:
            raise ValueError(f"Duplicate database_id: {database_id}")
        seen_ids.add(database_id)

        schema = _LoadSchema(item, processed, database_id)
        declared_db_path = _Get(
            schema,
            "database_path",
            "databasePath",
            "db_path",
            default=_Get(item, "database_path", "databasePath", "db_path", default=""),
        )
        database_path = _ResolveDeclaredPath(declared_db_path, processed)
        if not database_path.is_file():
            raise FileNotFoundError(f"SQLite database for {database_id} does not exist: {database_path}")

        schema_prompt = _LoadSchemaPrompt(item, processed, database_id)
        databases.append({
            "database_id": database_id,
            "database_path": str(database_path),
            "tables": [_NormalizeTable(table) for table in _Get(schema, "tables", default=[])],
            "foreign_keys": [
                _NormalizeForeignKey(key)
                for key in _Get(schema, "foreign_keys", "foreignKeys", default=[])
            ],
            "schema_prompt": schema_prompt,
        })
    return databases


def LoadProcessedQuestions(
    processed_dir: str | Path | None = None,
    database_ids: set[str] | None = None,
) -> list[BirdQuestionRecord]:
    processed = ResolveProcessedDir(processed_dir)
    jsonl_path = processed / "questions.jsonl"
    if jsonl_path.is_file():
        raw_questions = _ReadJsonLines(jsonl_path)
    else:
        raw_questions = _ReadJsonArray(processed / "questions.json")

    questions: list[BirdQuestionRecord] = []
    seen_ids: set[str] = set()
    for item in raw_questions:
        question_id = str(_Get(item, "question_id", "questionId", default="")).strip()
        database_id = str(_Get(item, "database_id", "databaseId", "db_id", default="")).strip()
        if not question_id:
            raise ValueError("BIRD question is missing question_id")
        if question_id in seen_ids:
            raise ValueError(f"Duplicate question_id: {question_id}")
        seen_ids.add(question_id)
        if database_ids is not None and database_id not in database_ids:
            raise ValueError(f"Unknown database_id for question {question_id}: {database_id}")
        questions.append({
            "question_id": question_id,
            "database_id": database_id,
            "question": str(_Get(item, "question", default="")),
            "gold_sql": str(_Get(item, "gold_sql", "goldSql", "SQL", default="")),
            "evidence": str(_Get(item, "evidence", default="") or ""),
            "difficulty": str(_Get(item, "difficulty", default="unknown") or "unknown"),
        })
    return questions


def LoadQuestionManifest(path: str | Path) -> list[str]:
    manifest_path = project_path(path).resolve()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    values = data.get("question_ids") if isinstance(data, dict) else data
    if not isinstance(values, list) or not all(isinstance(value, (str, int)) for value in values):
        raise ValueError(f"Question manifest must be a JSON array or question_ids object: {manifest_path}")
    question_ids = [str(value) for value in values]
    if len(question_ids) != len(set(question_ids)):
        raise ValueError(f"Question manifest contains duplicate IDs: {manifest_path}")
    return question_ids


def _LoadSchema(item: dict[str, Any], processed: Path, database_id: str) -> dict[str, Any]:
    declared = _Get(item, "schema_path", "schemaPath", default="")
    candidates = []
    if declared:
        candidates.append(_ResolveDeclaredPath(declared, processed))
    candidates.append(processed / "schemas" / f"{database_id}.json")
    for candidate in candidates:
        if candidate.is_file():
            data = json.loads(candidate.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError(f"BIRD schema must be an object: {candidate}")
            return data
    if isinstance(item.get("tables"), list):
        return item
    raise FileNotFoundError(f"Missing structured schema for {database_id}")


def _LoadSchemaPrompt(item: dict[str, Any], processed: Path, database_id: str) -> str:
    declared = _Get(item, "schema_prompt_path", "schemaPromptPath", default="")
    candidates = []
    if declared:
        candidates.append(_ResolveDeclaredPath(declared, processed))
    candidates.append(processed / "schema_prompts" / f"{database_id}.md")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return ""


def _NormalizeTable(item: dict[str, Any]) -> BirdTableRecord:
    table_name = str(_Get(item, "table_name", "tableName", default=""))
    return {
        "table_name": table_name,
        "display_name": str(_Get(item, "display_name", "displayName", default=table_name)),
        "columns": [_NormalizeColumn(column) for column in _Get(item, "columns", default=[])],
    }


def _NormalizeColumn(item: dict[str, Any]) -> BirdColumnRecord:
    column_name = str(_Get(item, "column_name", "columnName", default=""))
    return {
        "column_name": column_name,
        "display_name": str(_Get(item, "display_name", "displayName", default=column_name)),
        "data_type": str(_Get(item, "data_type", "column_type", "columnType", default="text")),
        "is_primary_key": bool(_Get(item, "is_primary_key", "isPrimary", "is_primary", default=False)),
        "description": str(_Get(item, "description", default="") or ""),
    }


def _NormalizeForeignKey(item: dict[str, Any]) -> BirdForeignKeyRecord:
    return {
        "source_table": str(_Get(item, "source_table", "left_table", "leftTable", default="")),
        "source_column": str(_Get(item, "source_column", "left_column", "leftColumn", default="")),
        "target_table": str(_Get(item, "target_table", "right_table", "rightTable", default="")),
        "target_column": str(_Get(item, "target_column", "right_column", "rightColumn", default="")),
    }


def _ResolveDeclaredPath(value: str | Path, processed: Path) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate.resolve()
    project_candidate = (PROJECT_ROOT / candidate).resolve()
    if project_candidate.exists():
        return project_candidate
    processed_candidate = (processed / candidate).resolve()
    if processed_candidate.exists():
        return processed_candidate
    return project_candidate


def _ReadJsonArray(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"Missing BIRD processed file: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ValueError(f"BIRD processed file must be a JSON array: {path}")
    return data


def _ReadJsonLines(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc.msg}") from exc
        if not isinstance(item, dict):
            raise ValueError(f"JSONL row must be an object at {path}:{line_number}")
        rows.append(item)
    return rows


def _Get(item: Any, *names: str, default=None):
    for name in names:
        if isinstance(item, dict) and name in item:
            return item[name]
    return default
