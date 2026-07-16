from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.agent.question_analyzer import QuestionAnalyzer


def test_analyzer_matches_plural_question_to_singular_schema_column():
    analysis = QuestionAnalyzer().Analyze(
        "What are the elements of the toxicology and label of molecule TR060?",
        {"atom": ["element"], "molecule": ["label", "molecule_id"]},
        "TR060 is the molecule id;",
    )

    assert analysis.intent.shape == "listing"
    assert analysis.intent.output_attributes == ["element", "label"]
    assert analysis.requested_outputs == ["element", "label"]


def test_analyzer_extracts_literals_and_formula_hints():
    analysis = QuestionAnalyzer().Analyze(
        "For the customer who paid 634.8 in 2012/8/25, what was the consumption decrease rate from Year 2012 to 2013?",
        {
            "transactions_1k": ["Date", "Price", "CustomerID"],
            "yearmonth": ["Date", "Consumption", "CustomerID"],
        },
        "'2012/8/24' can be represented by '2012-08-24'; Consumption decrease rate = (consumption_2012 - consumption_2013) / consumption_2012",
    )

    assert analysis.intent.shape == "ratio"
    assert analysis.intent.metrics == ["ratio"]
    assert (
        "Consumption decrease rate = (consumption_2012 - consumption_2013) / consumption_2012"
    ) in analysis.formula_hints
    assert {item.raw for item in analysis.filters} >= {"634.8", "2012/8/25"}
    assert any(item.normalized == "2012-08-25" for item in analysis.filters)


def test_analyzer_keeps_invalid_slash_date_without_crashing():
    analysis = QuestionAnalyzer().Analyze(
        "Which orders were placed on 2012/99/25?",
        {"orders": ["order_id", "order_date"]},
    )

    assert any(
        item.raw == "2012/99/25" and item.kind == "date" and item.normalized is None
        for item in analysis.filters
    )


def test_analyzer_ignores_question_words_but_keeps_useful_text_literals():
    analysis = QuestionAnalyzer().Analyze(
        "What/List/Which customers visited Monterey?",
        {"customers": ["customer_name", "city"]},
    )

    text_filters = {item.raw for item in analysis.filters if item.kind == "text"}

    assert text_filters == {"Monterey"}
