from pathlib import Path
import json
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from askdata.data.bird_io import LoadProcessedDatabases, LoadProcessedQuestions  # noqa: E402
from askdata.eval.runner import EvalRunner  # noqa: E402
from askdata.tools.retriever import SemanticRetriever  # noqa: E402


class ContractAgent:
    def Run(self, question, database_id, session_context=None):
        return {
            "answer": "There is one item.",
            "sql": "SELECT COUNT(id) AS count FROM items",
            "columns": ["count"],
            "rows": [{"count": 1}],
            "trace": [],
            "error": None,
        }


def test_data_processing_native_outputs_feed_backend_end_to_end(tmp_path):
    raw = tmp_path / "raw" / "MINIDEV"
    source_db_dir = raw / "dev_databases" / "demo"
    source_db_dir.mkdir(parents=True)
    source_db = source_db_dir / "demo.sqlite"
    with sqlite3.connect(source_db) as connection:
        connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT)")
        connection.execute("INSERT INTO items(id, name) VALUES (1, 'one')")

    (raw / "dev_tables.json").write_text(json.dumps([{
        "db_id": "demo",
        "table_names_original": ["items"],
        "table_names": ["inventory items"],
        "column_names_original": [[-1, "*"], [0, "id"], [0, "name"]],
        "column_names": [[-1, "*"], [0, "item identifier"], [0, "item name"]],
        "column_types": ["text", "integer", "text"],
        "primary_keys": [1],
        "foreign_keys": [],
    }]), encoding="utf-8")
    (raw / "mini_dev_sqlite.json").write_text(json.dumps([{
        "question_id": 1,
        "db_id": "demo",
        "question": "How many inventory items are there?",
        "evidence": "Count every item row.",
        "SQL": "SELECT COUNT(id) AS count FROM items",
        "difficulty": "simple",
    }]), encoding="utf-8")

    databases_dir = tmp_path / "databases"
    processed = tmp_path / "processed"
    command = [
        sys.executable,
        str(ROOT / "data-processing" / "src" / "askdata" / "cli.py"),
        "prepare-bird",
        "--raw-dir", str(tmp_path / "raw"),
        "--db-dir", str(databases_dir),
        "--out-dir", str(processed),
        "--demo-db-limit", "1",
        "--demo-question-limit", "1",
        "--validate-sql",
        "--force",
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)

    assert completed.returncode == 0, completed.stderr
    assert not (processed / "questions.json").exists()
    databases = LoadProcessedDatabases(processed)
    questions = LoadProcessedQuestions(processed, database_ids={"demo"})
    prompt = SemanticRetriever(processed_dir=processed).Build().Retrieve(
        "demo", "How many inventory items are there?"
    )
    report = EvalRunner(processed_dir=processed, agent_graph=ContractAgent()).Run()

    assert databases[0]["tables"][0]["display_name"] == "inventory items"
    assert questions[0]["question_id"] == "bird_0001"
    assert "Evidence: Count every item row." in prompt
    assert report["summary"]["executionAccuracy"] == 1.0
