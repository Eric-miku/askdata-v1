from pathlib import Path
import sys

import pytest
from pydantic import TypeAdapter, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.api.response_models import AnswerResponse, QueryResponse
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
