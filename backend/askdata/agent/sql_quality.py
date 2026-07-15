"""Gold-independent SQL and result checks used by the staged pipeline."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from pydantic import BaseModel, Field
import sqlglot
from sqlglot import exp

from askdata.agent.answer_shape import AnswerShapeFailureCodes
from askdata.agent.intent import IntentContract
from askdata.db.validator import SQLValidator


class QualityReport(BaseModel):
    passed: bool
    failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    covered_elements: list[str] = Field(default_factory=list)


class SqlCandidate(BaseModel):
    sql: str
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    referenced_context: list[str] = Field(default_factory=list)
    static_report: QualityReport
    result_report: QualityReport | None = None
    execution_error: str | None = None
    directness: float = Field(default=1.0, ge=0.0, le=1.0)
    sequence: int = Field(ge=0)


class CandidateLedger:
    """Record every attempt and select by quality, never mere recency."""

    def __init__(self) -> None:
        self._candidates: list[SqlCandidate] = []

    @property
    def candidates(self) -> tuple[SqlCandidate, ...]:
        return tuple(self._candidates)

    def Add(self, candidate: SqlCandidate) -> None:
        self._candidates.append(candidate)

    def SelectBest(self) -> SqlCandidate | None:
        eligible = [
            candidate
            for candidate in self._candidates
            if candidate.execution_error is None
            and "unsafe_sql" not in candidate.static_report.failures
        ]
        if not eligible:
            return None

        def rank(candidate: SqlCandidate) -> tuple[float, bool, int, float, int]:
            reports = [candidate.static_report]
            if candidate.result_report is not None:
                reports.append(candidate.result_report)
            coverage = candidate.result_report.coverage if candidate.result_report else candidate.static_report.coverage
            failures = sum(len(report.failures) for report in reports)
            warnings = sum(len(report.warnings) for report in reports)
            return (
                coverage,
                failures == 0,
                -warnings,
                candidate.directness,
                -candidate.sequence,
            )

        return max(eligible, key=rank)


def EvaluateStaticSql(
    intent: IntentContract,
    sql: str,
    schema: Mapping[str, Iterable[str]],
    *,
    question: str | None = None,
) -> QualityReport:
    """Inspect safety, schema grounding, and intent alignment without execution."""

    try:
        statements = [statement for statement in sqlglot.parse(sql or "", read="sqlite") if statement is not None]
    except Exception:
        return QualityReport(passed=False, failures=["invalid_sql"], coverage=0.0)
    if not statements:
        return QualityReport(passed=False, failures=["invalid_sql"], coverage=0.0)

    validation = SQLValidator(dialect="sqlite").validate(sql)
    if not validation.is_valid:
        return QualityReport(passed=False, failures=["unsafe_sql"], coverage=0.0)

    parsed = statements[0]

    select = parsed if isinstance(parsed, exp.Select) else parsed.find(exp.Select)
    if select is None:
        return QualityReport(passed=False, failures=["invalid_sql"], coverage=0.0)

    normalized_schema = {
        str(table).casefold(): {str(column).casefold() for column in columns}
        for table, columns in schema.items()
    }
    tables = list(parsed.find_all(exp.Table))
    cte_names = {cte.alias_or_name.casefold() for cte in parsed.find_all(exp.CTE)}
    virtual_schema = _virtual_schema(parsed)
    column_schema = {**normalized_schema, **virtual_schema}
    table_names = [table.name.casefold() for table in tables if table.name.casefold() not in cte_names]
    alias_to_table = {table.alias_or_name.casefold(): table.name.casefold() for table in tables}
    failures: list[str] = []
    warnings: list[str] = []

    unknown_tables = {table for table in table_names if table not in normalized_schema}
    if unknown_tables:
        failures.append("unknown_table")

    if _has_unknown_columns(parsed, column_schema, alias_to_table, [*table_names, *virtual_schema]):
        failures.append("unknown_column")

    projections = list(select.expressions)
    projection_names = _projection_names(projections, column_schema, table_names, virtual_schema)
    aggregate_names = _aggregate_names(projections)
    group_names = _column_names(select.args.get("group"))

    asks_count = any(metric.casefold() == "count" for metric in intent.metrics)
    has_count = any(projection.find(exp.Count) is not None for projection in projections)
    if asks_count and not has_count:
        failures.append("missing_count_aggregation")

    missing_outputs = [
        attribute
        for attribute in intent.output_attributes
        if attribute.casefold() not in projection_names
    ]
    if missing_outputs:
        failures.append("missing_output_attribute")

    missing_metrics = [
        metric
        for metric in intent.metrics
        if metric.casefold() != "count"
        and metric.casefold() not in projection_names
        and metric.casefold() not in aggregate_names
    ]
    if missing_metrics:
        failures.append("missing_metric")

    if intent.grouping and any(item.casefold() not in group_names for item in intent.grouping):
        failures.append("missing_grouping")

    order = select.args.get("order")
    if intent.shape == "ranking" or intent.order:
        if order is None or not order.expressions:
            failures.append("missing_order")
        elif intent.order and not _order_matches(order, intent.order):
            failures.append("wrong_order_direction")

    limit_value = _literal_limit(select.args.get("limit"))
    if intent.shape == "ranking" and limit_value is None:
        failures.append("missing_limit")
    if intent.expected_max_rows is not None and limit_value is not None and limit_value > intent.expected_max_rows:
        failures.append("excessive_limit")

    if intent.filters and select.args.get("where") is None:
        failures.append("missing_filter")
    if intent.time_condition and select.args.get("where") is None:
        failures.append("missing_time_condition")

    joins = list(parsed.find_all(exp.Join))
    if any(_join_is_unconnected(join) for join in joins):
        failures.append("unconnected_join")

    expected_entities = {entity.casefold() for entity in intent.entities}
    if expected_entities and set(table_names) - expected_entities:
        warnings.append("unnecessary_join")

    if question:
        failures.extend(AnswerShapeFailureCodes(question, sql))

    covered, required = _static_coverage(
        intent,
        set(table_names),
        projection_names,
        aggregate_names,
        group_names,
        order is not None,
        select.args.get("where") is not None,
        has_count,
    )
    failures = _unique(failures)
    warnings = _unique(warnings)
    coverage = len(covered) / len(required) if required else 1.0
    return QualityReport(
        passed=not failures,
        failures=failures,
        warnings=warnings,
        coverage=coverage,
        covered_elements=covered,
    )


def EvaluateResult(
    intent: IntentContract,
    columns: list[str],
    rows: list[dict[str, Any]],
    *,
    empty_is_legitimate: bool = False,
) -> QualityReport:
    """Verify cardinality, non-null content, and returned intent coverage."""

    failures: list[str] = []
    warnings: list[str] = []
    normalized_columns = {column.casefold() for column in columns}

    if not rows:
        if empty_is_legitimate:
            warnings.append("legitimate_empty_result")
        else:
            failures.append("empty_result")
    elif all(value is None for row in rows for value in row.values()):
        failures.append("null_only_result")

    expected_max = intent.expected_max_rows
    if expected_max is None and intent.shape in {"scalar", "ratio"}:
        expected_max = 1
    if expected_max is not None and len(rows) > expected_max:
        failures.append("too_many_rows")

    required = [
        *(f"output:{name.casefold()}" for name in intent.output_attributes),
        *(f"metric:{name.casefold()}" for name in intent.metrics),
    ]
    covered = [
        element
        for element in required
        if element.partition(":")[2] in normalized_columns
    ]
    if any(element.startswith("output:") for element in set(required) - set(covered)):
        failures.append("missing_result_attribute")
    if any(element.startswith("metric:") for element in set(required) - set(covered)):
        failures.append("missing_result_metric")

    coverage = len(covered) / len(required) if required else (1.0 if columns else 0.0)
    failures = _unique(failures)
    return QualityReport(
        passed=not failures,
        failures=failures,
        warnings=warnings,
        coverage=coverage,
        covered_elements=covered,
    )


def _has_unknown_columns(
    parsed: exp.Expression,
    schema: Mapping[str, set[str]],
    alias_to_table: Mapping[str, str],
    table_names: list[str],
) -> bool:
    known_tables = [name for name in table_names if name in schema]
    for column in parsed.find_all(exp.Column):
        name = column.name.casefold()
        if name == "*":
            continue
        qualifier = column.table.casefold()
        if qualifier:
            table = alias_to_table.get(qualifier, qualifier)
            if table not in schema or name not in schema[table]:
                return True
        elif not any(name in schema[table] for table in known_tables):
            return True
    return False


def _projection_names(
    projections: list[exp.Expression],
    schema: Mapping[str, set[str]],
    table_names: list[str],
    virtual_tables: Mapping[str, set[str]],
) -> set[str]:
    names: set[str] = set()
    for projection in projections:
        if projection.alias:
            names.add(projection.alias.casefold())
        if isinstance(projection, exp.Star) or (
            isinstance(projection, exp.Column) and projection.name == "*"
        ):
            for table in [*table_names, *virtual_tables]:
                names.update(schema.get(table, set()))
        if isinstance(projection, exp.Column):
            names.add(projection.name.casefold())
        names.update(column.name.casefold() for column in projection.find_all(exp.Column))
    return names


def _aggregate_names(projections: list[exp.Expression]) -> set[str]:
    names: set[str] = set()
    for projection in projections:
        if projection.find(exp.AggFunc) is not None:
            if projection.alias:
                names.add(projection.alias.casefold())
            names.update(column.name.casefold() for column in projection.find_all(exp.Column))
    return names


def _column_names(expression: exp.Expression | None) -> set[str]:
    if expression is None:
        return set()
    return {column.name.casefold() for column in expression.find_all(exp.Column)}


def _order_matches(order: exp.Order, direction: str) -> bool:
    wants_descending = direction == "descending"
    return all(bool(ordered.args.get("desc")) == wants_descending for ordered in order.expressions)


def _join_is_unconnected(join: exp.Join) -> bool:
    if str(join.args.get("kind") or "").upper() == "CROSS":
        return False
    on_clause = join.args.get("on")
    normalized_missing_on = isinstance(on_clause, exp.Boolean) and on_clause.this is True
    if (on_clause is None or normalized_missing_on) and not join.args.get("using"):
        return True
    if on_clause is None:
        return False
    qualifiers = {column.table.casefold() for column in on_clause.find_all(exp.Column) if column.table}
    joined = join.this.alias_or_name.casefold()
    return bool(qualifiers) and joined not in qualifiers


def _virtual_schema(parsed: exp.Expression) -> dict[str, set[str]]:
    virtual: dict[str, set[str]] = {}
    for cte in parsed.find_all(exp.CTE):
        select = cte.this if isinstance(cte.this, exp.Select) else cte.this.find(exp.Select)
        if select is not None:
            virtual[cte.alias_or_name.casefold()] = {
                name.casefold() for name in select.named_selects if name and name != "*"
            }
    for subquery in parsed.find_all(exp.Subquery):
        if not subquery.alias_or_name:
            continue
        select = subquery.this if isinstance(subquery.this, exp.Select) else subquery.this.find(exp.Select)
        if select is not None:
            virtual[subquery.alias_or_name.casefold()] = {
                name.casefold() for name in select.named_selects if name and name != "*"
            }
    return virtual


def _literal_limit(limit: exp.Limit | None) -> int | None:
    if limit is None:
        return None
    expression = limit.args.get("expression")
    if isinstance(expression, exp.Literal) and expression.is_int:
        return int(expression.this)
    return None


def _static_coverage(
    intent: IntentContract,
    tables: set[str],
    projections: set[str],
    aggregates: set[str],
    groups: set[str],
    has_order: bool,
    has_where: bool,
    has_count: bool,
) -> tuple[list[str], list[str]]:
    required = [
        *(f"entity:{item.casefold()}" for item in intent.entities),
        *(f"output:{item.casefold()}" for item in intent.output_attributes),
        *(f"metric:{item.casefold()}" for item in intent.metrics),
        *(f"group:{item.casefold()}" for item in intent.grouping),
        *("filter" for _ in intent.filters),
        *(("order",) if intent.order else ()),
        *(("time",) if intent.time_condition else ()),
    ]
    covered: list[str] = []
    for element in required:
        kind, _, value = element.partition(":")
        if kind == "entity" and value in tables:
            covered.append(element)
        elif kind == "output" and value in projections:
            covered.append(element)
        elif kind == "metric" and (value in projections or value in aggregates or (value == "count" and has_count)):
            covered.append(element)
        elif kind == "group" and value in groups:
            covered.append(element)
        elif kind in {"filter", "time"} and has_where:
            covered.append(element)
        elif kind == "order" and has_order:
            covered.append(element)
    return covered, required


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))
