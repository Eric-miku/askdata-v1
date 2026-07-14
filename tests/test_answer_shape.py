from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.agent.answer_shape import CheckAnswerShape  # noqa: E402


def test_count_question_warns_when_query_does_not_count():
    warnings = CheckAnswerShape("How many schools are open?", "SELECT name FROM schools WHERE status = 'Open'")

    assert "Question asks for a count, but SQL does not use COUNT." in warnings


def test_count_query_has_no_shape_warning():
    assert CheckAnswerShape("How many schools are open?", "SELECT COUNT(*) FROM schools") == []


def test_ratio_question_warns_when_query_returns_intermediate_values():
    warnings = CheckAnswerShape(
        "What percentage of loans are fully paid?",
        "SELECT SUM(amount), SUM(CASE WHEN status = 'A' THEN amount ELSE 0 END) FROM loan",
    )

    assert "Question asks for one final computed value, but SQL selects multiple expressions." in warnings


def test_percentage_question_warns_when_query_returns_one_raw_column():
    warnings = CheckAnswerShape(
        "What percentage of accounts are active?",
        "SELECT status FROM account WHERE status = 'active'",
    )

    assert "Question asks for a computed percentage, ratio, or rate, but SQL returns a raw value." in warnings


def test_list_question_warns_for_aggregate_only_query():
    warnings = CheckAnswerShape("List the school names", "SELECT COUNT(*) FROM schools")

    assert "Question asks for a list, but SQL returns only aggregate expressions." in warnings


def test_top_entity_warns_when_helper_columns_are_returned():
    warnings = CheckAnswerShape(
        "Which team has the most wins?",
        "SELECT team_name, COUNT(*) AS wins FROM matches GROUP BY team_name ORDER BY wins DESC LIMIT 1",
    )

    assert "Question asks for the top entity only; remove unrequested helper columns." in warnings


def test_invalid_sql_returns_parse_warning_instead_of_raising():
    warnings = CheckAnswerShape("List schools", "SELECT FROM")

    assert warnings == ["SQL could not be parsed for answer-shape review."]
