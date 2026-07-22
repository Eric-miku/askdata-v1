"""NL2SQL evaluation metrics and BIRD result comparison helpers."""

from __future__ import annotations

from collections import Counter
import math
import re
from typing import Any, Iterable


def normalize_sql(sql: str) -> str:
    """Normalize SQL for exact-match style comparison."""
    if not sql:
        return ""
    sql = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    sql = " ".join(sql.strip().rstrip(";").split())
    return sql.lower()


def exact_match(pred_sql: str, gold_sql: str) -> bool:
    """Exact Match (EM)."""
    return normalize_sql(pred_sql) == normalize_sql(gold_sql)


def ExactMatch(pred_sql: str, gold_sql: str) -> bool:
    """Backward-compatible PascalCase alias used by the runner/tests."""
    return exact_match(pred_sql, gold_sql)


def exact_match_accuracy(pred_sqls: list[str], gold_sqls: list[str]) -> float:
    if len(pred_sqls) != len(gold_sqls):
        raise ValueError("预测SQL列表和标准SQL列表长度不一致")
    if not pred_sqls:
        return 1.0
    return sum(1 for pred, gold in zip(pred_sqls, gold_sqls) if exact_match(pred, gold)) / len(pred_sqls)


def execution_accuracy(
    pred_result: list[tuple],
    gold_result: list[tuple],
    ignore_order: bool = True,
) -> bool:
    if len(pred_result) != len(gold_result):
        return False
    if ignore_order:
        return Counter(pred_result) == Counter(gold_result)
    return pred_result == gold_result


def execution_accuracy_batch(pred_results: list[list[tuple]], gold_results: list[list[tuple]]) -> float:
    if len(pred_results) != len(gold_results):
        raise ValueError("预测结果列表和标准结果列表长度不一致")
    if not pred_results:
        return 1.0
    return sum(
        1 for pred, gold in zip(pred_results, gold_results) if execution_accuracy(pred, gold)
    ) / len(pred_results)


def validate_sql_structure(pred_sql: str) -> dict:
    """Basic structural safety check used by lightweight evaluation tools."""
    sql_upper = pred_sql.upper()
    dangerous = []
    if "DROP" in sql_upper:
        dangerous.append("DROP")
    if "DELETE" in sql_upper and "WHERE" not in sql_upper:
        dangerous.append("DELETE without WHERE")
    if "UPDATE" in sql_upper and "WHERE" not in sql_upper:
        dangerous.append("UPDATE without WHERE")
    if "TRUNCATE" in sql_upper:
        dangerous.append("TRUNCATE")
    return {
        "valid": not dangerous,
        "has_drop": "DROP" in sql_upper,
        "has_delete": "DELETE" in sql_upper,
        "has_update": "UPDATE" in sql_upper,
        "dangerous_operations": dangerous,
        "warnings": [],
    }


def candidate_hit_rate(candidate_results: list[list[tuple]], gold_result: list[tuple]) -> bool:
    """Return whether any executed candidate result matches the gold result."""
    if not candidate_results:
        return False
    gold_counter = Counter(_CanonicalTuple(row) for row in gold_result)
    return any(Counter(_CanonicalTuple(row) for row in result) == gold_counter for result in candidate_results)


def batch_candidate_hit_rate(
    candidates_results: list[list[list[tuple]]],
    gold_results: list[list[tuple]],
) -> float:
    """Batch candidate hit rate as a 0..1 fraction."""
    if len(candidates_results) != len(gold_results):
        raise ValueError("候选结果列表和标准结果列表长度不一致")
    if not candidates_results:
        return 1.0
    hits = sum(
        1
        for candidate_result, gold_result in zip(candidates_results, gold_results)
        if candidate_hit_rate(candidate_result, gold_result)
    )
    return hits / len(candidates_results)


class BirdResultComparer:
    """Compare BIRD SQL execution outputs with strict and relaxed modes."""

    def Compare(
        self,
        pred_columns: list[str],
        pred_rows: list[dict[str, Any]],
        pred_sql: str,
        gold_columns: list[str],
        gold_rows: list[dict[str, Any]],
        gold_sql: str,
    ) -> dict[str, Any]:
        ignore_order = not _HasOrderBy(gold_sql)
        strict_passed = self._RowsMatchByNames(
            pred_columns,
            pred_rows,
            gold_columns,
            gold_rows,
            ignore_order=ignore_order,
            require_same_columns=True,
        )
        if strict_passed:
            return self.BuildVerdict(True, True, "strict", None)

        relaxed_mode = self._RelaxedMatchMode(
            pred_columns,
            pred_rows,
            gold_columns,
            gold_rows,
            ignore_order=ignore_order,
        )
        if relaxed_mode:
            return self.BuildVerdict(False, True, relaxed_mode, None)

        mismatch_type = "rows_mismatch" if len(pred_rows) == len(gold_rows) else "row_count_mismatch"
        return self.BuildVerdict(False, False, None, mismatch_type)

    def BuildVerdict(
        self,
        strict_passed: bool,
        relaxed_passed: bool,
        match_mode: str | None,
        mismatch_type: str | None,
    ) -> dict[str, Any]:
        return {
            "passed": strict_passed or relaxed_passed,
            "strict_passed": strict_passed,
            "relaxed_passed": relaxed_passed,
            "match_mode": match_mode,
            "mismatch_type": mismatch_type,
        }

    def _RowsMatchByNames(
        self,
        pred_columns: list[str],
        pred_rows: list[dict[str, Any]],
        gold_columns: list[str],
        gold_rows: list[dict[str, Any]],
        ignore_order: bool,
        require_same_columns: bool,
    ) -> bool:
        if require_same_columns and set(pred_columns) != set(gold_columns):
            return False
        if any(column not in pred_columns for column in gold_columns):
            return False
        pred = [_RowTuple(row, gold_columns) for row in pred_rows]
        gold = [_RowTuple(row, gold_columns) for row in gold_rows]
        return _CompareRows(pred, gold, ignore_order)

    def _RelaxedMatchMode(
        self,
        pred_columns: list[str],
        pred_rows: list[dict[str, Any]],
        gold_columns: list[str],
        gold_rows: list[dict[str, Any]],
        ignore_order: bool,
    ) -> str | None:
        if not gold_columns:
            return None

        named_gold_columns = [column for column in gold_columns if column in pred_columns]
        if named_gold_columns and len(named_gold_columns) == len(gold_columns):
            pred = [_RowTuple(row, gold_columns) for row in pred_rows]
            gold = [_RowTuple(row, gold_columns) for row in gold_rows]
            if _CompareRows(pred, gold, ignore_order):
                return "name"

        if len(gold_columns) == 1 and self._SingleGoldValueAppears(pred_rows, gold_rows, gold_columns[0]):
            # Keep a direct one-value projection distinct from a projection
            # that also exposes unrequested helper columns.
            if len(pred_columns) <= len(gold_columns):
                return "single_value"

        if self._GoldValuesAreSubset(pred_rows, gold_rows, gold_columns, ignore_order):
            return "subset"

        return None

    def _SingleGoldValueAppears(
        self,
        pred_rows: list[dict[str, Any]],
        gold_rows: list[dict[str, Any]],
        gold_column: str,
    ) -> bool:
        if len(gold_rows) != 1 or not pred_rows:
            return False
        target = _CanonicalValue(gold_rows[0].get(gold_column))
        return any(target in [_CanonicalValue(value) for value in row.values()] for row in pred_rows)

    def _GoldValuesAreSubset(
        self,
        pred_rows: list[dict[str, Any]],
        gold_rows: list[dict[str, Any]],
        gold_columns: list[str],
        ignore_order: bool,
    ) -> bool:
        if len(pred_rows) != len(gold_rows):
            return False
        pred_value_sets = [Counter(_CanonicalValue(value) for value in row.values()) for row in pred_rows]
        gold_value_sets = [
            Counter(_CanonicalValue(row.get(column)) for column in gold_columns)
            for row in gold_rows
        ]
        if ignore_order:
            unmatched = pred_value_sets.copy()
            for gold_counter in gold_value_sets:
                match_index = next(
                    (
                        index
                        for index, pred_counter in enumerate(unmatched)
                        if _CounterContains(pred_counter, gold_counter)
                    ),
                    None,
                )
                if match_index is None:
                    return False
                unmatched.pop(match_index)
            return True
        return all(_CounterContains(pred, gold) for pred, gold in zip(pred_value_sets, gold_value_sets))


def _HasOrderBy(sql: str) -> bool:
    return bool(re.search(r"\border\s+by\b", sql or "", re.I))


def _RowTuple(row: dict[str, Any], columns: Iterable[str]) -> tuple[Any, ...]:
    return tuple(_CanonicalValue(row.get(column)) for column in columns)


def _CanonicalTuple(row: tuple[Any, ...]) -> tuple[Any, ...]:
    return tuple(_CanonicalValue(value) for value in row)


def _CanonicalValue(value: Any) -> Any:
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        return round(value, 4)
    return value


def _CompareRows(pred: list[tuple[Any, ...]], gold: list[tuple[Any, ...]], ignore_order: bool) -> bool:
    if len(pred) != len(gold):
        return False
    if ignore_order:
        return Counter(pred) == Counter(gold)
    return pred == gold


def _CounterContains(container: Counter, subset: Counter) -> bool:
    return all(container[key] >= count for key, count in subset.items())
