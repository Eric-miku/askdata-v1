from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.eval.manual_regressions import (
    LoadManualRegressionCases,
    ManualRegressionCase,
    ManualRegressionRunner,
)


FIXTURE = Path(__file__).parent / "fixtures" / "manual_regressions.json"


def test_load_manual_regression_cases():
    cases = LoadManualRegressionCases(FIXTURE)

    assert {case.id for case in cases} >= {
        "toxicology_tr060_elements_label",
        "debit_card_consumption_decrease_rate",
        "california_monterey_high_school_frpm_address",
        "california_total_enrollment_over_500",
    }


def test_manual_regression_runner_checks_columns_rows_error_and_forbidden_sql():
    case = ManualRegressionCase(
        id="demo",
        database_id="demo",
        question="question",
        expected_columns=["element", "label"],
        min_rows=1,
        expected_error=None,
        must_not_sql=["SELECT *"],
    )
    runner = ManualRegressionRunner()

    passed = runner.Check(
        case,
        {
            "error": None,
            "sql": "SELECT element, label FROM molecule",
            "columns": ["element", "label"],
            "rows": [{"element": "c", "label": "-"}],
        },
    )
    failed = runner.Check(
        case,
        {
            "error": None,
            "sql": "SELECT * FROM molecule",
            "columns": ["label"],
            "rows": [{"label": "-"}],
        },
    )

    assert passed["passed"] is True
    assert failed["passed"] is False
    assert set(failed["failures"]) >= {"missing_column:element", "forbidden_sql:SELECT *"}


def test_manual_regression_runner_can_use_injected_query_function():
    cases = [
        ManualRegressionCase(
            id="demo",
            database_id="db",
            question="question",
            expected_columns=["answer"],
            min_rows=1,
        )
    ]
    calls = []

    def query_fn(question, database_id):
        calls.append((question, database_id))
        return {
            "error": None,
            "sql": "SELECT answer FROM facts",
            "columns": ["answer"],
            "rows": [{"answer": 1}],
        }

    report = ManualRegressionRunner().Run(cases, query_fn)

    assert calls == [("question", "db")]
    assert report["summary"] == {"total": 1, "passed": 1, "pass_rate": 1.0}
