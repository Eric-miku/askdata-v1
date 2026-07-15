"""Deterministic regression metrics for the versioned AskData V2 demo suite."""

from __future__ import annotations

from collections import Counter
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable, Mapping

from pydantic import ValidationError

from askdata.api.response_models import ChartSpec


_RUNTIME_FIELDS = ("latency_ms", "llm_calls", "sql_executions", "token_usage")


class DemoSuite:
    """Compare captured predictions with curated product expectations.

    The suite is intentionally offline. Capturing predictions may involve live
    services, but comparing them never calls an LLM, database, or vector store.
    """

    def __init__(self, cases: Iterable[Mapping[str, Any]]) -> None:
        self.cases = [dict(case) for case in cases]
        self._RequireUniqueIds(self.cases, "case")

    def Compare(self, predictions: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        prediction_list = [dict(prediction) for prediction in predictions]
        self._RequireUniqueIds(prediction_list, "prediction")
        by_id = {str(item["id"]): item for item in prediction_list}

        missing_fields: Counter[str] = Counter()
        results: list[dict[str, Any]] = []
        clarification_expected = clarification_predicted = clarification_true = 0
        false_clarifications = clear_cases = 0
        unanswerable_expected = unanswerable_predicted = unanswerable_true = 0
        proxy_queries = proxy_scope_total = 0
        chart_total = chart_valid = 0
        table_only_total = table_only_passed = 0
        empty_total = empty_passed = 0
        partial_total = partial_passed = 0
        outage_total = outage_passed = 0
        table_gold = table_hits = column_gold = column_hits = 0
        stream_total = stream_passed = restart_total = restart_passed = 0
        latencies: list[float] = []
        llm_calls = sql_executions = token_usage = 0

        for case in self.cases:
            case_id = str(case["id"])
            prediction = by_id.get(case_id, {})
            checks: list[bool] = []

            expected_kind = case.get("expected_kind")
            predicted_kind = prediction.get("kind")
            checks.append(self._Present(prediction, "kind", missing_fields))
            if expected_kind is not None:
                checks.append(predicted_kind == expected_kind)

            if predicted_kind in {"answer", "partial"}:
                sql_present = self._Present(prediction, "sql", missing_fields)
                sql = prediction.get("sql")
                checks.append(
                    sql_present and isinstance(sql, str) and bool(sql.strip())
                )
            for field in _RUNTIME_FIELDS:
                present = self._Present(prediction, field, missing_fields)
                checks.append(present and self._NonnegativeNumber(prediction.get(field)))

            expected_clarification = expected_kind == "clarification"
            predicted_clarification = predicted_kind == "clarification"
            clarification_expected += int(expected_clarification)
            clarification_predicted += int(predicted_clarification)
            clarification_true += int(expected_clarification and predicted_clarification)
            if case.get("category") == "clear":
                clear_cases += 1
                false_clarifications += int(predicted_clarification)

            expected_unanswerable = (
                case.get("category") == "unanswerable"
                or case.get("expected_error_code") == "unanswerable_from_schema"
            )
            predicted_unanswerable = (
                predicted_kind == "error"
                and prediction.get("code") == "unanswerable_from_schema"
            )
            unanswerable_expected += int(expected_unanswerable)
            unanswerable_predicted += int(predicted_unanswerable)
            unanswerable_true += int(expected_unanswerable and predicted_unanswerable)
            proxy_scope = (
                expected_unanswerable
                or expected_kind == "error"
                or predicted_kind == "error"
            )
            if proxy_scope:
                proxy_scope_total += 1
                has_proxy_query = bool(str(prediction.get("sql") or "").strip())
                proxy_queries += int(has_proxy_query)
                checks.append(not has_proxy_query)

            if "expected_error_code" in case:
                checks.append(
                    self._Present(prediction, "code", missing_fields)
                    and prediction.get("code") == case["expected_error_code"]
                )

            chart_expected = "expected_chart" in case and case.get("expected_chart") is not None
            table_only_expected = "expected_chart" in case and case.get("expected_chart") is None
            chart_present = prediction.get("chart") is not None
            if chart_expected or chart_present:
                chart_total += 1
                if not chart_present:
                    missing_fields["chart"] += 1
                    valid_chart = False
                else:
                    valid_chart = self._ValidChart(prediction["chart"])
                chart_valid += int(valid_chart)
                checks.append(valid_chart)
                if chart_expected:
                    checks.append(
                        isinstance(prediction.get("chart"), Mapping)
                        and prediction["chart"].get("type") == case["expected_chart"]
                    )
            if table_only_expected:
                table_only_total += 1
                present = self._Present(prediction, "chart", missing_fields)
                matched = present and prediction.get("chart") is None
                table_only_passed += int(matched)
                checks.append(matched)

            if "expected_empty_result" in case:
                empty_total += 1
                present = self._Present(prediction, "rows", missing_fields)
                rows = prediction.get("rows")
                matched = (
                    present
                    and isinstance(rows, list)
                    and (len(rows) == 0) is bool(case["expected_empty_result"])
                )
                empty_passed += int(matched)
                checks.append(matched)

            if case.get("expected_partial") is True:
                partial_total += 1
                limitations_present = self._Present(
                    prediction, "limitations", missing_fields
                )
                suggestions_present = self._Present(
                    prediction, "suggestions", missing_fields
                )
                limitations = prediction.get("limitations")
                suggestions = prediction.get("suggestions")
                matched = (
                    limitations_present
                    and suggestions_present
                    and isinstance(limitations, list)
                    and bool(limitations)
                    and all(isinstance(item, str) and item.strip() for item in limitations)
                    and isinstance(suggestions, list)
                    and all(isinstance(item, str) and item.strip() for item in suggestions)
                )
                partial_passed += int(matched)
                checks.append(matched)

            if case.get("expected_vector_outage_fallback") is True:
                outage_total += 1
                outage_present = self._Present(
                    prediction, "vector_outage", missing_fields
                )
                fallback_present = self._Present(
                    prediction, "lexical_fallback", missing_fields
                )
                warnings_present = self._Present(
                    prediction, "retrieval_warnings", missing_fields
                )
                warnings = prediction.get("retrieval_warnings")
                matched = (
                    outage_present
                    and prediction.get("vector_outage") is True
                    and fallback_present
                    and prediction.get("lexical_fallback") is True
                    and warnings_present
                    and isinstance(warnings, list)
                    and bool(warnings)
                    and all(isinstance(item, str) and item.strip() for item in warnings)
                )
                outage_passed += int(matched)
                checks.append(matched)

            if "expected_context" in case:
                present = self._Present(prediction, "retrieved_context", missing_fields)
                expected_context = str(case["expected_context"]).casefold()
                actual_context = str(prediction.get("retrieved_context") or "").casefold()
                checks.append(present and expected_context in actual_context)

            gold_tables = self._Normalized(case.get("gold_tables"))
            if gold_tables:
                present = self._Present(prediction, "retrieved_tables", missing_fields)
                retrieved = self._Normalized(prediction.get("retrieved_tables"))
                table_gold += len(gold_tables)
                table_hits += len(gold_tables & retrieved)
                checks.append(present and gold_tables <= retrieved)

            gold_columns = self._Normalized(case.get("gold_columns"))
            if gold_columns:
                present = self._Present(prediction, "retrieved_columns", missing_fields)
                retrieved = self._Normalized(prediction.get("retrieved_columns"))
                column_matches = self._ColumnMatches(gold_columns, retrieved)
                column_gold += len(gold_columns)
                column_hits += column_matches
                checks.append(present and column_matches == len(gold_columns))

            if "expected_stream_parity" in case:
                stream_total += 1
                present = self._Present(prediction, "stream_parity", missing_fields)
                matched = present and prediction.get("stream_parity") is case["expected_stream_parity"]
                stream_passed += int(matched)
                checks.append(matched)

            if "expected_restart_persistence" in case:
                restart_total += 1
                present = self._Present(prediction, "restart_persistence", missing_fields)
                matched = (
                    present
                    and prediction.get("restart_persistence")
                    is case["expected_restart_persistence"]
                )
                restart_passed += int(matched)
                checks.append(matched)

            if self._NonnegativeNumber(prediction.get("latency_ms")):
                latencies.append(float(prediction["latency_ms"]))
            if self._NonnegativeNumber(prediction.get("llm_calls")):
                llm_calls += int(prediction["llm_calls"])
            if self._NonnegativeNumber(prediction.get("sql_executions")):
                sql_executions += int(prediction["sql_executions"])
            if self._NonnegativeNumber(prediction.get("token_usage")):
                token_usage += int(prediction["token_usage"])

            results.append({
                "id": case_id,
                "category": str(case.get("category") or "unknown"),
                "passed": bool(checks) and all(checks),
            })

        by_category: dict[str, dict[str, Any]] = {}
        for category in sorted({item["category"] for item in results}):
            items = [item for item in results if item["category"] == category]
            passed = sum(1 for item in items if item["passed"])
            by_category[category] = {
                "total": len(items),
                "passed": passed,
                "pass_rate": self._Rate(passed, len(items)),
            }
        passed = sum(1 for item in results if item["passed"])
        return {
            "summary": {
                "total": len(results),
                "passed": passed,
                "pass_rate": self._Rate(passed, len(results)),
            },
            "by_category": by_category,
            "clarification_precision": self._Rate(clarification_true, clarification_predicted),
            "clarification_recall": self._Rate(clarification_true, clarification_expected),
            "false_clarification_rate": self._Rate(false_clarifications, clear_cases),
            "unanswerable_precision": self._Rate(unanswerable_true, unanswerable_predicted),
            "unanswerable_recall": self._Rate(unanswerable_true, unanswerable_expected),
            "proxy_query_rate": self._Rate(proxy_queries, proxy_scope_total),
            "chart_spec_validity": self._Rate(chart_valid, chart_total),
            "table_only_correctness": self._Rate(
                table_only_passed, table_only_total
            ),
            "empty_result_correctness": self._Rate(empty_passed, empty_total),
            "partial_response_validity": self._Rate(partial_passed, partial_total),
            "vector_outage_fallback": self._Rate(outage_passed, outage_total),
            "retrieval_table_recall_at_k": self._Rate(table_hits, table_gold),
            "retrieval_column_recall_at_k": self._Rate(column_hits, column_gold),
            "stream_parity": self._Rate(stream_passed, stream_total),
            "restart_persistence": self._Rate(restart_passed, restart_total),
            "latency_ms": {
                "p50": self._Percentile(latencies, 50),
                "p95": self._Percentile(latencies, 95),
            },
            "llm_calls": llm_calls,
            "sql_executions": sql_executions,
            "token_usage": token_usage,
            "missing_fields": dict(sorted(missing_fields.items())),
            "cases": results,
        }

    @staticmethod
    def WriteReport(report: Mapping[str, Any], path: str | Path) -> None:
        """Atomically replace a JSON report in the destination directory."""
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(report, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    @staticmethod
    def Load(path: str | Path, collection: str) -> list[dict[str, Any]]:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        values = payload if isinstance(payload, list) else payload.get(collection)
        if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
            raise ValueError(f"Expected a JSON list in '{collection}'")
        return [dict(item) for item in values]

    @staticmethod
    def _RequireUniqueIds(items: list[dict[str, Any]], label: str) -> None:
        ids = [str(item.get("id") or "").strip() for item in items]
        if any(not item_id for item_id in ids):
            raise ValueError(f"Every {label} requires a nonblank id")
        duplicates = sorted(item_id for item_id, count in Counter(ids).items() if count > 1)
        if duplicates:
            raise ValueError(f"Duplicate {label} ids: {', '.join(duplicates)}")

    @staticmethod
    def _Present(item: Mapping[str, Any], field: str, missing: Counter[str]) -> bool:
        if field in item:
            return True
        missing[field] += 1
        return False

    @staticmethod
    def _NonnegativeNumber(value: Any) -> bool:
        return (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(float(value))
            and value >= 0
        )

    @staticmethod
    def _Normalized(values: Any) -> set[str]:
        if not isinstance(values, list):
            return set()
        return {str(value).strip().casefold() for value in values if str(value).strip()}

    @staticmethod
    def _ColumnMatches(gold: set[str], retrieved: set[str]) -> int:
        """Only bare gold names may use conservative terminal-name matching."""
        retrieved_bare = {value.rsplit(".", 1)[-1] for value in retrieved}
        return sum(
            1
            for value in gold
            if value in retrieved or ("." not in value and value in retrieved_bare)
        )

    @staticmethod
    def _ValidChart(chart: Any) -> bool:
        try:
            ChartSpec.model_validate(chart)
            return True
        except (ValidationError, TypeError):
            return False

    @staticmethod
    def _Rate(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 4) if denominator else 0.0

    @staticmethod
    def _Percentile(values: list[float], percentile: int) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        position = (len(ordered) - 1) * percentile / 100
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return round(ordered[lower], 2)
        weight = position - lower
        return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 2)
