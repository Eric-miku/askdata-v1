"""Gold-independent checks that compare question intent with SQL output shape."""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp


def CheckAnswerShape(question: str, sql: str) -> list[str]:
    try:
        parsed = sqlglot.parse_one(sql or "", read="sqlite")
    except Exception:
        return ["SQL could not be parsed for answer-shape review."]
    select = parsed if isinstance(parsed, exp.Select) else parsed.find(exp.Select)
    if select is None or not select.expressions:
        return ["SQL could not be parsed for answer-shape review."]

    lowered = (question or "").strip().lower()
    expressions = list(select.expressions)
    warnings: list[str] = []

    asks_average = bool(re.search(r"\b(average|mean)\b", lowered))
    asks_count = not asks_average and bool(re.search(r"\b(how many|number of|count of)\b", lowered))
    has_count = any(expression.find(exp.Count) is not None for expression in expressions)
    if asks_count and not has_count:
        warnings.append("Question asks for a count, but SQL does not use COUNT.")

    asks_final_value = bool(re.search(r"\b(percentage|percent|ratio|rate)\b", lowered))
    if asks_final_value and len(expressions) > 1:
        warnings.append("Question asks for one final computed value, but SQL selects multiple expressions.")
    if asks_final_value and len(expressions) == 1:
        has_computation = any(
            expressions[0].find(kind) is not None
            for kind in (exp.Div, exp.Mul, exp.Sub, exp.Add, exp.AggFunc)
        )
        if not has_computation:
            warnings.append("Question asks for a computed percentage, ratio, or rate, but SQL returns a raw value.")

    asks_list = bool(re.search(r"\b(list|show|give)\b", lowered))
    aggregate_only = all(expression.find(exp.AggFunc) is not None for expression in expressions)
    if asks_list and aggregate_only:
        warnings.append("Question asks for a list, but SQL returns only aggregate expressions.")

    asks_top_entity = bool(re.match(r"^(which|who)\b", lowered)) and bool(
        re.search(r"\b(top|most|least|highest|lowest|best|worst)\b", lowered)
    )
    explicitly_asks_supporting_value = bool(re.search(r"\b(and|with|along with|as well as)\b", lowered))
    if asks_top_entity and not explicitly_asks_supporting_value and len(expressions) > 1:
        warnings.append("Question asks for the top entity only; remove unrequested helper columns.")

    return warnings
