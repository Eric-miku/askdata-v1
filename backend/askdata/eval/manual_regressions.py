"""Manual Text2SQL regression fixtures and result checks.

This harness is intentionally offline by default. It validates captured or
injected AgentGraph-style results against known hard-case expectations without
calling an LLM, database, or retriever.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from pydantic import BaseModel, Field


class ManualRegressionCase(BaseModel):
    id: str
    database_id: str
    question: str
    expected_columns: list[str] = Field(default_factory=list)
    min_rows: int = Field(default=0, ge=0)
    expected_error: str | None = None
    must_not_sql: list[str] = Field(default_factory=list)


def LoadManualRegressionCases(path: Path) -> list[ManualRegressionCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [ManualRegressionCase.model_validate(item) for item in payload]


class ManualRegressionRunner:
    """Check Text2SQL results against manual hard-case fixtures."""

    def Check(self, case: ManualRegressionCase, result: Mapping[str, Any]) -> dict[str, Any]:
        failures: list[str] = []
        error = result.get("error")
        if case.expected_error is None and error:
            failures.append(f"unexpected_error:{error}")
        if case.expected_error is not None and error != case.expected_error:
            failures.append(f"expected_error:{case.expected_error}")

        columns = {str(column).casefold() for column in result.get("columns") or []}
        for column in case.expected_columns:
            if column.casefold() not in columns:
                failures.append(f"missing_column:{column}")

        rows = result.get("rows") or []
        if len(rows) < case.min_rows:
            failures.append(f"row_count_below_min:{len(rows)}<{case.min_rows}")

        sql = str(result.get("sql") or "").casefold()
        for forbidden in case.must_not_sql:
            if forbidden.casefold() in sql:
                failures.append(f"forbidden_sql:{forbidden}")

        return {"id": case.id, "passed": not failures, "failures": failures}

    def Compare(
        self,
        cases: Iterable[ManualRegressionCase],
        results: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        checked = [self.Check(case, results.get(case.id, {})) for case in cases]
        passed = sum(1 for item in checked if item["passed"])
        total = len(checked)
        return {
            "summary": {
                "total": total,
                "passed": passed,
                "pass_rate": passed / total if total else 0.0,
            },
            "cases": checked,
        }

    def Run(
        self,
        cases: Iterable[ManualRegressionCase],
        query_fn: Callable[[str, str], Mapping[str, Any]],
    ) -> dict[str, Any]:
        results = {
            case.id: query_fn(case.question, case.database_id)
            for case in cases
        }
        return self.Compare(cases, results)
