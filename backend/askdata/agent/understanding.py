"""Deterministic structured understanding and multi-turn context merging."""

from __future__ import annotations

import re
from typing import Any


_TIME_PATTERNS = (
    "今天", "昨天", "本周", "上周", "本月", "上个月", "本季度", "上季度",
    "今年", "去年", "近三个月", "近半年", "近一年",
)
_METRIC_HINTS = ("金额", "销售额", "合同额", "收入", "营收", "利润", "数量", "客户数", "增长率", "占比", "平均")
_DIMENSION_RE = re.compile(r"按\s*([^，。；,;]+?)(?:查看|统计|分组|对比|展示|$)")
_TOP_RE = re.compile(r"(?:前\s*|top\s*)(\d+)", re.I)
_ONLY_RE = re.compile(r"只(?:看|要|统计)\s*([^，。；,;]+)")
_CLEAR_FILTER_RE = re.compile(r"不限制\s*([^，。；,;]+)")
_CLEAR_DIMENSION_RE = re.compile(r"(?:取消|不要)\s*([^，。；,;]*?)(?:分组|维度)")


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class QuestionUnderstanding:
    """Extract a conservative intent shape and merge explicit follow-up changes."""

    def Parse(self, question: str) -> dict[str, Any]:
        text = question.strip()
        dimensions = _unique(_DIMENSION_RE.findall(text))
        if not dimensions:
            match = re.search(r"各\s*([^，。；,;的]{1,12})(?:的)?", text)
            if match:
                dimensions = [match.group(1)]

        top_match = _TOP_RE.search(text)
        filters = [{"expression": value, "source": "explicit"} for value in _ONLY_RE.findall(text)]
        time_range = next((value for value in _TIME_PATTERNS if value in text), None)
        metrics = _unique([hint for hint in _METRIC_HINTS if hint in text])
        return {
            "query_object": text,
            "metrics": metrics,
            "dimensions": dimensions,
            "filters": filters,
            "time_range": time_range,
            "sort": "desc" if re.search(r"最高|最多|排名|前\s*\d+|top\s*\d+", text, re.I) else None,
            "top_n": int(top_match.group(1)) if top_match else None,
            "cleared_filters": _unique(_CLEAR_FILTER_RE.findall(text)),
            "clear_dimensions": bool(_CLEAR_DIMENSION_RE.search(text)),
        }

    def Resolve(self, question: str, previous: dict[str, Any] | None = None) -> dict[str, Any]:
        current = self.Parse(question)
        if not previous:
            return current

        resolved = {
            "query_object": question.strip(),
            "metrics": current["metrics"] or list(previous.get("metrics", [])),
            "dimensions": current["dimensions"] or list(previous.get("dimensions", [])),
            "filters": list(previous.get("filters", [])),
            "time_range": current["time_range"] or previous.get("time_range"),
            "sort": current["sort"] if current["sort"] is not None else previous.get("sort"),
            "top_n": current["top_n"] if current["top_n"] is not None else previous.get("top_n"),
            "cleared_filters": current["cleared_filters"],
            "clear_dimensions": current["clear_dimensions"],
        }
        if current["clear_dimensions"]:
            resolved["dimensions"] = []
        if current["cleared_filters"]:
            cleared = current["cleared_filters"]
            resolved["filters"] = [
                item for item in resolved["filters"]
                if not any(value in str(item.get("expression", "")) for value in cleared)
            ]
        if current["filters"]:
            resolved["filters"] = current["filters"]
        return resolved
