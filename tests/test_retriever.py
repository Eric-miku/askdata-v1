from pathlib import Path
import json
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.tools.retriever import BirdSchemaIndex, SemanticRetriever


def sample_database(table_count=2):
    tables = [
        {
            "tableName": "schools",
            "columns": [
                {"columnName": "id", "columnType": "integer", "isPrimary": True},
                {"columnName": "school_name", "columnType": "text"},
            ],
        },
        {
            "tableName": "students",
            "columns": [
                {"columnName": "id", "columnType": "integer", "isPrimary": True},
                {"columnName": "school_id", "columnType": "integer"},
                {"columnName": "age", "columnType": "integer"},
            ],
        },
    ]
    for index in range(3, table_count + 1):
        tables.append({
            "tableName": f"table_{index}",
            "columns": [{"columnName": "id", "columnType": "integer", "isPrimary": True}],
        })
    return {
        "databaseId": "demo",
        "databasePath": "/tmp/demo.sqlite",
        "tables": tables,
        "foreignKeys": [
            {
                "leftTable": "students",
                "leftColumn": "school_id",
                "rightTable": "schools",
                "rightColumn": "id",
            }
        ],
    }


def test_retrieve_matches_table_name_and_includes_database_path():
    index = BirdSchemaIndex().Build([sample_database()])

    result = index.Retrieve("demo", "list schools")

    assert result["database_id"] == "demo"
    assert result["database_path"] == "/tmp/demo.sqlite"
    assert result["matched_tables"] == [
        {"table_name": "schools", "reason": "Token match."},
        {"table_name": "students", "reason": "Foreign-key neighbor."},
    ]
    assert "Table schools(id integer, school_name text)" in result["schema_prompt"]


def test_retrieve_matches_column_name_and_includes_join_context():
    index = BirdSchemaIndex().Build([sample_database()])

    result = index.Retrieve("demo", "average age by school")

    assert {"table_name": "students", "reason": "Token match."} in result["matched_tables"]
    assert {
        "table_name": "students",
        "column_name": "age",
        "column_type": "integer",
        "reason": "Token match.",
    } in result["matched_columns"]
    assert result["matched_joins"] == [
        {
            "left_table": "students",
            "left_column": "school_id",
            "right_table": "schools",
            "right_column": "id",
        }
    ]
    assert "Join students.school_id = schools.id" in result["schema_prompt"]


def test_retrieve_returns_exact_question_evidence_separately():
    index = BirdSchemaIndex().Build(
        [sample_database()],
        questions=[
            {
                "database_id": "demo",
                "question": "List total enrollment?",
                "evidence": "Total enrollment is enrollment_a + enrollment_b",
            }
        ],
    )

    result = index.Retrieve("demo", "list total enrollment")

    assert result["evidence"] == "Total enrollment is enrollment_a + enrollment_b"
    assert "Evidence: Total enrollment is enrollment_a + enrollment_b" in result["schema_prompt"]


def test_retrieve_falls_back_to_first_eight_tables():
    index = BirdSchemaIndex().Build([sample_database(table_count=10)])

    result = index.Retrieve("demo", "unmatched words")

    assert len(result["matched_tables"]) == 8
    assert result["matched_tables"][0]["table_name"] == "schools"
    assert result["matched_tables"][-1]["table_name"] == "table_8"
    assert "Table table_9" not in result["schema_prompt"]


def test_semantic_retriever_returns_prompt_string():
    retriever = SemanticRetriever(index=BirdSchemaIndex().Build([sample_database()]))

    prompt = retriever.Retrieve("demo", "list schools")

    assert isinstance(prompt, str)
    assert prompt.startswith("Database: demo")
    assert "SQLite path: /tmp/demo.sqlite" in prompt
    assert "Table schools(id integer, school_name text)" in prompt


def test_native_schema_matches_display_names_and_expands_foreign_key_neighbor():
    database = {
        "database_id": "demo",
        "database_path": "/tmp/demo.sqlite",
        "schema_prompt": "",
        "tables": [
            {
                "table_name": "orders",
                "display_name": "customer purchases",
                "columns": [
                    {
                        "column_name": "customer_id",
                        "display_name": "buyer identifier",
                        "description": "customer account",
                        "data_type": "integer",
                        "is_primary_key": False,
                    }
                ],
            },
            {
                "table_name": "customers",
                "display_name": "buyers",
                "columns": [
                    {
                        "column_name": "id",
                        "display_name": "customer id",
                        "description": "stable buyer key",
                        "data_type": "integer",
                        "is_primary_key": True,
                    }
                ],
            },
        ],
        "foreign_keys": [{
            "source_table": "orders",
            "source_column": "customer_id",
            "target_table": "customers",
            "target_column": "id",
        }],
    }

    result = BirdSchemaIndex().Build([database]).Retrieve("demo", "list customer purchases")

    assert {item["table_name"] for item in result["matched_tables"]} == {"orders", "customers"}
    assert result["matched_joins"] == [{
        "left_table": "orders",
        "left_column": "customer_id",
        "right_table": "customers",
        "right_column": "id",
    }]
    assert "Table orders(customer_id integer)" in result["schema_prompt"]
    assert "Table customers(id integer)" in result["schema_prompt"]


def test_semantic_retriever_loads_native_contract_and_evidence(tmp_path):
    processed = tmp_path / "processed"
    schemas = processed / "schemas"
    schemas.mkdir(parents=True)
    database_path = tmp_path / "demo.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY)")
    (schemas / "demo.json").write_text(json.dumps({
        "database_id": "demo",
        "database_path": str(database_path),
        "tables": [{
            "table_name": "items",
            "columns": [{"column_name": "id", "data_type": "integer", "is_primary_key": True}],
        }],
        "foreign_keys": [],
    }), encoding="utf-8")
    (processed / "databases.json").write_text(json.dumps([{
        "database_id": "demo",
        "database_path": str(database_path),
        "schema_path": str(schemas / "demo.json"),
    }]), encoding="utf-8")
    (processed / "questions.jsonl").write_text(json.dumps({
        "question_id": "q1",
        "database_id": "demo",
        "question": "How many items?",
        "gold_sql": "SELECT COUNT(id) FROM items",
        "evidence": "Count every item row.",
    }) + "\n", encoding="utf-8")

    prompt = SemanticRetriever(processed_dir=processed).Build().Retrieve("demo", "How many items?")

    assert "Evidence: Count every item row." in prompt
    assert "Table items(id integer)" in prompt


def test_schema_index_exposes_ranked_lexical_candidates_and_canonical_chunks(tmp_path):
    instructions = tmp_path / "instructions"
    instructions.mkdir()
    (instructions / "demo.md").write_text(
        "## Business Term Mappings\n- State Special School -> EdOpsCode = 'SSS'\n"
        "## JOIN Patterns\n- students.school_id = schools.id\n",
        encoding="utf-8",
    )
    questions = [{
        "database_id": "demo",
        "question": "How many special schools?",
        "evidence": "SSS identifies a State Special School.",
        "gold_sql": "SELECT COUNT(*) FROM schools WHERE EdOpsCode = 'SSS'",
        "question_id": "q1",
    }]
    index = BirdSchemaIndex(instructions_dir=instructions).Build(
        [sample_database()], questions=questions
    )

    lexical = index.LexicalCandidates("demo", "school age")
    chunks = index.BuildChunks("demo")
    backbone = index.SchemaBackbone("demo")

    assert lexical
    assert lexical[0].score >= lexical[-1].score
    assert {chunk.source_type for chunk in chunks} == {"schema", "value", "evidence", "example"}
    assert any(
        chunk.source_type == "schema"
        and chunk.table_name == "students"
        and chunk.column_name == "age"
        for chunk in chunks
    )
    assert len({chunk.id for chunk in chunks}) == len(chunks)
    assert all(chunk.database_id == "demo" for chunk in chunks)
    assert "schools.id [primary key]" in backbone
    assert "students.school_id -> schools.id" in backbone


def test_schema_chunks_attribute_foreign_keys_and_join_neighbors():
    chunks = BirdSchemaIndex().Build([sample_database()]).BuildChunks("demo")
    students = next(
        chunk for chunk in chunks
        if chunk.source_type == "schema"
        and chunk.table_name == "students"
        and chunk.column_name is None
    )

    assert "students.school_id -> schools.id" in students.text
    assert "schools" in students.join_neighbors
    assert "students.school_id -> schools.id" in students.foreign_keys


def test_instruction_chunks_separate_value_mappings_from_business_evidence(tmp_path):
    instructions = tmp_path / "instructions"
    instructions.mkdir()
    (instructions / "demo.md").write_text(
        "## Business Term Mappings\n"
        "- SSS = State Special School\n"
        "- enrollment means count of students\n"
        "- pupils is an alias for students\n"
        "## JOIN Patterns\n"
        "- students.school_id = schools.id\n",
        encoding="utf-8",
    )

    chunks = BirdSchemaIndex(instructions_dir=instructions).Build(
        [sample_database()]
    ).BuildChunks("demo")
    by_text = {chunk.text: chunk for chunk in chunks}

    assert by_text["SSS = State Special School"].source_type == "value"
    assert by_text["enrollment means count of students"].source_type == "evidence"
    assert by_text["pupils is an alias for students"].source_type == "evidence"
    join = by_text["JOIN pattern: students.school_id = schools.id"]
    assert join.source_type == "evidence"
    assert set(join.join_neighbors) == {"students", "schools"}


def test_canonical_value_chunks_bound_and_attribute_profiled_values():
    database = sample_database()
    database["tables"][0]["columns"][1]["sample_values"] = [
        f"school-{index}" for index in range(30)
    ]
    index = BirdSchemaIndex().Build([database])

    values = [chunk for chunk in index.BuildChunks("demo") if chunk.source_type == "value"]

    assert len(values) == 20
    assert all(chunk.table_name == "schools" for chunk in values)
    assert all(chunk.column_name == "school_name" for chunk in values)


def test_vector_startup_failure_is_cached_and_returns_safe_fallback(
    tmp_path, monkeypatch
):
    processed = tmp_path / "processed"
    schemas = processed / "schemas"
    schemas.mkdir(parents=True)
    database_path = tmp_path / "demo.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY)")
    (schemas / "demo.json").write_text(json.dumps({
        "database_id": "demo",
        "database_path": str(database_path),
        "tables": [{"table_name": "items", "columns": [{
            "column_name": "id", "data_type": "integer", "is_primary_key": True,
        }]}],
        "foreign_keys": [],
    }), encoding="utf-8")
    (processed / "databases.json").write_text(json.dumps([{
        "database_id": "demo", "database_path": str(database_path),
        "schema_path": str(schemas / "demo.json"),
    }]), encoding="utf-8")

    from askdata.core.config import settings
    from askdata.tools import embedding_client, vector_store
    from askdata.tools.retriever import _ResetVectorValidationFailuresForTests

    calls = {"embed": 0, "search": 0}

    class Embedding:
        def __init__(self, **kwargs):
            pass

        def Validate(self):
            calls["embed"] += 1
            return [0.1, 0.2]

    class Store:
        def __init__(self, *args):
            pass

        def Search(self, database_id, vectors, top_k):
            calls["search"] += 1
            raise RuntimeError("secret remote failure")

    monkeypatch.setattr(settings, "VECTOR_RETRIEVAL_ENABLED", True)
    monkeypatch.setattr(settings, "EMBEDDING_API_URL", "http://embedding.test/v1")
    monkeypatch.setattr(settings, "MILVUS_URI", "http://milvus.test")
    monkeypatch.setattr(embedding_client, "EmbeddingClient", Embedding)
    monkeypatch.setattr(vector_store, "MilvusVectorStore", Store)
    _ResetVectorValidationFailuresForTests()

    first = SemanticRetriever(processed_dir=processed).Build().index.Retrieve("demo", "items")
    second = SemanticRetriever(processed_dir=processed).Build().index.Retrieve("demo", "items")

    assert calls == {"embed": 1, "search": 1}
    assert first["schema_prompt"].startswith("Database: demo")
    assert first["retrieval_trace"] == second["retrieval_trace"]
    assert first["retrieval_trace"] == [{
        "status": "warning",
        "message": "Semantic retrieval is unavailable; lexical schema retrieval was used.",
    }]
    assert "secret" not in str(first)


def test_disabled_vector_configuration_makes_no_validation_call(tmp_path, monkeypatch):
    processed = tmp_path / "processed"
    processed.mkdir()
    database_path = tmp_path / "demo.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE items(id INTEGER)")
    (processed / "databases.json").write_text(json.dumps([{
        "databaseId": "demo", "databasePath": str(database_path),
        "tables": [{"tableName": "items", "columns": []}], "foreignKeys": [],
    }]), encoding="utf-8")

    from askdata.core.config import settings
    from askdata.tools import embedding_client

    monkeypatch.setattr(settings, "VECTOR_RETRIEVAL_ENABLED", False)
    monkeypatch.setattr(embedding_client, "EmbeddingClient", lambda **kwargs: (_ for _ in ()).throw(
        AssertionError("disabled vector retrieval must not construct a network client")
    ))

    prompt = SemanticRetriever(processed_dir=processed).Build().Retrieve("demo", "items")

    assert "Table items" in prompt
