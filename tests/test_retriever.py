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
