from pathlib import Path
import json
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from typer.testing import CliRunner

from askdata import cli


runner = CliRunner()


def write_processed_dataset(root):
    processed = root / "processed"
    processed.mkdir()
    database_path = root / "demo.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE items(id INTEGER)")
    (processed / "databases.json").write_text(json.dumps([
        {
            "databaseId": "demo",
            "databasePath": str(database_path),
            "tables": [{"tableName": "items", "columns": []}],
            "foreignKeys": [],
        }
    ]), encoding="utf-8")
    return processed


def test_cli_help_lists_development_commands():
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "serve" in result.output
    assert "eval-bird" in result.output
    assert "chat" in result.output
    assert "databases" in result.output
    assert "gen-instructions" in result.output
    assert "index-schema" in result.output


def test_index_schema_validates_all_embeddings_before_mutating_store(tmp_path, monkeypatch):
    processed = write_processed_dataset(tmp_path)

    class BadEmbedding:
        model = "bad-model"
        dimension = 2

        def Embed(self, texts):
            return [[1.0, 2.0]][: max(0, len(texts) - 1)]

    class RecordingStore:
        collection_name = "test_chunks"

        def __init__(self):
            self.upserts = []

        def Upsert(self, chunks, vectors):
            self.upserts.append((chunks, vectors))

    store = RecordingStore()
    monkeypatch.setattr(cli, "_BuildEmbeddingClient", lambda: BadEmbedding())
    monkeypatch.setattr(cli, "_BuildVectorStore", lambda: store)

    result = runner.invoke(cli.app, [
        "index-schema", "--database-id", "demo", "--processed-dir", str(processed)
    ])

    assert result.exit_code != 0
    assert store.upserts == []


def test_index_schema_prints_validated_index_metadata(tmp_path, monkeypatch):
    processed = write_processed_dataset(tmp_path)
    (processed / "questions.jsonl").write_text(json.dumps({
        "question_id": "q1",
        "database_id": "demo",
        "question": "How many items?",
        "gold_sql": "SELECT COUNT(*) FROM items",
        "evidence": "Count item rows.",
    }) + "\n", encoding="utf-8")

    class Embedding:
        model = "test-model"
        dimension = 2

        def Embed(self, texts):
            return [[1.0, 2.0] for _ in texts]

    class Store:
        collection_name = "test_chunks"

        def __init__(self):
            self.upserts = []

        def Upsert(self, chunks, vectors):
            self.upserts.append((chunks, vectors))

    store = Store()
    monkeypatch.setattr(cli, "_BuildEmbeddingClient", lambda: Embedding())
    monkeypatch.setattr(cli, "_BuildVectorStore", lambda: store)

    result = runner.invoke(cli.app, [
        "index-schema", "--database-id", "demo", "--processed-dir", str(processed)
    ])

    assert result.exit_code == 0
    assert len(store.upserts) == 1
    assert "model: test-model" in result.output
    assert "dimension: 2" in result.output
    assert "collection: test_chunks" in result.output
    assert "source version:" in result.output
    assert "evidence chunks: 1" in result.output
    assert "example chunks: 1" in result.output


def test_build_vector_store_uses_legacy_milvus_host_when_uri_is_absent(monkeypatch):
    captured = {}

    class Store:
        def __init__(self, uri, collection_name):
            captured["uri"] = uri
            captured["collection_name"] = collection_name

    monkeypatch.setattr(cli.settings, "MILVUS_URI", "")
    monkeypatch.setattr(cli.settings, "MILVUS_HOST", "7.59.11.153", raising=False)
    monkeypatch.setattr(cli.settings, "MILVUS_PORT", 19530, raising=False)
    monkeypatch.setattr(cli.settings, "MILVUS_COLLECTION", "test_chunks")
    monkeypatch.setattr(cli, "MilvusVectorStore", Store)

    cli._BuildVectorStore()

    assert captured == {
        "uri": "http://7.59.11.153:19530",
        "collection_name": "test_chunks",
    }


def test_eval_bird_help_is_available():
    result = runner.invoke(cli.app, ["eval-bird", "--help"])

    assert result.exit_code == 0
    assert "--limit" in result.output
    assert "--out" in result.output
    assert "--seed" in result.output
    assert "--question-manifest" in result.output
    assert "--model-name" in result.output
    assert "--processed-dir" in result.output


def test_databases_lists_processed_database_ids(tmp_path):
    processed = write_processed_dataset(tmp_path)

    result = runner.invoke(cli.app, ["databases", "--processed-dir", str(processed)])

    assert result.exit_code == 0
    assert "demo" in result.output
    assert "items" in result.output


def test_databases_loads_tables_from_native_schema_file(tmp_path):
    processed = tmp_path / "processed"
    schemas = processed / "schemas"
    schemas.mkdir(parents=True)
    database_path = tmp_path / "demo.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE native_items(id INTEGER)")
    (schemas / "demo.json").write_text(json.dumps({
        "database_id": "demo",
        "database_path": str(database_path),
        "tables": [{"table_name": "native_items", "columns": []}],
        "foreign_keys": [],
    }), encoding="utf-8")
    (processed / "databases.json").write_text(json.dumps([{
        "database_id": "demo",
        "database_path": str(database_path),
        "schema_path": str(schemas / "demo.json"),
    }]), encoding="utf-8")

    result = runner.invoke(cli.app, ["databases", "--processed-dir", str(processed)])

    assert result.exit_code == 0
    assert "native_items" in result.output


def test_gen_instructions_writes_one_template_per_database(tmp_path):
    processed = write_processed_dataset(tmp_path)
    out_dir = tmp_path / "instructions"

    result = runner.invoke(cli.app, ["gen-instructions", "--processed-dir", str(processed), "--out-dir", str(out_dir)])

    assert result.exit_code == 0
    template = out_dir / "demo.md"
    assert template.exists()
    content = template.read_text(encoding="utf-8")
    assert "Business Term Mappings" in content
    assert "JOIN Patterns" in content


class FakeAgentGraph:
    def __init__(self):
        self.calls = []

    def Run(self, question, database_id, session_context=None):
        self.calls.append((question, database_id, session_context))
        return {
            "answer": "共有 3 条。",
            "sql": "SELECT COUNT(id) AS count FROM items",
            "columns": ["count"],
            "rows": [{"count": 3}],
            "trace": [],
            "error": None,
        }


def test_chat_session_runs_query_and_stores_last_sql():
    agent = FakeAgentGraph()
    session = cli.ChatSession(agent_graph=agent, database_id="demo")

    output = session.Ask("How many items?")

    assert "共有 3 条。" in output
    assert "SELECT COUNT(id) AS count FROM items" in output
    assert session.last_sql == "SELECT COUNT(id) AS count FROM items"
    assert agent.calls[0][1] == "demo"
