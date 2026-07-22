"""Deterministic chart recommendations for query results."""

from __future__ import annotations

import re
from typing import Any


_DATE_NAME = re.compile(r"date|time|year|month|day|week|quarter|日期|时间|年|月|季度", re.I)
_DATE_VALUE = re.compile(r"^\d{4}[-/]?(?:\d{1,2})?(?:[-/]?\d{1,2})?")
_SHARE_QUESTION = re.compile(r"占比|比例|份额|构成|percentage|ratio|share", re.I)
_MAX_POINTS = 50


def _cell(row: dict[str, Any] | list[Any], column: str, index: int) -> Any:
    return row[index] if isinstance(row, list) else row.get(column)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class ChartRecommender:
    """Build a small, executable-data-free chart specification whitelist."""

    def Recommend(
        self,
        question: str,
        columns: list[str] | None,
        rows: list[dict[str, Any]] | list[list[Any]] | None,
    ) -> dict[str, Any] | None:
        if not columns or not rows or len(rows) == 1:
            return None

        sample = rows[:_MAX_POINTS]
        numeric = [
            (column, index)
            for index, column in enumerate(columns)
            if any(_is_number(_cell(row, column, index)) for row in sample)
        ]
        if not numeric:
            return None

        dimension = next(
            (
                (column, index)
                for index, column in enumerate(columns)
                if all((value := _cell(row, column, index)) is None or not _is_number(value) for row in sample)
            ),
            None,
        )
        if dimension is None:
            return None

        dimension_name, dimension_index = dimension
        metric_name, metric_index = numeric[0]
        categories = [
            str(value if (value := _cell(row, dimension_name, dimension_index)) is not None else "-")
            for row in sample
        ]
        values = [
            _cell(row, metric_name, metric_index)
            if _is_number(_cell(row, metric_name, metric_index))
            else None
            for row in sample
        ]

        temporal = bool(_DATE_NAME.search(dimension_name)) or sum(
            bool(_DATE_VALUE.match(value)) for value in categories
        ) >= max(2, len(categories) // 2)
        if temporal:
            chart_type = "line"
        elif _SHARE_QUESTION.search(question) and len(categories) <= 8:
            chart_type = "pie"
        elif any(len(value) > 12 for value in categories):
            chart_type = "horizontal_bar"
        else:
            chart_type = "bar"

        chart: dict[str, Any] = {
            "type": chart_type,
            "title": f"{metric_name} 按 {dimension_name}",
            "dimension": dimension_name,
            "metric": metric_name,
            "truncated": len(rows) > _MAX_POINTS,
        }
        if chart_type == "pie":
            chart["series"] = [{
                "name": metric_name,
                "data": [
                    {"name": category, "value": value}
                    for category, value in zip(categories, values)
                    if value is not None
                ],
            }]
        elif chart_type == "horizontal_bar":
            chart.update({
                "yAxis": {"type": "category", "data": categories},
                "xAxis": {"type": "value", "name": metric_name},
                "series": [{"name": metric_name, "data": values}],
            })
        else:
            chart.update({
                "xAxis": {"type": "category", "data": categories},
                "yAxis": {"type": "value", "name": metric_name},
                "series": [{"name": metric_name, "data": values}],
            })
        return chart
