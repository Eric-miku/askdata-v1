"""Evaluation metrics — BirdResultComparer with 4-tier column matching (strict/name/position/subset), plus ExactMatch, ExecutionAccuracy, and SQL structure validation utilities."""

from collections import Counter
from difflib import SequenceMatcher
from itertools import combinations
import re
from typing import Any, List, Tuple


def NormalizeSql(sql: str) -> str:
    sql = re.sub(r"--.*$", "", sql or "", flags=re.MULTILINE)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    return re.sub(r"\s+", " ", sql.strip().rstrip(";")).lower()


def ExactMatch(pred_sql: str, gold_sql: str) -> bool:
    return NormalizeSql(pred_sql) == NormalizeSql(gold_sql)


def ExactMatchAccuracy(pred_sqls: List[str], gold_sqls: List[str]) -> float:
    if len(pred_sqls) != len(gold_sqls):
        raise ValueError("预测SQL列表和标准SQL列表长度不一致")
    if not pred_sqls:
        return 100.0
    return sum(1 for pred, gold in zip(pred_sqls, gold_sqls) if ExactMatch(pred, gold)) / len(pred_sqls) * 100


def _NormalizeTupleValue(value):
    if value is None:
        return ("null", None)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("number", float(value))
    if isinstance(value, float):
        return ("number", round(value, 4))
    text = str(value).strip()
    try:
        return ("number", round(float(text), 4))
    except ValueError:
        return ("text", text)


def _NormalizeTupleRow(row: Tuple) -> Tuple:
    return tuple(_NormalizeTupleValue(value) for value in row)


def ExecutionAccuracy(pred_result: List[Tuple], gold_result: List[Tuple], ignore_order: bool = True) -> bool:
    pred = [_NormalizeTupleRow(row) for row in pred_result]
    gold = [_NormalizeTupleRow(row) for row in gold_result]
    if ignore_order:
        return Counter(pred) == Counter(gold)
    return pred == gold


def ExecutionAccuracyBatch(pred_results: List[List[Tuple]], gold_results: List[List[Tuple]]) -> float:
    if len(pred_results) != len(gold_results):
        raise ValueError("预测结果列表和标准结果列表长度不一致")
    if not pred_results:
        return 100.0
    return sum(1 for pred, gold in zip(pred_results, gold_results) if ExecutionAccuracy(pred, gold)) / len(pred_results) * 100


def SqlSimilarity(pred_sql: str, gold_sql: str) -> float:
    return SequenceMatcher(None, NormalizeSql(pred_sql), NormalizeSql(gold_sql)).ratio()


def ValidateSqlStructure(pred_sql: str) -> dict:
    sql = pred_sql or ""
    dangerous_patterns = {
        "DROP": r"\bdrop\b",
        "TRUNCATE": r"\btruncate\b",
        "DELETE without WHERE": r"\bdelete\b(?![\s\S]*\bwhere\b)",
        "UPDATE without WHERE": r"\bupdate\b(?![\s\S]*\bwhere\b)",
    }
    dangerous = [label for label, pattern in dangerous_patterns.items() if re.search(pattern, sql, re.I)]
    return {
        "valid": not dangerous,
        "has_drop": bool(re.search(r"\bdrop\b", sql, re.I)),
        "has_delete": bool(re.search(r"\bdelete\b", sql, re.I)),
        "has_update": bool(re.search(r"\bupdate\b", sql, re.I)),
        "dangerous_operations": dangerous,
        "warnings": [],
    }


class BirdResultComparer:
    """Compares generated SQL results with gold SQL results."""

    def Compare(
        self,
        generated_columns: list[str],
        generated_rows: list[dict],
        generated_sql: str,
        gold_columns: list[str],
        gold_rows: list[dict],
        gold_sql: str,
    ) -> dict[str, Any]:
        if not gold_columns:
            passed = len(generated_rows) == 0
            return self.BuildVerdict(passed, passed, "strict" if passed else None, None if passed else "rows_mismatch")

        strict_passed = False
        if {str(column).lower() for column in generated_columns or []} == {str(column).lower() for column in gold_columns}:
            strict_passed = self.CompareProjectedRows(
                [self.NormalizeRow(row) for row in generated_rows],
                [self.NormalizeRow(row) for row in gold_rows],
                generated_sql,
                gold_sql,
            )

        generated_set = {str(column).lower() for column in generated_columns or []}
        shared_by_name = [column for column in gold_columns if str(column).lower() in generated_set]
        relaxed_passed = False
        match_mode = None

        if len(shared_by_name) == len(gold_columns):
            generated = [self.NormalizeSubRow(row, shared_by_name) for row in generated_rows]
            gold = [self.NormalizeSubRow(row, shared_by_name) for row in gold_rows]
            relaxed_passed = self.CompareProjectedRows(generated, gold, generated_sql, gold_sql)
            match_mode = "name" if relaxed_passed else None

        if not relaxed_passed and len(generated_columns or []) == 1 and len(gold_columns) == 1:
            generated = [tuple(self.NormalizeValue(row.get(generated_columns[0])) for _ in [0]) for row in generated_rows]
            gold = [tuple(self.NormalizeValue(row.get(gold_columns[0])) for _ in [0]) for row in gold_rows]
            relaxed_passed = self.CompareProjectedRows(generated, gold, generated_sql, gold_sql)
            match_mode = "single_value" if relaxed_passed else None

        if not relaxed_passed and len(generated_columns or []) >= len(gold_columns):
            generated = [
                tuple(self.NormalizeValue(row.get(generated_columns[index])) for index in range(len(gold_columns)))
                for row in generated_rows
            ]
            gold = [tuple(self.NormalizeValue(row.get(column)) for column in gold_columns) for row in gold_rows]
            relaxed_passed = self.CompareProjectedRows(generated, gold, generated_sql, gold_sql)
            match_mode = "position" if relaxed_passed else None

        if not relaxed_passed and len(generated_columns or []) > len(gold_columns):
            relaxed_passed = self.CompareGeneratedSubsets(
                generated_columns, generated_rows, gold_columns, gold_rows, generated_sql, gold_sql
            )
            match_mode = "subset" if relaxed_passed else None

        passed = strict_passed or relaxed_passed
        mismatch_type = self.ClassifyMismatch(passed, generated_columns or [], gold_columns)
        return self.BuildVerdict(strict_passed, relaxed_passed, match_mode or ("strict" if strict_passed else None), mismatch_type)

    def ClassifyMismatch(self, passed: bool, generated_columns: list[str], gold_columns: list[str]) -> str | None:
        if passed:
            return None
        generated_set = {str(column).lower() for column in generated_columns}
        gold_set = {str(column).lower() for column in gold_columns}
        if not generated_set & gold_set:
            return "columns_no_overlap"
        if not gold_set <= generated_set:
            return "missing_gold_column"
        if not generated_set <= gold_set:
            return "extra_generated_column"
        return "rows_mismatch"

    def NormalizeSubRow(self, row: dict, columns: list[str]) -> tuple:
        return tuple(self.NormalizeValue(row.get(column)) for column in columns)

    def CompareGeneratedSubsets(self, generated_columns, generated_rows, gold_columns, gold_rows, generated_sql, gold_sql):
        gold = [tuple(self.NormalizeValue(row.get(column)) for column in gold_columns) for row in gold_rows]
        for subset in combinations(generated_columns, len(gold_columns)):
            generated = [tuple(self.NormalizeValue(row.get(column)) for column in subset) for row in generated_rows]
            if self.CompareProjectedRows(generated, gold, generated_sql, gold_sql):
                return True
        return False

    def CompareProjectedRows(self, generated, gold, generated_sql, gold_sql):
        if self.HasOrderBy(gold_sql):
            return generated == gold
        return Counter(generated) == Counter(gold)

    def BuildVerdict(self, strict_passed, relaxed_passed, match_mode, mismatch_type):
        passed = strict_passed or relaxed_passed
        return {
            "passed": passed,
            "strict_passed": strict_passed,
            "relaxed_passed": relaxed_passed,
            "match_mode": match_mode,
            "mismatch_type": None if passed else mismatch_type,
        }

    def NormalizeRow(self, row: dict) -> tuple:
        return tuple((str(key).lower(), self.NormalizeValue(value)) for key, value in sorted((row or {}).items()))

    def NormalizeValue(self, value):
        return _NormalizeTupleValue(value)

    def HasOrderBy(self, sql: str) -> bool:
        return bool(re.search(r"\border\s+by\b", sql or "", re.I))
