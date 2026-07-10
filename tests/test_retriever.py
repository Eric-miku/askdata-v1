from pathlib import Path
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
    assert result["matched_tables"] == [{"table_name": "schools", "reason": "Token match."}]
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
