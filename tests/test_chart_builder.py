from datetime import date
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.agent.intent import IntentContract
from askdata.analysis.chart_builder import ChartBuilder


def test_time_series_has_priority_over_ranking():
    spec = ChartBuilder().Build(
        question="top daily revenue",
        intent=IntentContract(shape="ranking", expected_max_rows=5),
        columns=["day", "revenue"],
        rows=[
            {"day": "2026-07-14", "revenue": 10},
            {"day": date(2026, 7, 15), "revenue": 12},
        ],
    )

    assert spec is not None
    assert spec.type == "line"
    assert spec.reason == "time_series"
    assert spec.category_field == "day"
    assert spec.value_fields == ["revenue"]


def test_ranking_builds_horizontal_bar():
    spec = ChartBuilder().Build(
        question="top five schools by enrollment",
        intent=IntentContract(shape="ranking", expected_max_rows=5),
        columns=["School", "Enrollment"],
        rows=[{"School": "A", "Enrollment": 10}],
    )

    assert spec is not None
    assert spec.type == "horizontal_bar"
    assert spec.reason == "ranking"
    assert spec.category_field == "School"
    assert spec.value_fields == ["Enrollment"]


def test_explicit_proportion_builds_pie_for_nonnegative_categories():
    spec = ChartBuilder().Build(
        "market share by category",
        IntentContract(shape="ratio", grouping=["category"]),
        ["category", "share"],
        [
            {"category": "A", "share": 0.6},
            {"category": "B", "share": 0.4},
        ],
    )

    assert spec is not None
    assert spec.type == "pie"
    assert spec.reason == "proportion"
    assert spec.category_field == "category"
    assert spec.value_fields == ["share"]


def test_share_with_too_many_categories_stays_table_only():
    rows = [{"category": str(index), "share": index} for index in range(7)]

    assert ChartBuilder().Build(
        "share by category",
        IntentContract(shape="ratio", grouping=["category"]),
        ["category", "share"],
        rows,
    ) is None


def test_proportion_with_negative_value_stays_table_only():
    rows = [
        {"category": "A", "share": 1.1},
        {"category": "B", "share": -0.1},
    ]

    assert ChartBuilder().Build(
        "share by category",
        IntentContract(shape="ratio", grouping=["category"]),
        ["category", "share"],
        rows,
    ) is None


def test_two_numeric_measures_build_scatter_with_at_least_five_rows():
    rows = [
        {"height": index, "weight": index * 2, "name": f"P{index}"}
        for index in range(5)
    ]

    spec = ChartBuilder().Build(
        "relationship between height and weight",
        IntentContract(shape="comparison", metrics=["height", "weight"]),
        ["name", "height", "weight"],
        rows,
    )

    assert spec is not None
    assert spec.type == "scatter"
    assert spec.reason == "correlation"
    assert spec.category_field is None
    assert spec.value_fields == ["height", "weight"]


def test_scatter_requires_five_rows():
    rows = [{"x": index, "y": index * 2} for index in range(4)]

    assert ChartBuilder().Build(
        "relationship between x and y",
        IntentContract(shape="comparison", metrics=["x", "y"]),
        ["x", "y"],
        rows,
    ) is None


def test_category_comparison_builds_vertical_bar():
    spec = ChartBuilder().Build(
        "compare revenue by region",
        IntentContract(shape="comparison", grouping=["region"], metrics=["revenue"]),
        ["region", "revenue"],
        [
            {"region": "East", "revenue": 10},
            {"region": "West", "revenue": 12},
        ],
    )

    assert spec is not None
    assert spec.type == "vertical_bar"
    assert spec.reason == "comparison"
    assert spec.category_field == "region"
    assert spec.value_fields == ["revenue"]


def test_unsuitable_result_stays_table_only():
    assert ChartBuilder().Build(
        "list school names",
        IntentContract(shape="listing", output_attributes=["School"]),
        ["School"],
        [{"School": "A"}, {"School": "B"}],
    ) is None


def test_chart_references_only_returned_columns_and_has_no_raw_options():
    spec = ChartBuilder().Build(
        question="top schools",
        intent=IntentContract(shape="ranking", expected_max_rows=5),
        columns=["School", "Enrollment"],
        rows=[
            {
                "School": "A",
                "Enrollment": 10,
                "formatter": "function () { return secret; }",
            }
        ],
    )

    assert spec is not None
    payload = spec.model_dump(mode="json")
    assert spec.category_field in {"School", "Enrollment"}
    assert set(spec.value_fields) <= {"School", "Enrollment"}
    assert "formatter" not in payload
    assert "options" not in payload
    assert "echarts" not in payload
