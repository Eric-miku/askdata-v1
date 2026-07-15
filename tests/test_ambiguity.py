import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.agent.ambiguity import (
    AmbiguityDecision,
    AmbiguityGate,
    Interpretation,
    StructuredInterpreter,
)
from askdata.agent.pipeline import StagedSqlPipeline


def interpretation(identifier, *, metric=None, entities=None, **overrides):
    return Interpretation(
        id=identifier,
        label=overrides.pop("label", identifier.replace("_", " ").title()),
        entities=entities or ["sales"],
        metric=metric,
        supported_by=overrides.pop(
            "supported_by",
            [f"sales.{metric}"] if metric else ["sales"],
        ),
        **overrides,
    )


class FakeInterpreter:
    def __init__(self, interpretations):
        self.interpretations = interpretations

    def Interpret(self, question, schema, evidence="", session_context=None):
        return self.interpretations


def revenue_schema():
    return {"sales": ["id", "gross_revenue", "net_revenue"]}


def test_two_revenue_metrics_require_clarification():
    result = AmbiguityGate(
        FakeInterpreter(
            [
                interpretation("gross", metric="gross_revenue"),
                interpretation("net", metric="net_revenue"),
            ]
        )
    ).Check("show revenue", revenue_schema())

    assert result.state == "materially_ambiguous"
    assert [option.id for option in result.options] == ["gross", "net"]


def test_one_schema_supported_interpretation_proceeds():
    result = AmbiguityGate(
        FakeInterpreter(
            [
                interpretation(
                    "enrollment",
                    entities=["schools"],
                    metric="Enrollment",
                    supported_by=["schools.Enrollment"],
                )
            ]
        )
    ).Check("top schools", {"schools": ["School", "Enrollment"]})

    assert result.state == "clear"


def test_missing_student_entity_is_unanswerable_not_ambiguous():
    result = AmbiguityGate(FakeInterpreter([])).Check(
        "list student names", {"schools": ["School", "Enrollment"]}
    )

    assert result.state == "unanswerable"
    assert result.missing_concepts


def test_hallucinated_interpretation_is_not_counted_as_supported():
    result = AmbiguityGate(
        FakeInterpreter(
            [
                interpretation("gross", metric="gross_revenue"),
                interpretation(
                    "profit",
                    metric="profit",
                    supported_by=["sales.profit"],
                ),
            ]
        )
    ).Check("show performance", revenue_schema())

    assert result.state == "clear"
    assert result.resolved_question is not None


@pytest.mark.parametrize("condition", ["EdOpsCode = 'SSS'", "EdOpsCode = SSS"])
def test_business_evidence_can_support_nonlexical_semantic_mapping(condition):
    special = Interpretation(
        id="state_special",
        label="State special schools",
        entities=["schools"],
        filters=[condition],
        supported_by=["schools.EdOpsCode", "evidence:State Special School means SSS"],
    )
    result = AmbiguityGate(FakeInterpreter([special])).Check(
        "list state special schools",
        {"schools": ["School", "EdOpsCode"]},
        evidence="State Special School means EdOpsCode = 'SSS'.",
    )

    assert result.state == "clear"


def test_city_literal_explicitly_supplied_by_user_is_schema_grounded():
    boston = Interpretation(
        id="boston",
        label="Customers in Boston",
        entities=["customers"],
        filters=["city = 'Boston'"],
        supported_by=["customers.city"],
    )

    result = AmbiguityGate(FakeInterpreter([boston])).Check(
        "list customers in Boston",
        {"customers": ["id", "name", "city"]},
    )

    assert result.state == "clear"


def test_year_literal_explicitly_supplied_by_user_is_schema_grounded():
    year = Interpretation(
        id="year_2025",
        label="Revenue for 2025",
        entities=["sales"],
        filters=["fiscal_year = '2025'"],
        supported_by=["sales.fiscal_year"],
    )

    result = AmbiguityGate(FakeInterpreter([year])).Check(
        "show revenue for 2025",
        {"sales": ["revenue", "fiscal_year"]},
    )

    assert result.state == "clear"


@pytest.mark.parametrize("condition", ["EdOpsCode = 'SSS'", "EdOpsCode = SSS"])
def test_inferred_coded_literal_without_business_evidence_is_rejected(condition):
    inferred = Interpretation(
        id="state_special",
        label="State special schools",
        entities=["schools"],
        filters=[condition],
        supported_by=["schools.EdOpsCode"],
    )

    result = AmbiguityGate(FakeInterpreter([inferred])).Check(
        "list state special schools",
        {"schools": ["School", "EdOpsCode"]},
    )

    assert result.state == "unanswerable"


def test_equivalent_supported_interpretations_do_not_trigger_clarification():
    result = AmbiguityGate(
        FakeInterpreter(
            [
                interpretation("gross", metric="gross_revenue", label="Gross revenue"),
                interpretation("gross_total", metric="gross_revenue", label="Total gross revenue"),
            ]
        )
    ).Check("show revenue", revenue_schema())

    assert result.state == "resolvable_from_context"
    assert result.options == []


def test_session_context_resolves_otherwise_material_metric_ambiguity():
    result = AmbiguityGate(
        FakeInterpreter(
            [
                interpretation("gross", metric="gross_revenue"),
                interpretation("net", metric="net_revenue"),
            ]
        )
    ).Check(
        "show revenue",
        revenue_schema(),
        session_context={"last_question": "Compare net revenue by month"},
    )

    assert result.state == "resolvable_from_context"
    assert "net" in result.resolved_question.casefold()


def test_structured_interpreter_uses_tool_schema_and_parses_interpretations():
    class RecordingLlm:
        def __init__(self):
            self.tools = None

        def Chat(self, messages, tools=None):
            self.tools = tools
            payload = {
                "interpretations": [
                    interpretation("gross", metric="gross_revenue").model_dump()
                ],
                "missing_concepts": [],
            }
            call = SimpleNamespace(
                id="interpret-1",
                function=SimpleNamespace(
                    name="submit_interpretations", arguments=json.dumps(payload)
                ),
            )
            return SimpleNamespace(content="", tool_calls=[call])

    llm = RecordingLlm()
    values = StructuredInterpreter(llm).Interpret(
        "show revenue", revenue_schema(), evidence="Revenue definitions"
    )

    assert values[0].metric == "gross_revenue"
    tool = llm.tools[0]["function"]
    assert tool["name"] == "submit_interpretations"
    assert tool["parameters"]["additionalProperties"] is False


def test_pipeline_returns_clarification_before_generating_or_executing_sql():
    class NeverReact:
        def GenerateCandidates(self, *args, **kwargs):
            raise AssertionError("SQL generation must wait for clarification")

    decision = AmbiguityDecision(
        state="materially_ambiguous",
        options=[
            {"id": "gross", "label": "Gross revenue"},
            {"id": "net", "label": "Net revenue"},
        ],
        interpretations=[
            interpretation("gross", metric="gross_revenue"),
            interpretation("net", metric="net_revenue"),
        ],
    )

    class Gate:
        def Check(self, *args, **kwargs):
            return decision

    result = StagedSqlPipeline(
        react=NeverReact(),
        ambiguity_gate=Gate(),
        runner=lambda *_: (_ for _ in ()).throw(AssertionError("must not execute")),
    ).Run(
        question="show revenue",
        retrieval={
            "schema": revenue_schema(),
            "schema_prompt": "Table sales(gross_revenue real, net_revenue real)",
        },
    )

    assert result["kind"] == "clarification"
    assert [option["id"] for option in result["options"]] == ["gross", "net"]
    assert result["interpretations"][0]["metric"] == "gross_revenue"
