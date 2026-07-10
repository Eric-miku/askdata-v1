from pathlib import Path
import json
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.eval.runner import EvalRunner


class FakeLLM:
    def Complete(self, prompt):
        assert "Database: demo" in prompt
        return "```sql\nSELECT COUNT(id) AS count FROM items\n```"


def write_processed_dataset(root, database_path):
    processed = root / "processed"
    processed.mkdir()
    (processed / "databases.json").write_text(json.dumps([
        {
            "databaseId": "demo",
            "databasePath": str(database_path),
            "tables": [
                {
                    "tableName": "items",
                    "columns": [
                        {"columnName": "id", "columnType": "integer", "isPrimary": True},
                    ],
                }
            ],
            "foreignKeys": [],
        }
    ]), encoding="utf-8")
    (processed / "questions.json").write_text(json.dumps([
        {
            "questionId": "q1",
            "databaseId": "demo",
            "question": "How many items?",
            "goldSql": "SELECT COUNT(id) AS count FROM items",
            "difficulty": "simple",
        }
    ]), encoding="utf-8")
    return processed


def test_eval_runner_is_self_contained_and_writes_report(tmp_path):
    database_path = tmp_path / "demo.sqlite"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE items(id INTEGER)")
    connection.executemany("INSERT INTO items(id) VALUES (?)", [(1,), (2,)])
    connection.commit()
    connection.close()
    processed = write_processed_dataset(tmp_path, database_path)
    out = tmp_path / "report.json"

    report = EvalRunner(processed_dir=processed, llm_client=FakeLLM()).Run(limit=1, out=out)

    assert out.exists()
    assert report["summary"]["total"] == 1
    assert report["summary"]["executionAccuracy"] == 1.0
    assert report["summary"]["validSqlRate"] == 1.0
    assert report["summary"]["exactMatchRate"] == 1.0
    assert report["summary"]["answerProducedRate"] == 1.0
    assert report["cases"][0]["generatedSql"] == "SELECT COUNT(id) AS count FROM items"
    assert report["cases"][0]["passed"] is True
