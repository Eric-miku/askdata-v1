from __future__ import annotations

import csv
import io
import zipfile

from askdata.tools.analysis import StructuredAnalyzer
from askdata.tools.exporter import BuildCsv, BuildXlsx
from askdata.tools.visualization import ChartRecommender


def test_chart_recommender_covers_line_pie_and_horizontal_bar():
    recommender = ChartRecommender()
    rows = [{"month": "2026-01", "sales": 10}, {"month": "2026-02", "sales": 15}]
    assert recommender.Recommend("销售趋势", ["month", "sales"], rows)["type"] == "line"

    share_rows = [{"region": "华东", "sales": 60}, {"region": "华南", "sales": 40}]
    assert recommender.Recommend("各地区销售占比", ["region", "sales"], share_rows)["type"] == "pie"

    long_rows = [{"product": "一个名称非常长的企业级产品", "sales": 10}, {"product": "短产品", "sales": 8}]
    assert recommender.Recommend("产品销售", ["product", "sales"], long_rows)["type"] == "horizontal_bar"


def test_single_value_and_text_only_results_do_not_force_a_chart():
    recommender = ChartRecommender()
    assert recommender.Recommend("总数", ["count"], [{"count": 3}]) is None
    assert recommender.Recommend("名称", ["name"], [{"name": "A"}, {"name": "B"}]) is None


def test_structured_analysis_has_traceable_evidence_and_iqr_anomaly():
    rows = [{"month": f"2026-{index + 1:02d}", "sales": value} for index, value in enumerate([10, 11, 9, 10, 100])]
    result = StructuredAnalyzer().Analyze(["month", "sales"], rows)
    assert result["summary"].startswith("查询返回 5 行")
    anomaly = next(item for item in result["insights"] if item["type"] == "anomaly" and item["title"].startswith("sales"))
    assert anomaly["method"] == "IQR"
    assert anomaly["evidence"][0] == {"column": "sales", "row_index": 4, "value": 100}


def test_structured_analysis_computes_share_period_change_yoy_and_forecast():
    rows = [
        {"month": "2025-01", "sales": 100},
        {"month": "2025-02", "sales": 110},
        {"month": "2025-03", "sales": 120},
        {"month": "2026-01", "sales": 130},
    ]
    insights = StructuredAnalyzer().Analyze(["month", "sales"], rows)["insights"]
    types = {item["type"] for item in insights}
    assert {"share", "period_change", "year_over_year", "forecast"}.issubset(types)
    yoy = next(item for item in insights if item["type"] == "year_over_year")
    assert yoy["calculation"]["growth_rate_percent"] == 30
    forecast = next(item for item in insights if item["type"] == "forecast")
    assert forecast["training_window"] == 4
    assert len(forecast["confidence_interval"]) == 2


def test_identifier_and_time_columns_are_not_analyzed_as_metrics():
    rows = [{"year": 2025, "customer_id": 1, "sales": 10}, {"year": 2026, "customer_id": 2, "sales": 20}]
    insights = StructuredAnalyzer().Analyze(["year", "customer_id", "sales"], rows)["insights"]
    assert all(not item["title"].startswith(("year", "customer_id")) for item in insights)


def test_exports_escape_excel_formulas_and_make_a_valid_xlsx_package():
    columns = ["name", "amount"]
    rows = [{"name": "=HYPERLINK(\"bad\")", "amount": 12}]
    csv_data = BuildCsv(columns, rows).decode("utf-8-sig")
    parsed = list(csv.reader(io.StringIO(csv_data)))
    assert parsed[1][0].startswith("'=")

    xlsx = BuildXlsx("问题", "SELECT name, amount FROM sales", "demo", columns, rows)
    with zipfile.ZipFile(io.BytesIO(xlsx)) as archive:
        assert "xl/worksheets/sheet1.xml" in archive.namelist()
        sheet = archive.read("xl/worksheets/sheet1.xml").decode()
        assert "'=HYPERLINK" in sheet
