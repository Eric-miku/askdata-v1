from pydantic import ValidationError
import pytest

from askdata.agent.intent import IntentContract


def test_intent_contract_preserves_structured_requirements():
    intent = IntentContract(
        shape="ranking",
        entities=["schools"],
        output_attributes=["name"],
        metrics=["score"],
        filters=["open schools"],
        grouping=["district"],
        order="descending",
        expected_max_rows=5,
        time_condition="2025",
    )

    assert intent.entities == ["schools"]
    assert intent.expected_max_rows == 5


def test_intent_contract_rejects_non_positive_expected_row_limit():
    with pytest.raises(ValidationError):
        IntentContract(shape="listing", expected_max_rows=0)
