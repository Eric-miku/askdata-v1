import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from typer.testing import CliRunner

from askdata import cli
from askdata.eval.demo_suite import DemoSuite


FIXTURE = Path(__file__).parent / "fixtures" / "v2_demo_cases.json"


def load_fixture():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cases = []
    predictions = []
    for item in payload["cases"]:
        case = dict(item)
        predictions.append({"id": case["id"], **case.pop("prediction")})
        cases.append(case)
    return cases, predictions


def test_demo_metrics_cover_all_v2_golden_journeys():
    cases, predictions = load_fixture()

    report = DemoSuite(cases).Compare(predictions)

    assert report["summary"] == {"total": 5, "passed": 5, "pass_rate": 1.0}
    assert report["by_category"]["clear"] == {
        "total": 1, "passed": 1, "pass_rate": 1.0
    }
    assert report["clarification_precision"] == 1.0
    assert report["clarification_recall"] == 1.0
    assert report["false_clarification_rate"] == 0.0
    assert report["unanswerable_precision"] == 1.0
    assert report["unanswerable_recall"] == 1.0
    assert report["proxy_query_rate"] == 0.0
    assert report["chart_spec_validity"] == 1.0
    assert report["retrieval_table_recall_at_k"] == 1.0
    assert report["retrieval_column_recall_at_k"] == 1.0
    assert report["stream_parity"] == 1.0
    assert report["restart_persistence"] == 1.0
    assert report["latency_ms"] == {"p50": 80.0, "p95": 136.0}
    assert report["llm_calls"] == 5
    assert report["sql_executions"] == 2
    assert report["token_usage"] == 1340


def test_missing_prediction_fields_never_pass_and_are_reported():
    cases = [{
        "id": "ranking",
        "category": "clear",
        "expected_kind": "answer",
        "expected_chart": "horizontal_bar",
        "expected_stream_parity": True,
    }]

    report = DemoSuite(cases).Compare([{"id": "ranking", "kind": "answer"}])

    assert report["summary"]["passed"] == 0
    assert report["chart_spec_validity"] == 0.0
    assert report["stream_parity"] == 0.0
    assert report["missing_fields"] == {
        "chart": 1,
        "latency_ms": 1,
        "llm_calls": 1,
        "sql": 1,
        "sql_executions": 1,
        "stream_parity": 1,
        "token_usage": 1,
    }


def test_zero_denominator_rates_are_zero_not_vacuous_passes():
    cases = [{"id": "clear", "category": "clear", "expected_kind": "answer"}]
    predictions = [{
        "id": "clear",
        "kind": "answer",
        "sql": "SELECT 1",
        "latency_ms": 1,
        "llm_calls": 0,
        "sql_executions": 1,
        "token_usage": 0,
    }]

    report = DemoSuite(cases).Compare(predictions)

    assert report["clarification_precision"] == 0.0
    assert report["clarification_recall"] == 0.0
    assert report["unanswerable_precision"] == 0.0
    assert report["unanswerable_recall"] == 0.0
    assert report["proxy_query_rate"] == 0.0
    assert report["chart_spec_validity"] == 0.0
    assert report["retrieval_table_recall_at_k"] == 0.0
    assert report["retrieval_column_recall_at_k"] == 0.0
    assert report["stream_parity"] == 0.0
    assert report["restart_persistence"] == 0.0


def test_false_clarification_rate_uses_only_clear_question_denominator():
    cases = [
        {"id": "clear", "category": "clear", "expected_kind": "answer"},
        {
            "id": "missing",
            "category": "unanswerable",
            "expected_kind": "error",
            "expected_error_code": "unanswerable_from_schema",
        },
    ]
    runtime = {"sql": None, "latency_ms": 1, "llm_calls": 0, "sql_executions": 0, "token_usage": 0}
    predictions = [
        {"id": "clear", "kind": "answer", **runtime, "sql": "SELECT 1"},
        {"id": "missing", "kind": "clarification", **runtime},
    ]

    report = DemoSuite(cases).Compare(predictions)

    assert report["false_clarification_rate"] == 0.0


def test_proxy_sql_makes_an_unanswerable_golden_journey_fail():
    cases = [{
        "id": "missing",
        "category": "unanswerable",
        "expected_kind": "error",
        "expected_error_code": "unanswerable_from_schema",
    }]
    predictions = [{
        "id": "missing",
        "kind": "error",
        "code": "unanswerable_from_schema",
        "sql": "SELECT name FROM employees",
        "latency_ms": 1,
        "llm_calls": 1,
        "sql_executions": 1,
        "token_usage": 50,
    }]

    report = DemoSuite(cases).Compare(predictions)

    assert report["proxy_query_rate"] == 1.0
    assert report["summary"]["passed"] == 0


def test_retrieval_recall_is_case_insensitive_and_missing_retrieval_fails_case():
    cases = [{
        "id": "semantic",
        "category": "semantic_mapping",
        "expected_kind": "answer",
        "gold_tables": ["Schools", "Districts"],
        "gold_columns": ["Schools.Name", "Districts.Id"],
    }]
    complete = {
        "id": "semantic",
        "kind": "answer",
        "sql": "SELECT 1",
        "retrieved_tables": ["schools", "districts"],
        "retrieved_columns": ["schools.name", "districts.id"],
        "latency_ms": 1,
        "llm_calls": 0,
        "sql_executions": 1,
        "token_usage": 0,
    }

    report = DemoSuite(cases).Compare([complete])
    missing = DemoSuite(cases).Compare([{k: v for k, v in complete.items() if k != "retrieved_columns"}])

    assert report["retrieval_table_recall_at_k"] == 1.0
    assert report["retrieval_column_recall_at_k"] == 1.0
    assert report["summary"]["passed"] == 1
    assert missing["retrieval_column_recall_at_k"] == 0.0
    assert missing["summary"]["passed"] == 0


def test_eval_demo_cli_writes_report_atomically_and_prints_category_table(tmp_path):
    out = tmp_path / "reports" / "demo.json"

    result = CliRunner().invoke(cli.app, [
        "eval-demo", "--cases", str(FIXTURE), "--out", str(out)
    ])

    assert result.exit_code == 0
    assert "Category" in result.output
    assert "semantic_mapping" in result.output
    assert "5/5" in result.output
    assert json.loads(out.read_text(encoding="utf-8"))["summary"]["passed"] == 5
    assert list(out.parent.glob("*.tmp")) == []


def test_eval_demo_cli_accepts_separate_predictions_and_fails_on_golden_failure(tmp_path):
    cases, predictions = load_fixture()
    case_path = tmp_path / "cases.json"
    predictions_path = tmp_path / "predictions.json"
    out = tmp_path / "report.json"
    case_path.write_text(json.dumps({"version": 1, "cases": cases}), encoding="utf-8")
    predictions[0].pop("chart")
    predictions_path.write_text(
        json.dumps({"version": 1, "predictions": predictions}), encoding="utf-8"
    )

    result = CliRunner().invoke(cli.app, [
        "eval-demo",
        "--cases", str(case_path),
        "--predictions", str(predictions_path),
        "--out", str(out),
    ])

    assert result.exit_code == 1
    assert "4/5" in result.output
    assert json.loads(out.read_text(encoding="utf-8"))["summary"]["passed"] == 4
