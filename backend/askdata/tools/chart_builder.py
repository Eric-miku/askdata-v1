"""Deterministic, data-driven chart selection for verified SQL results."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import math
import re
from typing import Any, Mapping, Sequence

from askdata.agent.intent import IntentContract
from askdata.api.response_models import ChartSpec


_PROPORTION_WORDS = re.compile(r"\b(share|proportion|percentage|percent)\b", re.I)
_ISO_DATE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[T ][0-2]\d:[0-5]\d(?::[0-5]\d(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$"
)


class ChartBuilder:
    """Build a validated product-level ChartSpec, or choose table-only output."""

    def Build(
        self,
        question: str,
        intent: IntentContract,
        columns: Sequence[str],
        rows: Sequence[Mapping[str, Any]],
    ) -> ChartSpec | None:
        trusted_columns = self._TrustedColumns(columns)
        trusted_rows = [row for row in rows if isinstance(row, Mapping)]
        if not trusted_columns or not trusted_rows:
            return None

        time_fields = [
            field
            for field in trusted_columns
            if self._IsTimeField(field, trusted_rows)
        ]
        numeric_fields = [
            field
            for field in trusted_columns
            if self._IsNumericField(field, trusted_rows)
        ]
        category_fields = [
            field
            for field in trusted_columns
            if field not in numeric_fields and field not in time_fields
        ]

        # This order is product policy. Keep it explicit rather than scoring charts.
        if time_fields and numeric_fields and len(trusted_rows) >= 2:
            return self._Spec(
                chart_type="line",
                reason="time_series",
                category=time_fields[0],
                values=numeric_fields,
            )

        if intent.shape == "ranking" and category_fields and numeric_fields:
            return self._Spec(
                chart_type="horizontal_bar",
                reason="ranking",
                category=category_fields[0],
                values=[numeric_fields[0]],
            )

        if _PROPORTION_WORDS.search(question):
            if (
                category_fields
                and numeric_fields
                and 1 <= len(trusted_rows) <= 6
                and self._AllNonnegative(numeric_fields[0], trusted_rows)
            ):
                return self._Spec(
                    chart_type="pie",
                    reason="proportion",
                    category=category_fields[0],
                    values=[numeric_fields[0]],
                )
            return None

        if len(numeric_fields) >= 2 and len(trusted_rows) >= 5:
            return self._Spec(
                chart_type="scatter",
                reason="correlation",
                category=None,
                values=numeric_fields[:2],
            )

        if category_fields and numeric_fields and len(trusted_rows) >= 2:
            return self._Spec(
                chart_type="vertical_bar",
                reason="comparison",
                category=category_fields[0],
                values=numeric_fields,
            )

        return None

    @staticmethod
    def _TrustedColumns(columns: Sequence[str]) -> list[str]:
        result: list[str] = []
        for column in columns:
            if isinstance(column, str) and column and column not in result:
                result.append(column)
        return result

    @classmethod
    def _IsNumericField(
        cls, field: str, rows: Sequence[Mapping[str, Any]]
    ) -> bool:
        values = cls._PresentValues(field, rows)
        return bool(values) and all(cls._IsFiniteNumber(value) for value in values)

    @classmethod
    def _IsTimeField(cls, field: str, rows: Sequence[Mapping[str, Any]]) -> bool:
        values = cls._PresentValues(field, rows)
        return bool(values) and all(cls._IsTimeValue(value) for value in values)

    @staticmethod
    def _PresentValues(
        field: str, rows: Sequence[Mapping[str, Any]]
    ) -> list[Any]:
        return [row[field] for row in rows if field in row and row[field] is not None]

    @staticmethod
    def _IsFiniteNumber(value: Any) -> bool:
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            return False
        try:
            return math.isfinite(float(value))
        except (OverflowError, TypeError, ValueError):
            return False

    @staticmethod
    def _IsTimeValue(value: Any) -> bool:
        if isinstance(value, (date, datetime)):
            return True
        return isinstance(value, str) and bool(_ISO_DATE.fullmatch(value.strip()))

    @classmethod
    def _AllNonnegative(
        cls, field: str, rows: Sequence[Mapping[str, Any]]
    ) -> bool:
        values = cls._PresentValues(field, rows)
        return bool(values) and all(cls._IsFiniteNumber(value) and value >= 0 for value in values)

    @staticmethod
    def _Spec(
        *,
        chart_type: str,
        reason: str,
        category: str | None,
        values: list[str],
    ) -> ChartSpec:
        label = category or values[0]
        return ChartSpec(
            type=chart_type,
            title=f"{reason.replace('_', ' ').title()} by {label}",
            category_field=category,
            category_label=category,
            value_fields=values,
            value_labels={field: field for field in values},
            reason=reason,
        )
