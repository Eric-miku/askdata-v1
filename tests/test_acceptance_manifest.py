import json
import sqlite3
from pathlib import Path

from askdata.agent.understanding import QuestionUnderstanding
from askdata.db.validator import SQLValidator
from askdata.data.source_store import DataSourceStore
from askdata.db.optimizer import ExplainSqliteQuery
from askdata.db.query_runner import Execute
from askdata.security.permissions import PermissionStore, ResetSqlAuthorizer, SetSqlAuthorizer


ROOT = Path(__file__).resolve().parents[1]


def test_core_acceptance_manifest_has_measurable_thresholds_and_passes_deterministic_cases(tmp_path):
    manifest = json.loads(
        (ROOT / "benchmarks" / "core-acceptance-scenarios.json").read_text(encoding="utf-8")
    )

    assert manifest["thresholds"] == {
        "sql_execution_success_rate": 0.98,
        "business_relaxed_accuracy": 0.80,
        "multi_turn_pass_rate": 0.90,
        "dangerous_sql_block_rate": 1.0,
        "export_correctness_rate": 1.0,
        "permission_bypass_block_rate": 1.0,
        "blocking_or_critical_defects": 0,
    }

    understanding = QuestionUnderstanding()
    for scenario in manifest["multi_turn"]:
        previous = None
        for step in scenario["steps"]:
            current = understanding.Resolve(step["question"], previous)
            for key, expected in step["expected"].items():
                assert current[key] == expected, f"{scenario['id']} / {step['question']} / {key}"
            previous = current

    validator = SQLValidator(dialect="sqlite")
    for case in manifest["security"]:
        result = validator.validate(case["sql"])
        assert result.is_valid is not case["must_block"], case["id"]

    database = tmp_path / "acceptance.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE regions(id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE orders(
                id INTEGER PRIMARY KEY,
                region_id INTEGER,
                region TEXT,
                FOREIGN KEY(region_id) REFERENCES regions(id)
            );
            INSERT INTO orders(id, region) VALUES (1, '华东'), (2, '华南');
        """)
    store = DataSourceStore(tmp_path / "sources.sqlite")
    store.save("acceptance", "Acceptance", str(database))
    first = store.mark_synced("acceptance")
    unchanged = store.mark_synced("acceptance")
    catalog_requirement = manifest["schema_catalog"]
    table = next(item for item in store.catalog("acceptance")["catalog"]["tables"] if item["name"] == "orders")
    assert set(catalog_requirement["required_objects"]).issubset(table)
    assert len(first["schema_fingerprint"]) == 64
    assert unchanged["schema_fingerprint"] == first["schema_fingerprint"]
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE orders ADD COLUMN amount REAL")
    changed = store.mark_synced("acceptance")
    assert catalog_requirement["ddl_change_must_report_table"] in changed["schema_change_summary"]["tables_changed"]

    for case in manifest["query_plan"]:
        result = ExplainSqliteQuery(case["sql"], database)
        assert result["success"] is case["must_succeed"], case["id"]
        if case["must_succeed"]:
            assert case["expected_suggestion_type"] in {item["type"] for item in result["suggestions"]}
        else:
            assert result["error_code"] == case["expected_error_code"]

    row_requirement = manifest["row_level_policy"]
    permissions = PermissionStore(tmp_path / "permissions.sqlite")
    permissions.save({
        "user_id": "alice",
        "database_id": "acceptance",
        "table_name": row_requirement["table"],
        "row_filter": row_requirement["filter"],
        "can_query": True,
        "can_export": True,
    })
    for mode in ("query", "export"):
        token = SetSqlAuthorizer(
            lambda sql, access_mode: permissions.prepare_sql(
                "alice", "acceptance", sql, access_mode
            )
        )
        try:
            result = Execute(row_requirement["sql"], database, access_mode=mode)
        finally:
            ResetSqlAuthorizer(token)
        assert result["success"]
        assert [row["id"] for row in result["rows"]] == row_requirement["expected_ids"]
        assert result["sql"] == row_requirement["sql"]
    allowed, reason, explained_sql = permissions.prepare_sql(
        "alice", "acceptance", row_requirement["sql"], "query"
    )
    assert allowed and reason is None
    assert row_requirement["filter"] in explained_sql
    assert ExplainSqliteQuery(explained_sql, database)["success"]
