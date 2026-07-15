from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.agent.intent import IntentContract
from askdata.agent.sql_quality import (
    CandidateLedger,
    EvaluateResult,
    EvaluateStaticSql,
    QualityReport,
    SqlCandidate,
)


SCHEMA = {
    "items": {"id", "name", "category", "price"},
    "schools": {"id", "name", "score", "district_id"},
    "districts": {"id", "name"},
}


def candidate(
    sql: str,
    *,
    sequence: int,
    coverage: float,
    static_failures: list[str] | None = None,
    result_failures: list[str] | None = None,
    warnings: list[str] | None = None,
    directness: float = 1.0,
    execution_error: str | None = None,
    has_result_report: bool = True,
    static_coverage: float | None = None,
    static_directness: float = 1.0,
) -> SqlCandidate:
    return SqlCandidate(
        sql=sql,
        static_report=QualityReport(
            passed=not static_failures,
            failures=static_failures or [],
            warnings=warnings or [],
            coverage=coverage if static_coverage is None else static_coverage,
            directness=static_directness,
        ),
        result_report=(
            QualityReport(
                passed=not result_failures,
                failures=result_failures or [],
                coverage=coverage,
            )
            if has_result_report
            else None
        ),
        execution_error=execution_error,
        sequence=sequence,
        directness=directness,
    )


def test_count_contract_rejects_listing_sql():
    intent = IntentContract(shape="scalar", metrics=["count"], expected_max_rows=1)

    report = EvaluateStaticSql(intent, "SELECT name FROM items", SCHEMA)

    assert "missing_count_aggregation" in report.failures


def test_ranking_contract_requires_order_and_limit():
    intent = IntentContract(shape="ranking", order="descending", expected_max_rows=5)

    report = EvaluateStaticSql(intent, "SELECT name, score FROM schools", SCHEMA)

    assert {"missing_order", "missing_limit"} <= set(report.failures)


def test_static_check_rejects_unsafe_or_unknown_schema_references():
    unsafe = EvaluateStaticSql(IntentContract(shape="listing"), "DELETE FROM items", SCHEMA)
    unknown = EvaluateStaticSql(
        IntentContract(shape="listing"),
        "SELECT secret FROM missing_table",
        SCHEMA,
    )

    assert "unsafe_sql" in unsafe.failures
    assert {"unknown_table", "unknown_column"} <= set(unknown.failures)


def test_static_check_distinguishes_parse_failure_from_unsafe_statement():
    report = EvaluateStaticSql(IntentContract(shape="listing"), "SELECT FROM", SCHEMA)

    assert report.failures == ["invalid_sql"]


def test_static_check_accepts_cte_output_columns():
    report = EvaluateStaticSql(
        IntentContract(shape="listing", output_attributes=["name"]),
        "WITH named AS (SELECT name FROM items) SELECT named.name FROM named",
        SCHEMA,
    )

    assert "unknown_column" not in report.failures
    assert "unknown_table" not in report.failures


def test_static_check_treats_wildcard_as_covering_known_output_attribute():
    report = EvaluateStaticSql(
        IntentContract(shape="listing", output_attributes=["name"]),
        "SELECT * FROM items",
        SCHEMA,
    )

    assert "missing_output_attribute" not in report.failures


def test_static_check_requires_requested_projection_and_grouping():
    intent = IntentContract(
        shape="grouped",
        output_attributes=["category"],
        metrics=["count"],
        grouping=["category"],
    )

    report = EvaluateStaticSql(intent, "SELECT COUNT(*) AS count FROM items", SCHEMA)

    assert {"missing_output_attribute", "missing_grouping"} <= set(report.failures)


def test_static_check_detects_unconnected_join():
    report = EvaluateStaticSql(
        IntentContract(shape="listing", entities=["schools", "districts"]),
        "SELECT schools.name FROM schools JOIN districts",
        SCHEMA,
    )

    assert "unconnected_join" in report.failures


def test_static_check_detects_join_predicate_that_never_references_joined_table():
    report = EvaluateStaticSql(
        IntentContract(shape="listing", entities=["schools", "districts"]),
        "SELECT schools.name FROM schools JOIN districts ON schools.id = schools.id",
        SCHEMA,
    )

    assert "unconnected_join" in report.failures


def test_static_check_requires_join_predicate_to_reference_prior_relation():
    report = EvaluateStaticSql(
        IntentContract(shape="listing", entities=["schools", "districts"]),
        "SELECT s.name FROM schools AS s JOIN districts AS d ON d.id > 0",
        SCHEMA,
    )

    assert "unconnected_join" in report.failures


def test_static_check_accepts_connected_alias_join_and_cte_join():
    aliased = EvaluateStaticSql(
        IntentContract(shape="listing", entities=["schools", "districts"]),
        "SELECT s.name FROM schools AS s JOIN districts AS d ON d.id = s.district_id",
        SCHEMA,
    )
    with_cte = EvaluateStaticSql(
        IntentContract(shape="listing", output_attributes=["name"]),
        "WITH d AS (SELECT id FROM districts) "
        "SELECT s.name FROM schools AS s JOIN d ON d.id = s.district_id",
        SCHEMA,
    )

    assert "unconnected_join" not in aliased.failures
    assert "unconnected_join" not in with_cte.failures


def test_static_check_allows_explicit_cross_join_to_scalar_subquery():
    report = EvaluateStaticSql(
        IntentContract(
            shape="listing",
            output_attributes=["name", "total_count"],
            entities=["schools", "items"],
        ),
        "SELECT s.name, totals.total_count FROM schools AS s "
        "CROSS JOIN (SELECT COUNT(*) AS total_count FROM items) AS totals",
        SCHEMA,
    )

    assert "unconnected_join" not in report.failures


def test_static_check_maps_existing_answer_shape_messages_to_codes():
    report = EvaluateStaticSql(
        IntentContract(shape="listing", output_attributes=["name"]),
        "SELECT COUNT(*) FROM items",
        SCHEMA,
        question="List item names",
    )

    assert "listing_returns_only_aggregates" in report.failures


def test_legacy_show_and_give_words_do_not_override_structured_scalar_intent():
    average = EvaluateStaticSql(
        IntentContract(shape="scalar", metrics=["avg_price"], expected_max_rows=1),
        "SELECT AVG(price) AS avg_price FROM items",
        SCHEMA,
        question="Show average price",
    )
    count = EvaluateStaticSql(
        IntentContract(shape="scalar", metrics=["count"], expected_max_rows=1),
        "SELECT COUNT(*) AS total FROM items",
        SCHEMA,
        question="Give count of items",
    )

    assert "listing_returns_only_aggregates" not in average.failures
    assert "listing_returns_only_aggregates" not in count.failures
    assert "legacy_listing_returns_only_aggregates" not in average.warnings
    assert "legacy_listing_returns_only_aggregates" not in count.warnings


def test_static_check_does_not_credit_where_one_equals_one_for_filter():
    report = EvaluateStaticSql(
        IntentContract(shape="listing", filters=["status = open"]),
        "SELECT name FROM items WHERE 1 = 1",
        {**SCHEMA, "items": {*SCHEMA["items"], "status"}},
    )

    assert report.coverage == 0.0
    assert "unresolved_filter_alignment" in report.warnings


def test_static_check_credits_grounded_filter_and_time_condition():
    schema = {**SCHEMA, "items": {*SCHEMA["items"], "status", "year"}}
    report = EvaluateStaticSql(
        IntentContract(
            shape="listing",
            filters=["status = open"],
            time_condition="year = 2025",
        ),
        "SELECT name FROM items WHERE status = 'open' AND year = 2025",
        schema,
    )

    assert report.coverage == 1.0
    assert "unresolved_filter_alignment" not in report.warnings
    assert "unresolved_time_alignment" not in report.warnings


def test_static_check_does_not_credit_unresolved_natural_language_filter():
    schema = {**SCHEMA, "items": {*SCHEMA["items"], "status"}}
    report = EvaluateStaticSql(
        IntentContract(shape="listing", filters=["open school items"]),
        "SELECT name FROM items WHERE status = 'open'",
        schema,
    )

    assert report.coverage == 0.0
    assert "unresolved_filter_alignment" in report.warnings


def test_compound_query_requires_filter_grounding_in_every_branch():
    schema = {**SCHEMA, "items": {*SCHEMA["items"], "status"}}
    report = EvaluateStaticSql(
        IntentContract(shape="listing", filters=["status = open"]),
        "SELECT name FROM items WHERE status = 'open' "
        "UNION ALL SELECT name FROM items WHERE status = 'closed'",
        schema,
    )

    assert report.coverage == 0.0
    assert "unresolved_filter_alignment" in report.warnings


def test_outer_filter_does_not_need_repetition_in_nested_subquery():
    schema = {**SCHEMA, "items": {*SCHEMA["items"], "status", "school_id"}}
    report = EvaluateStaticSql(
        IntentContract(shape="listing", entities=["items"], filters=["status = open"]),
        "SELECT name FROM items WHERE status = 'open' AND school_id IN "
        "(SELECT id FROM schools WHERE score > 5)",
        schema,
    )

    assert "unresolved_filter_alignment" not in report.warnings
    assert "filter" in report.covered_elements


def test_compound_filter_only_checks_branches_for_requested_entity():
    schema = {**SCHEMA, "items": {*SCHEMA["items"], "status"}}
    report = EvaluateStaticSql(
        IntentContract(shape="listing", entities=["items"], filters=["status = open"]),
        "SELECT name FROM items WHERE status = 'open' "
        "UNION ALL SELECT name FROM schools",
        schema,
    )

    assert "unresolved_filter_alignment" not in report.warnings
    assert "filter" in report.covered_elements


def test_static_check_requires_every_requested_entity():
    report = EvaluateStaticSql(
        IntentContract(shape="listing", entities=["items", "schools"]),
        "SELECT name FROM items",
        SCHEMA,
    )

    assert "missing_entity" in report.failures


def test_unused_cte_does_not_satisfy_requested_entity_coverage():
    report = EvaluateStaticSql(
        IntentContract(shape="listing", entities=["items", "schools"]),
        "WITH unused AS (SELECT name FROM schools) SELECT name FROM items",
        SCHEMA,
    )

    assert "missing_entity" in report.failures
    assert "entity:schools" not in report.covered_elements


def test_static_check_marks_unrequested_projection_and_reduces_directness():
    report = EvaluateStaticSql(
        IntentContract(shape="listing", output_attributes=["name"]),
        "SELECT name, price FROM items",
        SCHEMA,
    )

    assert "unrequested_projection" in report.warnings
    assert report.directness == 0.5


def test_static_check_allows_grouping_and_order_helper_projections():
    report = EvaluateStaticSql(
        IntentContract(
            shape="ranking",
            output_attributes=["name"],
            metrics=["score"],
            grouping=["name"],
            order="descending",
            expected_max_rows=5,
        ),
        "SELECT name, score FROM schools GROUP BY name ORDER BY score DESC LIMIT 5",
        SCHEMA,
    )

    assert "unrequested_projection" not in report.warnings
    assert report.directness == 1.0


def test_static_check_accepts_ordering_by_aggregate_projection_alias():
    report = EvaluateStaticSql(
        IntentContract(
            shape="ranking",
            output_attributes=["category"],
            metrics=["count"],
            grouping=["category"],
            order="descending",
            expected_max_rows=5,
        ),
        "SELECT category, COUNT(*) AS total FROM items "
        "GROUP BY category ORDER BY total DESC LIMIT 5",
        SCHEMA,
    )

    assert "unknown_column" not in report.failures


def test_ranking_order_must_target_requested_metric():
    report = EvaluateStaticSql(
        IntentContract(
            shape="ranking",
            output_attributes=["name"],
            metrics=["score"],
            order="descending",
            expected_max_rows=5,
        ),
        "SELECT name, score FROM schools ORDER BY name DESC LIMIT 5",
        SCHEMA,
    )

    assert "wrong_order_target" in report.failures


def test_ranking_order_accepts_semantic_aggregate_alias():
    report = EvaluateStaticSql(
        IntentContract(
            shape="ranking",
            output_attributes=["category"],
            metrics=["count"],
            grouping=["category"],
            order="descending",
            expected_max_rows=5,
        ),
        "SELECT category, COUNT(*) AS total_count FROM items "
        "GROUP BY category ORDER BY total_count DESC LIMIT 5",
        SCHEMA,
    )

    assert "wrong_order_target" not in report.failures


def test_ranking_order_accepts_primary_aggregate_and_secondary_tiebreaker():
    report = EvaluateStaticSql(
        IntentContract(
            shape="ranking",
            output_attributes=["category"],
            metrics=["count"],
            grouping=["category"],
            order="descending",
            expected_max_rows=5,
        ),
        "SELECT category, COUNT(*) AS total FROM items GROUP BY category "
        "ORDER BY COUNT(*) DESC, category ASC LIMIT 5",
        SCHEMA,
    )

    assert "wrong_order_target" not in report.failures
    assert "wrong_order_direction" not in report.failures


def test_ranking_direction_checks_primary_order_term_only():
    report = EvaluateStaticSql(
        IntentContract(
            shape="ranking",
            output_attributes=["name"],
            metrics=["score"],
            order="descending",
            expected_max_rows=5,
        ),
        "SELECT name, score FROM schools ORDER BY score DESC, name ASC LIMIT 5",
        SCHEMA,
    )

    assert "wrong_order_direction" not in report.failures


def test_static_check_treats_count_aggregate_aliases_as_requested_metric():
    for alias in ("count", "total", "total_count"):
        report = EvaluateStaticSql(
            IntentContract(shape="scalar", metrics=["count"], expected_max_rows=1),
            f"SELECT COUNT(*) AS {alias} FROM items",
            SCHEMA,
        )

        assert "unrequested_projection" not in report.warnings
        assert report.directness == 1.0


def test_projection_lineage_allows_result_aliases_to_cover_metrics():
    count_intent = IntentContract(shape="grouped", grouping=["category"], metrics=["count"])
    count_static = EvaluateStaticSql(
        count_intent,
        "SELECT category, COUNT(*) AS total FROM items GROUP BY category",
        SCHEMA,
    )
    count_result = EvaluateResult(
        count_intent,
        ["category", "total"],
        [{"category": "A", "total": 2}],
        static_report=count_static,
    )
    average_intent = IntentContract(shape="scalar", metrics=["average_price"])
    average_static = EvaluateStaticSql(
        average_intent,
        "SELECT AVG(price) AS avg_price FROM items",
        SCHEMA,
    )
    average_result = EvaluateResult(
        average_intent,
        ["avg_price"],
        [{"avg_price": 4.5}],
        static_report=average_static,
    )

    assert count_result.coverage == 1.0
    assert "missing_result_metric" not in count_result.failures
    assert average_static.coverage == 1.0
    assert "unrequested_projection" not in average_static.warnings
    assert average_static.directness == 1.0
    assert average_result.coverage == 1.0
    assert "missing_result_metric" not in average_result.failures


def test_computed_ratio_lineage_treats_percentage_rate_and_ratio_as_equivalent():
    intent = IntentContract(shape="ratio", metrics=["percentage"])
    static_report = EvaluateStaticSql(
        intent,
        "SELECT SUM(price) * 100.0 / COUNT(*) AS ratio FROM items",
        SCHEMA,
    )
    result_report = EvaluateResult(
        intent,
        ["ratio"],
        [{"ratio": 25.0}],
        static_report=static_report,
    )

    assert static_report.coverage == 1.0
    assert "missing_metric" not in static_report.failures
    assert result_report.coverage == 1.0
    assert "missing_result_metric" not in result_report.failures


def test_compound_query_uses_root_order_limit_and_output_projection():
    report = EvaluateStaticSql(
        IntentContract(
            shape="ranking",
            output_attributes=["name"],
            metrics=["score"],
            order="descending",
            expected_max_rows=5,
        ),
        "SELECT name, score FROM schools WHERE score >= 10 "
        "UNION ALL SELECT name, score FROM schools WHERE score < 10 "
        "ORDER BY score DESC LIMIT 5",
        SCHEMA,
    )

    assert not ({"missing_order", "missing_limit", "wrong_order_target"} & set(report.failures))
    assert "missing_output_attribute" not in report.failures


def test_explicit_cte_column_alias_is_grounded():
    report = EvaluateStaticSql(
        IntentContract(shape="listing", output_attributes=["label"]),
        "WITH named(label) AS (SELECT name FROM items) SELECT label FROM named",
        SCHEMA,
    )

    assert "unknown_column" not in report.failures
    assert "missing_output_attribute" not in report.failures


def test_join_using_column_must_exist_on_both_sides():
    invalid = EvaluateStaticSql(
        IntentContract(shape="listing", entities=["schools", "districts"]),
        "SELECT schools.name FROM schools JOIN districts USING (district_id)",
        SCHEMA,
    )
    valid_schema = {
        **SCHEMA,
        "districts": {*SCHEMA["districts"], "district_id"},
    }
    valid = EvaluateStaticSql(
        IntentContract(shape="listing", entities=["schools", "districts"]),
        "SELECT schools.name FROM schools JOIN districts USING (district_id)",
        valid_schema,
    )

    assert "invalid_join_using" in invalid.failures
    assert "invalid_join_using" not in valid.failures


def test_static_check_rejects_raw_ratio_projection():
    report = EvaluateStaticSql(
        IntentContract(shape="ratio", metrics=["ratio"]),
        "SELECT price AS ratio FROM items LIMIT 1",
        SCHEMA,
    )

    assert "missing_ratio_computation" in report.failures


def test_static_check_rejects_aggregate_that_is_not_a_ratio():
    static_report = EvaluateStaticSql(
        IntentContract(shape="ratio", metrics=["ratio"]),
        "SELECT SUM(price) AS ratio FROM items",
        SCHEMA,
    )
    result_report = EvaluateResult(
        IntentContract(shape="ratio", metrics=["ratio"]),
        ["ratio"],
        [{"ratio": 12.5}],
        static_report=static_report,
    )

    assert "missing_ratio_computation" in static_report.failures
    assert "ratio_raw_output" in result_report.failures


def test_static_check_accepts_division_and_percentage_transformation():
    ratio = EvaluateStaticSql(
        IntentContract(shape="ratio", metrics=["ratio"]),
        "SELECT SUM(price) / COUNT(*) AS ratio FROM items",
        SCHEMA,
    )
    percentage = EvaluateStaticSql(
        IntentContract(shape="ratio", metrics=["percentage"]),
        "SELECT SUM(price) * 100.0 / COUNT(*) AS percentage FROM items",
        SCHEMA,
    )

    assert "missing_ratio_computation" not in ratio.failures
    assert "missing_ratio_computation" not in percentage.failures


def test_result_check_distinguishes_legitimate_empty_result():
    intent = IntentContract(shape="listing", output_attributes=["name"])

    suspicious = EvaluateResult(intent, ["name"], [])
    legitimate = EvaluateResult(intent, ["name"], [], empty_is_legitimate=True)

    assert "empty_result" in suspicious.failures
    assert "empty_result" not in legitimate.failures
    assert "legitimate_empty_result" in legitimate.warnings


def test_result_check_rejects_null_only_and_wrong_scalar_shape():
    null_only = EvaluateResult(
        IntentContract(shape="listing", output_attributes=["name"]),
        ["name"],
        [{"name": None}],
    )
    too_many = EvaluateResult(
        IntentContract(shape="scalar", expected_max_rows=1),
        ["count"],
        [{"count": 1}, {"count": 2}],
    )

    assert "null_only_result" in null_only.failures
    assert "too_many_rows" in too_many.failures


def test_result_check_rejects_scalar_and_ratio_with_multiple_outputs():
    scalar = EvaluateResult(
        IntentContract(shape="scalar"),
        ["count", "name"],
        [{"count": 1, "name": "pen"}],
    )
    ratio = EvaluateResult(
        IntentContract(shape="ratio"),
        ["numerator", "denominator"],
        [{"numerator": 1, "denominator": 2}],
    )

    assert "scalar_multiple_outputs" in scalar.failures
    assert "ratio_multiple_outputs" in ratio.failures


def test_result_check_rejects_suspicious_count_and_non_numeric_ratio():
    count = EvaluateResult(
        IntentContract(shape="scalar", metrics=["count"]),
        ["count"],
        [{"count": -1}],
    )
    ratio = EvaluateResult(
        IntentContract(shape="ratio"),
        ["ratio"],
        [{"ratio": "unknown"}],
    )

    assert "suspicious_count" in count.failures
    assert "ratio_non_numeric" in ratio.failures


def test_result_check_validates_count_even_when_column_uses_alias():
    report = EvaluateResult(
        IntentContract(shape="scalar", metrics=["count"]),
        ["total"],
        [{"total": -1}],
    )

    assert "suspicious_count" in report.failures


def test_result_check_marks_raw_ratio_from_static_context():
    static_report = QualityReport(
        passed=False,
        failures=["missing_ratio_computation"],
        coverage=0.5,
    )

    report = EvaluateResult(
        IntentContract(shape="ratio"),
        ["ratio"],
        [{"ratio": 0.5}],
        static_report=static_report,
    )

    assert "ratio_raw_output" in report.failures


def test_result_check_requires_grouping_output_and_valid_ranking_order():
    grouped = EvaluateResult(
        IntentContract(shape="grouped", grouping=["category"], metrics=["count"]),
        ["count"],
        [{"count": 2}],
    )
    ranking = EvaluateResult(
        IntentContract(shape="ranking", metrics=["score"], order="descending"),
        ["name", "score"],
        [{"name": "A", "score": 8}, {"name": "B", "score": 10}],
    )

    assert "missing_result_grouping" in grouped.failures
    assert "ranking_order_mismatch" in ranking.failures


def test_result_check_rejects_inspection_query_as_final_listing():
    report = EvaluateResult(
        IntentContract(shape="listing", output_attributes=["name"]),
        ["id"],
        [{"id": 1}],
    )

    assert "inspection_query_result" in report.failures


def test_result_check_calculates_requested_output_coverage():
    intent = IntentContract(shape="listing", output_attributes=["name", "price"])

    report = EvaluateResult(intent, ["name"], [{"name": "pen"}])

    assert report.coverage == 0.5
    assert report.covered_elements == ["output:name"]
    assert "missing_result_attribute" in report.failures


def test_candidate_ledger_prefers_complete_older_candidate_to_recent_inspection():
    ledger = CandidateLedger()
    ledger.Add(candidate("SELECT COUNT(*) AS count FROM items", sequence=1, coverage=1.0))
    ledger.Add(candidate("SELECT id FROM items", sequence=2, coverage=0.4))

    assert ledger.SelectBest().sql.startswith("SELECT COUNT")


def test_candidate_ledger_excludes_execution_errors_and_uses_directness_before_age():
    ledger = CandidateLedger()
    ledger.Add(candidate("SELECT broken", sequence=1, coverage=1.0, execution_error="boom"))
    ledger.Add(candidate("SELECT id, name, price FROM items", sequence=2, coverage=1.0, directness=0.5))
    ledger.Add(candidate("SELECT name FROM items", sequence=3, coverage=1.0, directness=1.0))

    assert ledger.SelectBest().sql == "SELECT name FROM items"


def test_candidate_ledger_never_selects_unsafe_sql_over_safe_candidate():
    ledger = CandidateLedger()
    ledger.Add(candidate("DELETE FROM items", sequence=1, coverage=1.0, static_failures=["unsafe_sql"]))
    ledger.Add(candidate("SELECT name FROM items", sequence=2, coverage=0.8))

    assert ledger.SelectBest().sql == "SELECT name FROM items"


def test_candidate_ledger_returns_none_without_successful_execution():
    ledger = CandidateLedger()
    ledger.Add(candidate("SELECT broken", sequence=1, coverage=1.0, execution_error="boom"))

    assert ledger.SelectBest() is None


def test_candidate_ledger_excludes_candidate_without_result_report():
    ledger = CandidateLedger()
    ledger.Add(candidate("SELECT name FROM items", sequence=1, coverage=1.0, has_result_report=False))

    assert ledger.SelectBest() is None


def test_candidate_ledger_combines_static_and_result_coverage():
    ledger = CandidateLedger()
    ledger.Add(
        candidate(
            "SELECT name FROM items",
            sequence=2,
            coverage=1.0,
            static_coverage=1.0,
        )
    )
    ledger.Add(
        candidate(
            "SELECT id FROM items",
            sequence=1,
            coverage=1.0,
            static_coverage=0.4,
        )
    )

    assert ledger.SelectBest().sql == "SELECT name FROM items"


def test_candidate_ledger_complete_older_candidate_beats_incomplete_recent_candidate():
    ledger = CandidateLedger()
    ledger.Add(candidate("SELECT name FROM items", sequence=1, coverage=1.0, static_coverage=1.0))
    ledger.Add(candidate("SELECT id FROM items", sequence=2, coverage=1.0, static_coverage=0.4))

    assert ledger.SelectBest().sql == "SELECT name FROM items"


def test_candidate_ledger_uses_static_projection_directness():
    ledger = CandidateLedger()
    ledger.Add(
        candidate(
            "SELECT name, price FROM items",
            sequence=1,
            coverage=1.0,
            static_directness=0.5,
        )
    )
    ledger.Add(
        candidate(
            "SELECT name FROM items",
            sequence=2,
            coverage=1.0,
            static_directness=1.0,
        )
    )

    assert ledger.SelectBest().sql == "SELECT name FROM items"


def test_candidate_ledger_prefers_fewer_failures_when_coverage_matches():
    ledger = CandidateLedger()
    ledger.Add(
        candidate(
            "SELECT id FROM items",
            sequence=1,
            coverage=1.0,
            static_failures=["missing_output_attribute", "missing_metric"],
        )
    )
    ledger.Add(
        candidate(
            "SELECT name FROM items",
            sequence=2,
            coverage=1.0,
            static_failures=["missing_metric"],
        )
    )

    assert ledger.SelectBest().sql == "SELECT name FROM items"
