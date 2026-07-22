from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.eval.metrics import BirdResultComparer, ExactMatch


def test_result_comparer_treats_unordered_duplicate_rows_as_equal():
    comparer = BirdResultComparer()

    result = comparer.Compare(
        ["name", "score"],
        [{"name": "A", "score": 1}, {"name": "B", "score": 2}, {"name": "A", "score": 1}],
        "select name, score from t",
        ["score", "name"],
        [{"score": 2, "name": "B"}, {"score": 1, "name": "A"}, {"score": 1, "name": "A"}],
        "select score, name from t",
    )

    assert result["passed"] is True
    assert result["mismatch_type"] is None


def test_result_comparer_ignores_generated_order_when_gold_has_no_order_by():
    comparer = BirdResultComparer()

    result = comparer.Compare(
        ["name"],
        [{"name": "A"}, {"name": "B"}],
        "select name from t order by name",
        ["name"],
        [{"name": "B"}, {"name": "A"}],
        "select name from t",
    )

    assert result["passed"] is True


def test_result_comparer_normalizes_float_and_null_values():
    comparer = BirdResultComparer()

    result = comparer.Compare(
        ["ratio", "note"],
        [{"ratio": 1.00000001, "note": None}],
        "select ratio, note from t",
        ["ratio", "note"],
        [{"ratio": 1.0, "note": None}],
        "select ratio, note from t",
    )

    assert result["passed"] is True


def test_result_comparer_reports_relaxed_pass_for_computed_alias_by_position():
    comparer = BirdResultComparer()

    result = comparer.Compare(
        ["avg_monthly_consumption"],
        [{"avg_monthly_consumption": 10.0}],
        "select avg(consumption) / 12 as avg_monthly_consumption from t",
        ["AVG(T2.Consumption) / 12"],
        [{"AVG(T2.Consumption) / 12": 10}],
        "select avg(T2.Consumption) / 12 from t",
    )

    assert result["passed"] is True
    assert result["strict_passed"] is False
    assert result["relaxed_passed"] is True
    assert result["match_mode"] == "single_value"


def test_result_comparer_finds_gold_values_when_generated_has_extra_columns_first():
    comparer = BirdResultComparer()

    result = comparer.Compare(
        ["total_consumption", "avg_monthly_consumption"],
        [{"total_consumption": 120, "avg_monthly_consumption": 10}],
        "select sum(consumption), avg(consumption) / 12 from t",
        ["AVG(T2.Consumption) / 12"],
        [{"AVG(T2.Consumption) / 12": 10}],
        "select avg(T2.Consumption) / 12 from t",
    )

    assert result["passed"] is True
    assert result["strict_passed"] is False
    assert result["relaxed_passed"] is True
    assert result["match_mode"] == "subset"


def test_result_comparer_accepts_single_count_alias_by_value():
    comparer = BirdResultComparer()

    result = comparer.Compare(
        ["user_count"],
        [{"user_count": 4941}],
        "select count(*) as user_count from users where LastAccessDate > '2014-09-01'",
        ["COUNT(Id)"],
        [{"COUNT(Id)": 4941}],
        "select count(Id) from users where date(LastAccessDate) > '2014-09-01'",
    )

    assert result["passed"] is True
    assert result["match_mode"] == "single_value"


def test_result_comparer_accepts_gold_column_subset_when_generated_has_helper_count():
    comparer = BirdResultComparer()

    result = comparer.Compare(
        ["team_long_name", "wins"],
        [{"team_long_name": "Rangers", "wins": 11}],
        "select team_long_name, count(*) as wins from match group by team_long_name order by wins desc limit 1",
        ["team_long_name"],
        [{"team_long_name": "Rangers"}],
        "select team_long_name from match order by wins desc limit 1",
    )

    assert result["passed"] is True
    assert result["match_mode"] in {"name", "subset"}


def test_result_comparer_allows_small_float_rounding_difference():
    comparer = BirdResultComparer()

    result = comparer.Compare(
        ["eligible_rate"],
        [{"eligible_rate": 0.9179}, {"eligible_rate": 0.9993}],
        "select round(frpm_count / enrollment, 4) as eligible_rate from t",
        ["CAST(frpm_count AS REAL) / enrollment"],
        [{"CAST(frpm_count AS REAL) / enrollment": 0.9179476526796843}, {"CAST(frpm_count AS REAL) / enrollment": 0.9993290841999329}],
        "select cast(frpm_count as real) / enrollment from t",
    )

    assert result["passed"] is True


def test_result_comparer_preserves_order_when_gold_has_order_by():
    comparer = BirdResultComparer()

    result = comparer.Compare(
        ["name"],
        [{"name": "A"}, {"name": "B"}],
        "select name from t",
        ["name"],
        [{"name": "B"}, {"name": "A"}],
        "select name from t order by name desc",
    )

    assert result["passed"] is False
    assert result["mismatch_type"] == "rows_mismatch"


def test_result_comparer_does_not_pass_when_only_one_gold_column_name_matches():
    comparer = BirdResultComparer()

    result = comparer.Compare(
        ["id", "wrong_name"],
        [{"id": 1, "wrong_name": "bad"}],
        "select id, wrong_name from t",
        ["id", "name"],
        [{"id": 1, "name": "good"}],
        "select id, name from t",
    )

    assert result["passed"] is False
    assert result["relaxed_passed"] is False


def test_exact_match_normalizes_whitespace_case_and_semicolon():
    assert ExactMatch(" SELECT  *  FROM schools; ", "select * from schools") is True
