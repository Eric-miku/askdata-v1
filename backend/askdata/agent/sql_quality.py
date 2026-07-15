"""Gold-independent SQL and result checks used by the staged pipeline."""

from __future__ import annotations

import re
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
    semantic_outputs: dict[str, list[str]] = Field(default_factory=dict)


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

        def rank(candidate: SqlCandidate) -> tuple[float, int, int, float, int]:
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
                -failures,
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

    select = parsed if isinstance(parsed, exp.Select) else next(parsed.find_all(exp.Select), None)
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
    contributing_tables = _contributing_relations(parsed)
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
    if _has_invalid_join_using(parsed, column_schema, alias_to_table):
        failures.append("invalid_join_using")

    projections = list(select.expressions)
    projection_names = _projection_names(projections, column_schema, table_names, virtual_schema)
    aggregate_names = _aggregate_names(projections)
    group_names = _column_names(select.args.get("group"))
    semantic_outputs = _projection_semantic_outputs(intent, projections)
    semantically_covered_metrics = {
        tag.partition(":")[2]
        for tags in semantic_outputs.values()
        for tag in tags
        if tag.startswith("metric:")
    }
    query_order = parsed.args.get("order") or select.args.get("order")
    query_limit = parsed.args.get("limit") or select.args.get("limit")
    order_names = _column_names(query_order)

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
        and _normalize_concept(metric) not in semantically_covered_metrics
    ]
    if missing_metrics:
        failures.append("missing_metric")

    if intent.grouping and any(item.casefold() not in group_names for item in intent.grouping):
        failures.append("missing_grouping")

    if intent.shape == "ratio" and not _is_computed_ratio(projections):
        failures.append("missing_ratio_computation")

    order = query_order
    if intent.shape == "ranking" or intent.order:
        if order is None or not order.expressions:
            failures.append("missing_order")
        elif intent.order and not _order_matches(order, intent.order):
            failures.append("wrong_order_direction")
        if intent.metrics and not _order_targets_metric(order, intent, semantic_outputs):
            failures.append("wrong_order_target")

    limit_value = _literal_limit(query_limit)
    if intent.shape == "ranking" and limit_value is None:
        failures.append("missing_limit")
    if intent.expected_max_rows is not None and limit_value is not None and limit_value > intent.expected_max_rows:
        failures.append("excessive_limit")

    where_scope_groups, unresolved_filter_scope = _applicable_filter_scopes(parsed, intent)
    if intent.filters and where_scope_groups and any(not group for group in where_scope_groups):
        failures.append("missing_filter")
    if intent.filters and not where_scope_groups and not unresolved_filter_scope:
        failures.append("missing_filter")
    if intent.time_condition and where_scope_groups and any(not group for group in where_scope_groups):
        failures.append("missing_time_condition")
    if intent.time_condition and not where_scope_groups and not unresolved_filter_scope:
        failures.append("missing_time_condition")
    grounded_filters = [
        _requirement_is_grounded(requirement, where_scope_groups)
        for requirement in intent.filters
    ]
    grounded_time = bool(
        intent.time_condition
        and _requirement_is_grounded(intent.time_condition, where_scope_groups)
    )
    if intent.filters and (unresolved_filter_scope or any(not grounded for grounded in grounded_filters)):
        warnings.append("unresolved_filter_alignment")
    if intent.time_condition and (unresolved_filter_scope or not grounded_time):
        warnings.append("unresolved_time_alignment")

    if _has_unconnected_join(parsed):
        failures.append("unconnected_join")

    expected_entities = {entity.casefold() for entity in intent.entities}
    if expected_entities - contributing_tables:
        failures.append("missing_entity")
    if expected_entities and contributing_tables - expected_entities:
        warnings.append("unnecessary_join")

    extra_count = _unrequested_projection_count(
        intent,
        projections,
        order_names,
        semantic_outputs,
    )
    directness = 1.0
    if extra_count:
        warnings.append("unrequested_projection")
        directness = max(0.0, 1.0 - extra_count / max(len(projections), 1))

    if question:
        legacy_failures, legacy_warnings = _classify_legacy_shape_codes(
            intent,
            AnswerShapeFailureCodes(question, sql),
        )
        failures.extend(legacy_failures)
        warnings.extend(legacy_warnings)

    covered, required = _static_coverage(
        intent,
        contributing_tables,
        projection_names,
        aggregate_names,
        group_names,
        order is not None,
        grounded_filters,
        grounded_time,
        has_count,
        semantically_covered_metrics,
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
        semantic_outputs=semantic_outputs,
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
    if static_report is not None:
        for column in columns:
            for semantic_element in static_report.semantic_outputs.get(_normalize_concept(column), []):
                if semantic_element in required and semantic_element not in covered:
                    covered.append(semantic_element)
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


def _projection_semantic_outputs(
    intent: IntentContract,
    projections: list[exp.Expression],
) -> dict[str, list[str]]:
    requested = {_normalize_concept(metric): metric for metric in intent.metrics}
    outputs: dict[str, list[str]] = {}
    for projection in projections:
        output_name = projection.output_name.casefold() if projection.output_name else ""
        if not output_name:
            continue
        candidates = {
            _normalize_concept(output_name),
            *_expression_metric_candidates(projection),
        }
        matched = [
            f"metric:{normalized_metric}"
            for normalized_metric in requested
            if normalized_metric in candidates
        ]
        if matched:
            outputs[_normalize_concept(output_name)] = matched
    return outputs


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
    semantic_outputs: Mapping[str, list[str]],
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
        if _normalize_concept(output_name) in semantic_outputs:
            continue
        if "count" in {metric.casefold() for metric in intent.metrics} and projection.find(exp.Count) is not None:
            continue
        underlying = {column.name.casefold() for column in projection.find_all(exp.Column)}
        if projection.find(exp.AggFunc) is not None and underlying & allowed:
            continue
        extras += 1
    return extras


def _order_targets_metric(
    order: exp.Order | None,
    intent: IntentContract,
    semantic_outputs: Mapping[str, list[str]],
) -> bool:
    if order is None or not order.expressions:
        return False
    ordered_expression = order.expressions[0].this
    requested = {_normalize_concept(metric) for metric in intent.metrics}
    names = {
        _normalize_concept(column.name)
        for column in ordered_expression.find_all(exp.Column)
    }
    if isinstance(ordered_expression, exp.Column):
        names.add(_normalize_concept(ordered_expression.name))
    if names & requested:
        return True
    if _expression_metric_candidates(ordered_expression) & requested:
        return True
    return any(
        tag.partition(":")[2] in requested
        for name in names
        for tag in semantic_outputs.get(name, [])
    )


def _normalize_concept(value: str) -> str:
    return re.sub(r"[^\w]+", "_", value.casefold()).strip("_")


def _expression_metric_candidates(expression: exp.Expression) -> set[str]:
    candidates: set[str] = set()
    if expression.find(exp.Count) is not None:
        candidates.add("count")
    for column in expression.find_all(exp.Column):
        normalized_column = _normalize_concept(column.name)
        candidates.add(normalized_column)
        if expression.find(exp.Avg) is not None:
            candidates.update(
                {
                    f"avg_{normalized_column}",
                    f"average_{normalized_column}",
                    f"mean_{normalized_column}",
                }
            )
        if expression.find(exp.Sum) is not None:
            candidates.update({f"sum_{normalized_column}", f"total_{normalized_column}"})
    if expression.find(exp.Div) is not None:
        candidates.update({"ratio", "percentage", "percent", "rate"})
    return candidates


def _requirement_is_grounded(
    requirement: str,
    scope_groups: list[list[exp.Where]],
) -> bool:
    if not scope_groups or any(not group for group in scope_groups):
        return False
    stopwords = {
        "a", "an", "and", "at", "by", "during", "for", "from", "in",
        "is", "of", "on", "or", "the", "to", "where", "with",
    }
    required_tokens = {
        token.casefold()
        for token in re.findall(r"[^\W_]+(?:_[^\W_]+)*", requirement, flags=re.UNICODE)
        if token.casefold() not in stopwords
    }
    required_operators = set(re.findall(r"<=|>=|<>|!=|=|<|>", requirement))
    if not required_tokens:
        return False
    for group in scope_groups:
        if not any(_where_matches_requirement(where, required_tokens, required_operators) for where in group):
            return False
    return True


def _where_matches_requirement(
    where: exp.Where,
    required_tokens: set[str],
    required_operators: set[str],
) -> bool:
    where_sql = where.sql(dialect="sqlite").casefold()
    sql_tokens = set(re.findall(r"[^\W_]+(?:_[^\W_]+)*", where_sql, flags=re.UNICODE))
    sql_operators = set(re.findall(r"<=|>=|<>|!=|=|<|>", where_sql))
    return required_tokens <= sql_tokens and required_operators <= sql_operators


def _classify_legacy_shape_codes(
    intent: IntentContract,
    codes: list[str],
) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    for code in codes:
        if code == "listing_returns_only_aggregates" and intent.shape in {"scalar", "ratio", "grouped"}:
            continue
        aligned = (
            (code == "missing_count_aggregation" and "count" in {_normalize_concept(item) for item in intent.metrics})
            or (code in {"computed_value_has_extra_projections", "missing_ratio_computation"} and intent.shape == "ratio")
            or (code == "listing_returns_only_aggregates" and intent.shape == "listing")
        )
        if aligned:
            failures.append(code)
        elif code != "invalid_sql":
            warnings.append(f"legacy_{code}")
    return failures, warnings


def _column_names(expression: exp.Expression | None) -> set[str]:
    if expression is None:
        return set()
    return {column.name.casefold() for column in expression.find_all(exp.Column)}


def _order_matches(order: exp.Order, direction: str) -> bool:
    wants_descending = direction == "descending"
    if not order.expressions:
        return False
    return bool(order.expressions[0].args.get("desc")) == wants_descending


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


def _has_invalid_join_using(
    parsed: exp.Expression,
    schema: Mapping[str, set[str]],
    alias_to_table: Mapping[str, str],
) -> bool:
    for select in parsed.find_all(exp.Select):
        from_clause = select.args.get("from_")
        introduced = _source_names(from_clause.this) if from_clause is not None else set()
        for join in select.args.get("joins") or []:
            joined = _source_names(join.this)
            using_columns = {
                identifier.name.casefold()
                for identifier in join.args.get("using") or []
            }
            if using_columns:
                left_columns = _columns_for_sources(introduced, schema, alias_to_table)
                right_columns = _columns_for_sources(joined, schema, alias_to_table)
                if not using_columns <= left_columns or not using_columns <= right_columns:
                    return True
            introduced.update(joined)
    return False


def _columns_for_sources(
    sources: set[str],
    schema: Mapping[str, set[str]],
    alias_to_table: Mapping[str, str],
) -> set[str]:
    return {
        column
        for source in sources
        for column in schema.get(alias_to_table.get(source, source), set())
    }


def _source_names(source: exp.Expression | None) -> set[str]:
    if source is None:
        return set()
    alias = source.alias_or_name
    return {alias.casefold()} if alias else set()


def _contributing_relations(parsed: exp.Expression) -> set[str]:
    ctes = {
        cte.alias_or_name.casefold(): cte.this
        for cte in parsed.find_all(exp.CTE)
    }
    relations: set[str] = set()
    visiting_ctes: set[str] = set()

    def visit_source(source: exp.Expression | None) -> None:
        if isinstance(source, exp.Table):
            name = source.name.casefold()
            if name in ctes:
                if name not in visiting_ctes:
                    visiting_ctes.add(name)
                    visit_query(ctes[name])
                    visiting_ctes.remove(name)
            else:
                relations.add(name)
        elif isinstance(source, exp.Subquery):
            visit_query(source.this)

    def visit_nested_subqueries(value: Any) -> None:
        values = value if isinstance(value, list) else [value]
        for item in values:
            if not isinstance(item, exp.Expression):
                continue
            if isinstance(item, exp.Subquery):
                visit_query(item.this)
            for subquery in item.find_all(exp.Subquery):
                visit_query(subquery.this)

    def visit_query(query: exp.Expression) -> None:
        if isinstance(query, exp.SetOperation):
            visit_query(query.this)
            visit_query(query.expression)
            return
        select = query if isinstance(query, exp.Select) else next(query.find_all(exp.Select), None)
        if select is None:
            return
        from_clause = select.args.get("from_")
        visit_source(from_clause.this if from_clause is not None else None)
        for join in select.args.get("joins") or []:
            visit_source(join.this)
        for argument in ("expressions", "where", "having", "qualify", "order", "group"):
            visit_nested_subqueries(select.args.get(argument))

    visit_query(parsed)
    return relations


def _applicable_filter_scopes(
    parsed: exp.Expression,
    intent: IntentContract,
) -> tuple[list[list[exp.Where]], bool]:
    expected = {entity.casefold() for entity in intent.entities}
    ctes = {
        cte.alias_or_name.casefold(): cte.this
        for cte in parsed.find_all(exp.CTE)
    }
    groups: list[list[exp.Where]] = []
    unresolved = False
    for branch in _output_branches(parsed):
        branch_relations = _direct_branch_relations(branch, ctes)
        if expected:
            if branch_relations & expected:
                groups.extend(_branch_filter_scope_groups(branch, expected, ctes, set()))
            elif not branch_relations:
                unresolved = True
        else:
            groups.extend(_branch_filter_scope_groups(branch, expected, ctes, set()))
    if expected and not groups and not unresolved:
        return [], False
    return groups, unresolved


def _branch_filter_scope_groups(
    branch: exp.Select,
    expected: set[str],
    ctes: Mapping[str, exp.Expression],
    visiting: set[str],
) -> list[list[exp.Where]]:
    outer_where = branch.args.get("where")
    source_groups: list[list[exp.Where]] = []
    sources: list[exp.Expression] = []
    from_clause = branch.args.get("from_")
    if from_clause is not None:
        sources.append(from_clause.this)
    sources.extend(join.this for join in branch.args.get("joins") or [])

    for source in sources:
        relations = _relations_from_source(source, ctes)
        if expected and not (relations & expected):
            continue
        source_groups.extend(_source_filter_scope_groups(source, expected, ctes, visiting))

    if not source_groups:
        return [[outer_where]] if outer_where is not None else [[]]
    if outer_where is not None:
        return [[outer_where, *group] for group in source_groups]
    return source_groups


def _source_filter_scope_groups(
    source: exp.Expression,
    expected: set[str],
    ctes: Mapping[str, exp.Expression],
    visiting: set[str],
) -> list[list[exp.Where]]:
    query: exp.Expression | None = None
    cte_name: str | None = None
    if isinstance(source, exp.Table):
        cte_name = source.name.casefold()
        query = ctes.get(cte_name)
    elif isinstance(source, exp.Subquery):
        query = source.this
    if query is None or (cte_name is not None and cte_name in visiting):
        return []

    next_visiting = {*visiting, cte_name} if cte_name is not None else set(visiting)
    groups: list[list[exp.Where]] = []
    for branch in _output_branches(query):
        relations = _direct_branch_relations(branch, ctes)
        if expected and not (relations & expected):
            continue
        groups.extend(_branch_filter_scope_groups(branch, expected, ctes, next_visiting))
    return groups


def _relations_from_source(
    source: exp.Expression,
    ctes: Mapping[str, exp.Expression],
) -> set[str]:
    if isinstance(source, exp.Table):
        name = source.name.casefold()
        return _contributing_relations(ctes[name]) if name in ctes else {name}
    if isinstance(source, exp.Subquery):
        return _contributing_relations(source.this)
    return set()


def _output_branches(query: exp.Expression) -> list[exp.Select]:
    if isinstance(query, exp.SetOperation):
        return [*_output_branches(query.this), *_output_branches(query.expression)]
    if isinstance(query, exp.Select):
        return [query]
    select = next(query.find_all(exp.Select), None)
    return [select] if select is not None else []


def _direct_branch_relations(
    select: exp.Select,
    ctes: Mapping[str, exp.Expression],
) -> set[str]:
    relations: set[str] = set()

    def add_source(source: exp.Expression | None) -> None:
        if isinstance(source, exp.Table):
            name = source.name.casefold()
            if name in ctes:
                relations.update(_contributing_relations(ctes[name]))
            else:
                relations.add(name)
        elif isinstance(source, exp.Subquery):
            relations.update(_contributing_relations(source.this))

    from_clause = select.args.get("from_")
    add_source(from_clause.this if from_clause is not None else None)
    for join in select.args.get("joins") or []:
        add_source(join.this)
    return relations


def _virtual_schema(parsed: exp.Expression) -> dict[str, set[str]]:
    virtual: dict[str, set[str]] = {}
    for cte in parsed.find_all(exp.CTE):
        select = cte.this if isinstance(cte.this, exp.Select) else cte.this.find(exp.Select)
        if select is not None:
            alias_expression = cte.args.get("alias")
            explicit_columns = alias_expression.args.get("columns") if alias_expression is not None else None
            names = (
                [column.name for column in explicit_columns]
                if explicit_columns
                else select.named_selects
            )
            virtual[cte.alias_or_name.casefold()] = {
                name.casefold() for name in names if name and name != "*"
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
    grounded_filters: list[bool],
    grounded_time: bool,
    has_count: bool,
    semantically_covered_metrics: set[str],
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
    filter_results = iter(grounded_filters)
    for element in required:
        kind, _, value = element.partition(":")
        if kind == "entity" and value in tables:
            covered.append(element)
        elif kind == "output" and value in projections:
            covered.append(element)
        elif kind == "metric" and (
            value in projections
            or value in aggregates
            or value in semantically_covered_metrics
            or (value == "count" and has_count)
        ):
            covered.append(element)
        elif kind == "group" and value in groups:
            covered.append(element)
        elif kind == "filter":
            if next(filter_results, False):
                covered.append(element)
        elif kind == "time" and grounded_time:
            covered.append(element)
        elif kind == "order" and has_order:
            covered.append(element)
    return covered, required


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))
