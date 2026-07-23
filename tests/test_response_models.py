from pathlib import Path
import sys

import pytest
from pydantic import TypeAdapter, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.api.response_models import (
    AnswerResponse,
    ChartSpec,
    ClarificationResponse,
    ErrorResponse,
    PartialResponse,
    QueryResponse,
    TraceEvent,
)
from askdata.api.schemas import ClarificationResolution, QueryRequest


def test_query_response_parses_answer_variant():
    response = TypeAdapter(QueryResponse).validate_python(
        {
            "kind": "answer",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "answer": "Sales increased.",
            "sql": "SELECT month, sales FROM revenue",
            "columns": ["month", "sales"],
            "rows": [{"month": "January", "sales": 120}],
            "chart": {
                "type": "line",
                "title": "Monthly sales",
                "category_field": "month",
                "category_label": "Month",
                "value_fields": ["sales"],
                "value_labels": {"sales": "Sales"},
                "reason": "time_series",
            },
            "confidence": "high",
            "trace": [
                {
                    "step": "execute_sql",
                    "status": "success",
                    "message": "Query completed.",
                    "sequence": 1,
                }
            ],
        }
    )

    assert isinstance(response, AnswerResponse)
    assert response.kind == "answer"
    assert response.session_id == "session-1"
    assert response.turn_id == "turn-1"
    assert response.chart is not None
    assert response.chart.type == "line"
    assert response.confidence == "high"
    assert response.trace[0].status == "success"


@pytest.mark.parametrize(
    ("payload", "expected_question", "expected_option_id", "expected_text"),
    [
        ({"question": "  How many rows?  "}, "How many rows?", None, None),
        (
            {
                "question": "   ",
                "clarification": {
                    "clarification_id": "clarification-1",
                    "option_id": "  option-1  ",
                }
            },
            None,
            "option-1",
            None,
        ),
    ],
)
def test_query_request_accepts_exactly_one_nonblank_input(
    payload, expected_question, expected_option_id, expected_text
):
    request = QueryRequest(database_id="demo", **payload)

    assert request.question == expected_question
    if request.clarification is not None:
        assert request.clarification.option_id == expected_option_id
        assert request.clarification.text == expected_text


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"question": "   "},
        {
            "question": "How many rows?",
            "clarification": {
                "clarification_id": "clarification-1",
                "option_id": "option-1",
            },
        },
    ],
)
def test_query_request_rejects_missing_blank_or_multiple_inputs(payload):
    with pytest.raises(ValidationError):
        QueryRequest(database_id="demo", **payload)


@pytest.mark.parametrize(
    ("payload", "expected_option_id", "expected_text"),
    [
        (
            {"option_id": "  option-1  ", "text": "   "},
            "option-1",
            None,
        ),
        (
            {"option_id": "   ", "text": "  Use the latest year  "},
            None,
            "Use the latest year",
        ),
    ],
)
def test_clarification_resolution_accepts_exactly_one_resolution(
    payload, expected_option_id, expected_text
):
    resolution = ClarificationResolution(
        clarification_id="clarification-1", **payload
    )

    assert resolution.option_id == expected_option_id
    assert resolution.text == expected_text


def test_clarification_resolution_strips_nonblank_id():
    resolution = ClarificationResolution(
        clarification_id="  clarification-1  ", option_id="option-1"
    )

    assert resolution.clarification_id == "clarification-1"


@pytest.mark.parametrize("clarification_id", ["", "   "])
def test_clarification_resolution_rejects_blank_id(clarification_id):
    with pytest.raises(ValidationError):
        ClarificationResolution(
            clarification_id=clarification_id, option_id="option-1"
        )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"option_id": "   "},
        {"text": "   "},
        {"option_id": "option-1", "text": "Use the latest year"},
    ],
)
def test_clarification_resolution_rejects_missing_blank_or_multiple_resolutions(
    payload,
):
    with pytest.raises(ValidationError):
        ClarificationResolution(clarification_id="clarification-1", **payload)


def test_query_response_rejects_invalid_discriminator():
    with pytest.raises(ValidationError):
        TypeAdapter(QueryResponse).validate_python(
            {"kind": "unknown", "session_id": "session-1", "turn_id": "turn-1"}
        )


def test_query_response_rejects_invalid_confidence():
    with pytest.raises(ValidationError):
        TypeAdapter(QueryResponse).validate_python(
            {
                "kind": "answer",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "answer": "Sales increased.",
                "sql": "SELECT 1",
                "columns": ["result"],
                "rows": [{"result": 1}],
                "confidence": "certain",
            }
        )


def test_query_response_parses_clarification_variant_and_strips_id():
    response = TypeAdapter(QueryResponse).validate_python(
        {
            "kind": "clarification",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "clarification_id": "  clarification-1  ",
            "question": "Which year should be used?",
            "options": [
                {
                    "id": "latest",
                    "label": "Latest year",
                    "description": "Use the newest available year.",
                }
            ],
            "recommended_option_id": "latest",
        }
    )

    assert isinstance(response, ClarificationResponse)
    assert response.clarification_id == "clarification-1"
    assert response.options[0].id == "latest"
    assert response.trace == []


@pytest.mark.parametrize("clarification_id", ["", "   "])
def test_clarification_response_rejects_blank_id(clarification_id):
    with pytest.raises(ValidationError):
        TypeAdapter(QueryResponse).validate_python(
            {
                "kind": "clarification",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "clarification_id": clarification_id,
                "question": "Which year should be used?",
                "options": [],
            }
        )


def test_query_response_parses_partial_variant_with_defaults():
    response = TypeAdapter(QueryResponse).validate_python(
        {
            "kind": "partial",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "answer": "Only recent data was available.",
            "limitations": ["Historical rows are missing."],
            "suggestions": ["Try a narrower date range."],
            "confidence": "low",
        }
    )

    assert isinstance(response, PartialResponse)
    assert response.columns == []
    assert response.rows == []
    assert response.chart is None
    assert response.sql is None


def test_partial_response_rejects_invalid_confidence():
    with pytest.raises(ValidationError):
        TypeAdapter(QueryResponse).validate_python(
            {
                "kind": "partial",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "answer": "Only recent data was available.",
                "limitations": [],
                "suggestions": [],
                "confidence": "certain",
            }
        )


def test_query_response_parses_error_variant_with_isolated_defaults():
    adapter = TypeAdapter(QueryResponse)
    first = adapter.validate_python(
        {
            "kind": "error",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "code": "query_failed",
            "message": "The query failed.",
            "retryable": True,
        }
    )
    second = adapter.validate_python(
        {
            "kind": "error",
            "session_id": "session-1",
            "turn_id": "turn-2",
            "code": "query_failed",
            "message": "The query failed again.",
            "retryable": False,
        }
    )

    assert isinstance(first, ErrorResponse)
    first.suggestions.append("Try again later.")
    assert second.suggestions == []


def test_error_response_rejects_missing_retryable_flag():
    with pytest.raises(ValidationError):
        TypeAdapter(QueryResponse).validate_python(
            {
                "kind": "error",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "code": "query_failed",
                "message": "The query failed.",
            }
        )


def test_chart_spec_accepts_enums_and_has_isolated_defaults():
    first = ChartSpec(type="scatter", title="Correlation", reason="correlation")
    second = ChartSpec(type="pie", title="Share", reason="proportion")

    first.value_fields.append("revenue")
    first.value_labels["revenue"] = "Revenue"

    assert second.value_fields == []
    assert second.value_labels == {}


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "area", "title": "Trend", "reason": "time_series"},
        {"type": "line", "title": "Trend", "reason": "forecast"},
    ],
)
def test_chart_spec_rejects_invalid_enums(payload):
    with pytest.raises(ValidationError):
        ChartSpec(**payload)


def test_trace_event_rejects_invalid_status():
    with pytest.raises(ValidationError):
        TraceEvent(step="execute_sql", status="complete", message="Done.")
