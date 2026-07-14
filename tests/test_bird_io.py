from pathlib import Path
import json
import sqlite3
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.data.bird_io import (  # noqa: E402
    LoadProcessedDatabases,
    LoadProcessedQuestions,
    ResolveProcessedDir,
)
import askdata.data.bird_io as bird_io  # noqa: E402


def _write_sqlite(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT)")


def test_loads_native_metadata_schema_and_jsonl(tmp_path):
    processed = tmp_path / "data" / "bird" / "processed"
    schema_dir = processed / "schemas"
    schema_dir.mkdir(parents=True)
    database_path = tmp_path / "data" / "bird" / "databases" / "demo" / "demo.sqlite"
    _write_sqlite(database_path)

    schema = {
        "database_id": "demo",
        "database_path": str(database_path),
        "tables": [{
            "table_name": "items",
            "display_name": "inventory items",
            "columns": [{
                "column_name": "id",
                "display_name": "item identifier",
                "data_type": "integer",
                "is_primary_key": True,
                "description": "stable item id",
            }],
        }],
        "foreign_keys": [],
    }
    (schema_dir / "demo.json").write_text(json.dumps(schema), encoding="utf-8")
    (processed / "databases.json").write_text(json.dumps([{
        "database_id": "demo",
        "database_path": str(database_path),
        "schema_path": str(schema_dir / "demo.json"),
    }]), encoding="utf-8")
    (processed / "questions.jsonl").write_text(json.dumps({
        "question_id": "bird_0001",
        "database_id": "demo",
        "question": "List inventory items",
        "gold_sql": "SELECT id FROM items",
        "evidence": "inventory item means an items row",
        "difficulty": "simple",
    }) + "\n", encoding="utf-8")

    databases = LoadProcessedDatabases(processed)
    questions = LoadProcessedQuestions(processed, database_ids={"demo"})

    assert databases[0]["database_id"] == "demo"
    assert databases[0]["database_path"] == str(database_path.resolve())
    assert databases[0]["tables"][0]["display_name"] == "inventory items"
    assert databases[0]["tables"][0]["columns"][0]["is_primary_key"] is True
    assert questions == [{
        "question_id": "bird_0001",
        "database_id": "demo",
        "question": "List inventory items",
        "gold_sql": "SELECT id FROM items",
        "evidence": "inventory item means an items row",
        "difficulty": "simple",
    }]


def test_loads_legacy_inline_schema_and_questions_json(tmp_path):
    processed = tmp_path / "processed"
    processed.mkdir()
    database_path = tmp_path / "demo.sqlite"
    _write_sqlite(database_path)
    (processed / "databases.json").write_text(json.dumps([{
        "databaseId": "demo",
        "databasePath": str(database_path),
        "tables": [{
            "tableName": "items",
            "columns": [{"columnName": "id", "columnType": "integer", "isPrimary": True}],
        }],
        "foreignKeys": [],
    }]), encoding="utf-8")
    (processed / "questions.json").write_text(json.dumps([{
        "questionId": "q1",
        "databaseId": "demo",
        "question": "How many items?",
        "goldSql": "SELECT COUNT(id) FROM items",
    }]), encoding="utf-8")

    database = LoadProcessedDatabases(processed)[0]
    question = LoadProcessedQuestions(processed, database_ids={"demo"})[0]

    assert database["tables"][0]["table_name"] == "items"
    assert database["tables"][0]["columns"][0]["is_primary_key"] is True
    assert question["question_id"] == "q1"
    assert question["gold_sql"] == "SELECT COUNT(id) FROM items"


def test_questions_reject_duplicate_ids_and_unknown_databases(tmp_path):
    processed = tmp_path / "processed"
    processed.mkdir()
    (processed / "databases.json").write_text("[]", encoding="utf-8")
    rows = [
        {"question_id": "q1", "database_id": "missing", "question": "One", "gold_sql": "SELECT 1"},
        {"question_id": "q1", "database_id": "missing", "question": "Two", "gold_sql": "SELECT 2"},
    ]
    (processed / "questions.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate question_id"):
        LoadProcessedQuestions(processed)

    (processed / "questions.jsonl").write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown database_id"):
        LoadProcessedQuestions(processed, database_ids={"demo"})


def test_database_loader_rejects_missing_sqlite(tmp_path):
    processed = tmp_path / "processed"
    processed.mkdir()
    (processed / "databases.json").write_text(json.dumps([{
        "database_id": "demo",
        "database_path": str(tmp_path / "missing.sqlite"),
        "tables": [],
        "foreign_keys": [],
    }]), encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="SQLite database for demo"):
        LoadProcessedDatabases(processed)


def test_resolve_processed_dir_accepts_bird_root(tmp_path):
    processed = tmp_path / "bird" / "processed"
    processed.mkdir(parents=True)
    (processed / "databases.json").write_text("[]", encoding="utf-8")

    assert ResolveProcessedDir(tmp_path / "bird") == processed.resolve()


def test_relative_database_path_ignores_cwd_and_project_directory_name(monkeypatch, tmp_path):
    project_root = tmp_path / "intern-agents"
    processed = project_root / "data" / "bird" / "processed"
    processed.mkdir(parents=True)
    database_path = project_root / "data" / "bird" / "databases" / "demo" / "demo.sqlite"
    _write_sqlite(database_path)
    (processed / "databases.json").write_text(json.dumps([{
        "database_id": "demo",
        "database_path": "data/bird/databases/demo/demo.sqlite",
        "tables": [],
        "foreign_keys": [],
    }]), encoding="utf-8")
    unrelated_cwd = tmp_path / "intern agents"
    unrelated_cwd.mkdir()
    monkeypatch.setattr(bird_io, "PROJECT_ROOT", project_root)
    monkeypatch.chdir(unrelated_cwd)

    database = LoadProcessedDatabases(processed)[0]

    assert database["database_path"] == str(database_path.resolve())
