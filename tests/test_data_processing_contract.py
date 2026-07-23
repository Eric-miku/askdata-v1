from pathlib import Path
import json
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from askdata.data.bird_io import LoadProcessedDatabases, LoadProcessedQuestions  # noqa: E402
from askdata.eval.runner import EvalRunner  # noqa: E402
from askdata.retrieval.retriever import SemanticRetriever  # noqa: E402


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


def write_demo_raw(tmp_path):
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
    return raw


def test_data_processing_native_outputs_feed_backend_end_to_end(tmp_path):
    write_demo_raw(tmp_path)

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


def test_prepare_bird_builds_schema_vector_index_contract(tmp_path):
    write_demo_raw(tmp_path)

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
        "--build-embeddings",
        "--embedding-provider", "hash",
        "--embedding-model", "hash-test",
        "--embedding-dimension", "8",
        "--embedding-batch-size", "2",
        "--vector-store", "jsonl",
        "--force",
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    vector_dir = processed / "vector_index"
    manifest = json.loads((vector_dir / "manifest.json").read_text(encoding="utf-8"))
    metadata = [
        json.loads(line)
        for line in (vector_dir / "schema_metadata.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    vectors = [
        json.loads(line)
        for line in (vector_dir / "schema_vectors.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    report = json.loads((processed / "preprocess_report.json").read_text(encoding="utf-8"))

    assert summary["vector_index"] == {"index_type": "jsonl", "document_count": 3, "dimension": 8}
    assert manifest["embedding_provider"] == "hash"
    assert manifest["embedding_model"] == "hash-test"
    assert manifest["document_types"] == {"table": 1, "column": 2}
    assert manifest["metadata_file"] == "schema_metadata.jsonl"
    assert manifest["vectors_file"] == "schema_vectors.jsonl"
    assert len(metadata) == len(vectors) == manifest["document_count"] == 3
    assert [item["id"] for item in metadata] == [item["id"] for item in vectors]
    assert metadata[0]["id"] == "schema://demo/table/items"
    assert metadata[0]["doc_type"] == "table"
    assert metadata[1]["doc_type"] == "column"
    assert metadata[1]["column_name"] == "id"
    assert "inventory items" in metadata[0]["text"]
    assert "item identifier" in metadata[1]["text"]
    assert all(len(item["vector"]) == 8 for item in vectors)
    assert report["outputs"]["vector_index"].endswith("processed/vector_index")
    assert report["vector_index"]["document_count"] == 3
