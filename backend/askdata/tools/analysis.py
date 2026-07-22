"""Evidence-backed deterministic analysis and follow-up suggestions."""

from __future__ import annotations

import math
import re
from statistics import median
from typing import Any


_TIME_NAME = re.compile(r"date|time|year|month|day|week|quarter|日期|时间|年份|月份|年|月|季度", re.I)
_NON_METRIC_NAME = re.compile(r"(^|_)(id|key|rank|index|year|month|day)($|_)|编号|序号|排名|年份|月份", re.I)
_DATE_PARTS = re.compile(r"(?P<year>20\d{2})(?:[-/年](?P<month>\d{1,2}))?")


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _time_key(value: Any) -> tuple[int, int, str] | None:
    text = str(value)
    match = _DATE_PARTS.search(text)
    if not match:
        return None
    return int(match.group("year")), int(match.group("month") or 1), text


def _evidence(column: str, row_index: int, value: Any) -> dict[str, Any]:
    return {"column": column, "row_index": row_index, "value": value}


class StructuredAnalyzer:
    def Analyze(self, columns: list[str] | None, rows: list[dict[str, Any]] | None) -> dict[str, Any]:
        if not columns or not rows:
            return {"summary": "没有可分析的数据。", "insights": [], "insufficient_reason": "查询结果为空"}

        temporal_column = self._TemporalColumn(columns, rows)
        numeric_columns = [
            column for column in columns
            if column != temporal_column
            and not _NON_METRIC_NAME.search(column)
            and any(_number(row.get(column)) is not None for row in rows)
        ]
        dimension = next((column for column in columns if column not in numeric_columns), None)
        insights: list[dict[str, Any]] = []
        for column in numeric_columns[:3]:
            points = [(index, row.get(column), _number(row.get(column))) for index, row in enumerate(rows)]
            points = [point for point in points if point[2] is not None]
            if not points:
                continue
            maximum = max(points, key=lambda point: point[2])
            minimum = min(points, key=lambda point: point[2])
            insights.append({
                "type": "range",
                "title": f"{column} 范围",
                "statement": f"最高值为 {maximum[1]}，最低值为 {minimum[1]}。",
                "evidence": [_evidence(column, maximum[0], maximum[1]), _evidence(column, minimum[0], minimum[1])],
            })
            if dimension:
                insights.append({
                    "type": "ranking",
                    "title": f"{column} Top/Bottom",
                    "statement": f"最高项为 {rows[maximum[0]].get(dimension)}，最低项为 {rows[minimum[0]].get(dimension)}。",
                    "evidence": [
                        _evidence(dimension, maximum[0], rows[maximum[0]].get(dimension)),
                        _evidence(column, maximum[0], maximum[1]),
                        _evidence(dimension, minimum[0], rows[minimum[0]].get(dimension)),
                        _evidence(column, minimum[0], minimum[1]),
                    ],
                })

            values = [point[2] for point in points]
            if len(values) >= 4:
                ordered = sorted(values)
                midpoint = len(ordered) // 2
                lower = ordered[: midpoint + (len(ordered) % 2)]
                upper = ordered[midpoint:]
                q1, q3 = median(lower), median(upper)
                low, high = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
                anomalies = [point for point in points if point[2] < low or point[2] > high]
                if anomalies:
                    insights.append({
                        "type": "anomaly",
                        "title": f"{column} 异常候选",
                        "statement": f"按 IQR 规则识别到 {len(anomalies)} 个异常候选，原因需要结合业务进一步验证。",
                        "method": "IQR",
                        "evidence": [_evidence(column, point[0], point[1]) for point in anomalies[:5]],
                    })

            total = sum(values)
            if dimension and total > 0 and all(value >= 0 for value in values):
                top_shares = sorted(points, key=lambda point: point[2], reverse=True)[:5]
                insights.append({
                    "type": "share",
                    "title": f"{column} 结构占比",
                    "statement": "；".join(
                        f"{rows[point[0]].get(dimension)}占{point[2] * 100 / total:.2f}%" for point in top_shares
                    ),
                    "evidence": [_evidence(column, point[0], point[1]) for point in top_shares],
                    "calculation": {"total": total, "formula": "value / total * 100"},
                })

            if temporal_column:
                timed = [
                    (index, _time_key(row.get(temporal_column)), _number(row.get(column)))
                    for index, row in enumerate(rows)
                ]
                timed = sorted((item for item in timed if item[1] and item[2] is not None), key=lambda item: item[1])
                if len(timed) >= 2:
                    previous, latest = timed[-2], timed[-1]
                    delta = latest[2] - previous[2]
                    rate = None if previous[2] == 0 else delta * 100 / previous[2]
                    insights.append({
                        "type": "period_change",
                        "title": f"{column} 环比/相邻周期变化",
                        "statement": f"最近两个周期变化量为 {delta:g}" + (f"，变化率为 {rate:.2f}%" if rate is not None else "，上期为零无法计算变化率") + "。",
                        "evidence": [_evidence(column, previous[0], previous[2]), _evidence(column, latest[0], latest[2])],
                        "calculation": {"difference": delta, "growth_rate_percent": rate},
                    })
                    prior_year = next((item for item in reversed(timed[:-1]) if item[1][:2] == (latest[1][0] - 1, latest[1][1])), None)
                    if prior_year:
                        yoy_delta = latest[2] - prior_year[2]
                        yoy_rate = None if prior_year[2] == 0 else yoy_delta * 100 / prior_year[2]
                        insights.append({
                            "type": "year_over_year",
                            "title": f"{column} 同比",
                            "statement": f"同比变化量为 {yoy_delta:g}" + (f"，同比为 {yoy_rate:.2f}%" if yoy_rate is not None else "，去年同期为零无法计算同比") + "。",
                            "evidence": [_evidence(column, prior_year[0], prior_year[2]), _evidence(column, latest[0], latest[2])],
                            "calculation": {"difference": yoy_delta, "growth_rate_percent": yoy_rate},
                        })
                forecast = self._Forecast(timed, column)
                if forecast:
                    insights.append(forecast)

        return {
            "summary": f"查询返回 {len(rows)} 行、{len(columns)} 列，生成 {len(insights)} 条可复核结论。",
            "insights": insights,
            "insufficient_reason": None if insights else "结果中没有可计算的业务数值列",
        }

    def _TemporalColumn(self, columns: list[str], rows: list[dict[str, Any]]) -> str | None:
        for column in columns:
            values = [row.get(column) for row in rows[:50] if row.get(column) is not None]
            if _TIME_NAME.search(column) or (values and sum(_time_key(value) is not None for value in values) >= max(2, len(values) // 2)):
                return column
        return None

    def _Forecast(self, timed: list[tuple[int, tuple[int, int, str], float]], column: str) -> dict[str, Any] | None:
        if len(timed) < 4:
            return None
        values = [item[2] for item in timed]
        count = len(values)
        mean_x = (count - 1) / 2
        mean_y = sum(values) / count
        denominator = sum((index - mean_x) ** 2 for index in range(count))
        slope = sum((index - mean_x) * (value - mean_y) for index, value in enumerate(values)) / denominator
        intercept = mean_y - slope * mean_x
        predicted = intercept + slope * count
        residuals = [value - (intercept + slope * index) for index, value in enumerate(values)]
        standard_error = math.sqrt(sum(value * value for value in residuals) / max(1, count - 2))
        margin = 1.96 * standard_error
        return {
            "type": "forecast",
            "title": f"{column} 下一周期预测",
            "statement": f"线性趋势预测值为 {predicted:.2f}，95%参考区间为 [{predicted - margin:.2f}, {predicted + margin:.2f}]。预测不是历史事实。",
            "method": "ordinary_least_squares",
            "training_window": count,
            "forecast": predicted,
            "confidence_interval": [predicted - margin, predicted + margin],
            "evidence": [_evidence(column, item[0], item[2]) for item in timed],
        }

    def Suggest(self, question: str, columns: list[str] | None, rows: list[dict[str, Any]] | None) -> list[str]:
        if not columns or not rows:
            return []
        temporal = self._TemporalColumn(columns, rows)
        numeric = next((column for column in columns if column != temporal and not _NON_METRIC_NAME.search(column) and any(_number(row.get(column)) is not None for row in rows)), None)
        dimension = next((column for column in columns if column not in (numeric, temporal)), temporal)
        if not numeric:
            return []
        candidates = [f"{numeric}的最高和最低值分别是什么？"]
        if dimension:
            candidates.extend([f"按{dimension}比较{numeric}的差异", f"{numeric}在不同{dimension}中的占比是多少？"])
        if temporal:
            candidates.append(f"{numeric}的同比和环比变化如何？")
        return [candidate for candidate in candidates if candidate.strip().lower() != question.strip().lower()][:3]
