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
    directness: float = Field(default=1.0, ge=0.0, le=1.0)


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
            and candidate.result_report is not None
            and "unsafe_sql" not in candidate.static_report.failures
        ]
        if not eligible:
            return None

        def rank(candidate: SqlCandidate) -> tuple[float, bool, int, float, int]:
            reports = [candidate.static_report]
            if candidate.result_report is not None:
                reports.append(candidate.result_report)
            # Static and returned-result coverage describe separate halves of
            # correctness, so neither is allowed to hide a gap in the other.
            coverage = (candidate.static_report.coverage + candidate.result_report.coverage) / 2
            failures = sum(len(report.failures) for report in reports)
            warnings = sum(len(report.warnings) for report in reports)
            directness = min(candidate.directness, candidate.static_report.directness)
            return (
                coverage,
                failures == 0,
                -warnings,
                directness,
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
    projection_aliases = {
        projection.alias.casefold()
        for candidate_select in parsed.find_all(exp.Select)
        for projection in candidate_select.expressions
        if projection.alias
    }
    failures: list[str] = []
    warnings: list[str] = []

    unknown_tables = {table for table in table_names if table not in normalized_schema}
    if unknown_tables:
        failures.append("unknown_table")

    if _has_unknown_columns(
        parsed,
        column_schema,
        alias_to_table,
        [*table_names, *virtual_schema],
        projection_aliases,
    ):
        failures.append("unknown_column")

    projections = list(select.expressions)
    projection_names = _projection_names(projections, column_schema, table_names, virtual_schema)
    aggregate_names = _aggregate_names(projections)
    group_names = _column_names(select.args.get("group"))
    order_names = _column_names(select.args.get("order"))

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

    if intent.shape == "ratio" and not _is_computed_ratio(projections):
        failures.append("missing_ratio_computation")

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

    if _has_unconnected_join(parsed):
        failures.append("unconnected_join")

    expected_entities = {entity.casefold() for entity in intent.entities}
    if expected_entities and set(table_names) - expected_entities:
        warnings.append("unnecessary_join")

    extra_count = _unrequested_projection_count(intent, projections, order_names)
    directness = 1.0
    if extra_count:
        warnings.append("unrequested_projection")
        directness = max(0.0, 1.0 - extra_count / max(len(projections), 1))

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
        directness=directness,
    )


def EvaluateResult(
    intent: IntentContract,
    columns: list[str],
    rows: list[dict[str, Any]],
    *,
    empty_is_legitimate: bool = False,
    static_report: QualityReport | None = None,
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

    if intent.shape == "scalar" and len(columns) != 1:
        failures.append("scalar_multiple_outputs")
    if intent.shape == "ratio":
        if len(columns) != 1:
            failures.append("ratio_multiple_outputs")
        elif rows and not all(_is_number(row.get(columns[0])) for row in rows):
            failures.append("ratio_non_numeric")
        if static_report and "missing_ratio_computation" in static_report.failures:
            failures.append("ratio_raw_output")

    if any(metric.casefold() == "count" for metric in intent.metrics) and rows:
        count_column = next((column for column in columns if column.casefold() == "count"), None)
        if count_column is None and intent.shape == "scalar" and len(columns) == 1:
            count_column = columns[0]
        if count_column is not None and any(
            not _is_non_negative_integer(row.get(count_column)) for row in rows
        ):
            failures.append("suspicious_count")

    missing_groups = {
        group.casefold() for group in intent.grouping
    } - normalized_columns
    if intent.shape == "grouped" and missing_groups:
        failures.append("missing_result_grouping")

    if intent.shape == "ranking" and intent.order and len(rows) > 1:
        order_column = next(
            (column for column in columns if column.casefold() in {metric.casefold() for metric in intent.metrics}),
            None,
        )
        if order_column and not _rows_match_order(rows, order_column, intent.order):
            failures.append("ranking_order_mismatch")

    if intent.shape == "listing" and columns and all(_is_inspection_column(column) for column in columns):
        requested = {item.casefold() for item in intent.output_attributes}
        if not requested or not requested.issubset(normalized_columns):
            failures.append("inspection_query_result")

    required = [
        *(f"output:{name.casefold()}" for name in intent.output_attributes),
        *(f"metric:{name.casefold()}" for name in intent.metrics),
        *(f"group:{name.casefold()}" for name in intent.grouping),
    ]
    covered = [
        element
        for element in required
        if element.partition(":")[2] in normalized_columns
    ]
    if (
        "metric:count" in required
        and "metric:count" not in covered
        and len(columns) == 1
        and intent.shape == "scalar"
    ):
        covered.append("metric:count")
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
    projection_aliases: set[str],
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
        elif name in projection_aliases and _is_projection_alias_reference(column):
            continue
        elif not any(name in schema[table] for table in known_tables):
            return True
    return False


def _is_projection_alias_reference(column: exp.Column) -> bool:
    return any(
        column.find_ancestor(kind) is not None
        for kind in (exp.Order, exp.Group, exp.Having, exp.Qualify)
    )


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


def _is_computed_ratio(projections: list[exp.Expression]) -> bool:
    if len(projections) != 1:
        return False
    # Aggregation alone (for example SUM(price)) is not a ratio. Requiring a
    # division also accepts rate/percentage forms whose numerator is scaled.
    return projections[0].find(exp.Div) is not None


def _unrequested_projection_count(
    intent: IntentContract,
    projections: list[exp.Expression],
    order_names: set[str],
) -> int:
    allowed = {
        *(item.casefold() for item in intent.output_attributes),
        *(item.casefold() for item in intent.metrics),
        *(item.casefold() for item in intent.grouping),
        *order_names,
    }
    if not allowed:
        return 0

    extras = 0
    for projection in projections:
        if isinstance(projection, exp.Star) or (
            isinstance(projection, exp.Column) and projection.name == "*"
        ):
            extras += 1
            continue
        output_name = projection.output_name.casefold() if projection.output_name else ""
        if output_name in allowed:
            continue
        if "count" in {metric.casefold() for metric in intent.metrics} and projection.find(exp.Count) is not None:
            continue
        underlying = {column.name.casefold() for column in projection.find_all(exp.Column)}
        if projection.find(exp.AggFunc) is not None and underlying & allowed:
            continue
        extras += 1
    return extras


def _column_names(expression: exp.Expression | None) -> set[str]:
    if expression is None:
        return set()
    return {column.name.casefold() for column in expression.find_all(exp.Column)}


def _order_matches(order: exp.Order, direction: str) -> bool:
    wants_descending = direction == "descending"
    return all(bool(ordered.args.get("desc")) == wants_descending for ordered in order.expressions)


def _has_unconnected_join(parsed: exp.Expression) -> bool:
    for select in parsed.find_all(exp.Select):
        from_clause = select.args.get("from_")
        introduced = _source_names(from_clause.this) if from_clause is not None else set()
        for join in select.args.get("joins") or []:
            joined = _source_names(join.this)
            if not introduced or not joined:
                return True
            if str(join.args.get("kind") or "").casefold() == "cross":
                introduced.update(joined)
                continue
            if join.args.get("using"):
                introduced.update(joined)
                continue
            on_clause = join.args.get("on")
            qualifiers = {
                column.table.casefold()
                for column in on_clause.find_all(exp.Column)
                if column.table
            } if on_clause is not None else set()
            if not (qualifiers & joined and qualifiers & introduced):
                return True
            introduced.update(joined)
    return False


def _source_names(source: exp.Expression | None) -> set[str]:
    if source is None:
        return set()
    alias = source.alias_or_name
    return {alias.casefold()} if alias else set()


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


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_non_negative_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _rows_match_order(rows: list[dict[str, Any]], column: str, direction: str) -> bool:
    values = [row.get(column) for row in rows]
    if any(value is None for value in values):
        return False
    try:
        pairs = zip(values, values[1:])
        if direction == "descending":
            return all(left >= right for left, right in pairs)
        return all(left <= right for left, right in pairs)
    except TypeError:
        return False


def _is_inspection_column(column: str) -> bool:
    normalized = column.casefold()
    return normalized in {"id", "rowid", "key"} or normalized.endswith("_id")


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
