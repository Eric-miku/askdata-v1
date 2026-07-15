from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


Confidence = Literal["high", "medium", "low"]


class TraceEvent(BaseModel):
    step: str
    status: Literal["started", "success", "retry", "warning", "error"]
    message: str
    sequence: int = 0


class ChartSpec(BaseModel):
    type: Literal["line", "vertical_bar", "horizontal_bar", "pie", "scatter"]
    title: str
    category_field: str | None = None
    category_label: str | None = None
    value_fields: list[str] = Field(default_factory=list)
    value_labels: dict[str, str] = Field(default_factory=dict)
    reason: Literal[
        "time_series", "comparison", "ranking", "proportion", "correlation"
    ]


class ResponseBase(BaseModel):
    session_id: str
    turn_id: str
    trace: list[TraceEvent] = Field(default_factory=list)


class AnswerResponse(ResponseBase):
    kind: Literal["answer"] = "answer"
    answer: str
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    chart: ChartSpec | None = None
    confidence: Confidence


class ClarificationOption(BaseModel):
    id: str
    label: str
    description: str | None = None


class ClarificationResponse(ResponseBase):
    kind: Literal["clarification"] = "clarification"
    clarification_id: str
    question: str
    options: list[ClarificationOption]
    recommended_option_id: str | None = None


class PartialResponse(ResponseBase):
    kind: Literal["partial"] = "partial"
    answer: str
    limitations: list[str]
    suggestions: list[str]
    confidence: Confidence
    sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    chart: ChartSpec | None = None


class ErrorResponse(ResponseBase):
    kind: Literal["error"] = "error"
    code: str
    message: str
    retryable: bool
    suggestions: list[str] = Field(default_factory=list)


QueryResponse = Annotated[
    AnswerResponse | ClarificationResponse | PartialResponse | ErrorResponse,
    Field(discriminator="kind"),
]
