from pathlib import Path
import json
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.eval.runner import EvalRunner


class FakeAgentGraph:
    def __init__(self, processed_dir=None):
        self.processed_dir = processed_dir

    def Run(self, question, database_id, session_context=None):
        return {
            "answer": "There are 2 items.",
            "sql": "SELECT COUNT(id) AS count FROM items",
            "columns": ["count"],
            "rows": [{"count": 2}],
            "trace": [{"step": "ExecuteSql", "status": "success", "message": "Returned 1 rows."}],
            "error": None,
        }


class FakeCandidateAgentGraph:
    def Run(self, question, database_id, session_context=None):
        return {
            "answer": "There is 1 item.",
            "sql": "SELECT 1 AS count",
            "columns": ["count"],
            "rows": [{"count": 1}],
            "trace": [{"step": "SelectBestCandidate", "status": "success", "message": "Selected candidate 1."}],
            "error": None,
            "candidates": [
                {
                    "sql": "SELECT 1 AS count",
                    "columns": ["count"],
                    "rows": [{"count": 1}],
                },
                {
                    "sql": "SELECT COUNT(id) AS count FROM items",
                    "columns": ["count"],
                    "rows": [{"count": 2}],
                },
            ],
        }


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


def write_multi_question_dataset(root, database_path):
    processed = write_processed_dataset(root, database_path)
    questions = [
        {
            "questionId": f"q{index}",
            "databaseId": "demo",
            "question": "How many items?",
            "goldSql": "SELECT COUNT(id) AS count FROM items",
            "difficulty": "simple",
        }
        for index in range(5)
    ]
    (processed / "questions.json").write_text(json.dumps(questions), encoding="utf-8")
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

    report = EvalRunner(processed_dir=processed, agent_graph=FakeAgentGraph()).Run(limit=1, out=out)

    assert out.exists()
    assert report["summary"]["total"] == 1
    assert report["summary"]["executionAccuracy"] == 1.0
    assert report["summary"]["validSqlRate"] == 1.0
    assert report["summary"]["exactMatchRate"] == 1.0
    assert report["summary"]["answerProducedRate"] == 1.0
    assert report["cases"][0]["generatedSql"] == "SELECT COUNT(id) AS count FROM items"
    assert report["cases"][0]["passed"] is True


def test_eval_runner_seed_reproducibly_shuffles_questions_before_limit(tmp_path):
    database_path = tmp_path / "demo.sqlite"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE items(id INTEGER)")
    connection.executemany("INSERT INTO items(id) VALUES (?)", [(1,), (2,)])
    connection.commit()
    connection.close()
    processed = write_multi_question_dataset(tmp_path, database_path)

    report_a = EvalRunner(processed_dir=processed, agent_graph=FakeAgentGraph()).Run(limit=3, seed=42)
    report_b = EvalRunner(processed_dir=processed, agent_graph=FakeAgentGraph()).Run(limit=3, seed=42)
    report_c = EvalRunner(processed_dir=processed, agent_graph=FakeAgentGraph()).Run(limit=3, seed=99)

    ids_a = [case["questionId"] for case in report_a["cases"]]
    ids_b = [case["questionId"] for case in report_b["cases"]]
    ids_c = [case["questionId"] for case in report_c["cases"]]
    assert ids_a == ids_b == ["q3", "q1", "q2"]
    assert ids_c == ["q1", "q2", "q0"]


def test_eval_runner_manifest_selects_exact_ids_and_records_metadata(tmp_path):
    database_path = tmp_path / "demo.sqlite"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE items(id INTEGER)")
    connection.executemany("INSERT INTO items(id) VALUES (?)", [(1,), (2,)])
    connection.commit()
    connection.close()
    processed = write_multi_question_dataset(tmp_path, database_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"question_ids": ["q4", "q1"]}), encoding="utf-8")

    report = EvalRunner(processed_dir=processed, agent_graph=FakeAgentGraph()).Run(
        limit=1,
        seed=99,
        question_manifest=manifest,
    )

    assert [case["questionId"] for case in report["cases"]] == ["q4", "q1"]
    assert report["summary"]["executionAccuracyStrict"] == 1.0
    assert report["summary"]["executionAccuracyRelaxed"] == 1.0
    assert report["summary"]["retryRepairRate"] == 0.0
    assert report["metadata"]["questionManifest"] == str(manifest.resolve())
    assert len(report["metadata"]["questionManifestSha256"]) == 64
    assert len(report["metadata"]["processedDataSha256"]) == 64
    assert report["metadata"]["seed"] == 99
    assert report["metadata"]["limit"] == 1


def test_eval_runner_reads_native_questions_jsonl(tmp_path):
    database_path = tmp_path / "demo.sqlite"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE items(id INTEGER)")
    connection.executemany("INSERT INTO items(id) VALUES (?)", [(1,), (2,)])
    connection.commit()
    connection.close()
    processed = tmp_path / "processed"
    processed.mkdir()
    (processed / "databases.json").write_text(json.dumps([{
        "database_id": "demo",
        "database_path": str(database_path),
        "tables": [{
            "table_name": "items",
            "columns": [{"column_name": "id", "data_type": "integer", "is_primary_key": True}],
        }],
        "foreign_keys": [],
    }]), encoding="utf-8")
    (processed / "questions.jsonl").write_text(json.dumps({
        "question_id": "bird_0001",
        "database_id": "demo",
        "question": "How many items?",
        "gold_sql": "SELECT COUNT(id) AS count FROM items",
        "difficulty": "simple",
    }) + "\n", encoding="utf-8")

    report = EvalRunner(processed_dir=processed, agent_graph=FakeAgentGraph()).Run()

    assert report["summary"]["total"] == 1
    assert report["cases"][0]["questionId"] == "bird_0001"


def test_eval_runner_reports_candidate_hit_and_selection_loss(tmp_path):
    database_path = tmp_path / "demo.sqlite"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE items(id INTEGER)")
    connection.executemany("INSERT INTO items(id) VALUES (?)", [(1,), (2,)])
    connection.commit()
    connection.close()
    processed = write_processed_dataset(tmp_path, database_path)

    report = EvalRunner(processed_dir=processed, agent_graph=FakeCandidateAgentGraph()).Run()

    assert report["summary"]["executionAccuracyRelaxed"] == 0.0
    assert report["summary"]["candidateHitRate"] == 1.0
    assert report["summary"]["candidateStrictHitRate"] == 1.0
    assert report["summary"]["candidateSelectionLossRate"] == 1.0
    assert report["byDatabase"]["demo"]["candidateSelectionLossRate"] == 1.0
    case = report["cases"][0]
    assert case["candidateCount"] == 2
    assert case["candidateHit"] is True
    assert case["candidateOutcomes"][1]["strictPass"] is True
