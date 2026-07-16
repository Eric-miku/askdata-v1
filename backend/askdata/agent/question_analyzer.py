"""Deterministic question analysis for Text2SQL planning."""

from __future__ import annotations

import re
from datetime import date
from typing import Literal, Mapping

from pydantic import BaseModel, Field

from askdata.agent.intent import IntentContract


_TEXT_FILTER_STOPWORDS = {
    "all",
    "average",
    "bottom",
    "count",
    "for",
    "from",
    "highest",
    "how",
    "least",
    "list",
    "lowest",
    "mean",
    "most",
    "number",
    "show",
    "top",
    "what",
    "when",
    "where",
    "which",
    "who",
    "year",
}


class QuestionFilter(BaseModel):
    raw: str
    kind: Literal["identifier", "number", "date", "text"]
    normalized: str | None = None


class QuestionAnalysis(BaseModel):
    intent: IntentContract
    requested_outputs: list[str] = Field(default_factory=list)
    filters: list[QuestionFilter] = Field(default_factory=list)
    formula_hints: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class QuestionAnalyzer:
    """Extract answer shape, requested outputs, literals, and formula hints."""

    def Analyze(
        self,
        question: str,
        schema: Mapping[str, list[str]],
        evidence: str = "",
    ) -> QuestionAnalysis:
        outputs = self._RequestedOutputs(question, schema)
        filters = self._Filters(question)
        formula_hints = self._FormulaHints(evidence)
        intent = self._Intent(question, outputs)
        return QuestionAnalysis(
            intent=intent,
            requested_outputs=outputs,
            filters=filters,
            formula_hints=formula_hints,
        )

    def _Intent(self, question: str, outputs: list[str]) -> IntentContract:
        lowered = question.casefold()
        if re.search(r"\b(percentage|percent|ratio|rate|share|decrease|increase)\b", lowered):
            return IntentContract(shape="ratio", metrics=["ratio"], expected_max_rows=1)
        if re.search(r"\b(how many|number of|count)\b", lowered):
            return IntentContract(shape="scalar", metrics=["count"], expected_max_rows=1)
        if re.search(r"\b(average|mean|avg)\b", lowered):
            return IntentContract(shape="scalar", metrics=outputs[-1:] or ["average"], expected_max_rows=1)
        if re.search(r"\b(top|bottom|highest|lowest|most|least|rank)\b", lowered):
            order = "ascending" if re.search(r"\b(bottom|lowest|least)\b", lowered) else "descending"
            return IntentContract(shape="ranking", output_attributes=outputs, order=order)
        return IntentContract(shape="listing", output_attributes=outputs)

    def _RequestedOutputs(self, question: str, schema: Mapping[str, list[str]]) -> list[str]:
        lowered = question.casefold()
        question_concepts = {self._NormalizeConcept(token) for token in re.findall(r"[a-z0-9]+", lowered)}
        outputs: list[str] = []
        for column in [column for columns in schema.values() for column in columns]:
            normalized = self._NormalizeConcept(column)
            if re.search(rf"\b{re.escape(column.casefold())}\b", lowered) or normalized in question_concepts:
                if column not in outputs:
                    outputs.append(column)
        return outputs

    def _Filters(self, question: str) -> list[QuestionFilter]:
        filters: list[QuestionFilter] = []
        seen: set[tuple[str, str]] = set()

        for raw in re.findall(r"\b\d{4}/\d{1,2}/\d{1,2}\b", question):
            normalized = self._NormalizeDate(raw)
            self._AddFilter(filters, seen, QuestionFilter(raw=raw, kind="date", normalized=normalized))

        for raw in re.findall(r"\b\d+\.\d+\b", question):
            self._AddFilter(filters, seen, QuestionFilter(raw=raw, kind="number", normalized=raw))

        for raw in re.findall(r"\b[A-Z]{1,5}\d{2,}\b", question):
            self._AddFilter(filters, seen, QuestionFilter(raw=raw, kind="identifier", normalized=raw))

        for raw in re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", question):
            if self._IsTextFilterStopword(raw):
                continue
            self._AddFilter(filters, seen, QuestionFilter(raw=raw, kind="text", normalized=raw))

        return filters

    @staticmethod
    def _AddFilter(filters: list[QuestionFilter], seen: set[tuple[str, str]], item: QuestionFilter) -> None:
        key = (item.raw, item.kind)
        if key not in seen:
            seen.add(key)
            filters.append(item)

    @staticmethod
    def _FormulaHints(evidence: str) -> list[str]:
        hints = []
        for part in evidence.split(";"):
            stripped = part.strip()
            if "=" in stripped and re.search(
                r"\b(rate|ratio|percentage|percent|decrease|increase|difference|average)\b", stripped, re.I
            ):
                hints.append(stripped)
        return hints

    @staticmethod
    def _NormalizeDate(raw: str) -> str | None:
        year, month, day = [int(part) for part in raw.split("/")]
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None

    @staticmethod
    def _IsTextFilterStopword(raw: str) -> bool:
        words = [word.casefold() for word in re.findall(r"[A-Za-z]+", raw)]
        return bool(words) and all(word in _TEXT_FILTER_STOPWORDS for word in words)

    @staticmethod
    def _NormalizeConcept(value: str) -> str:
        tokens = re.findall(r"[a-z0-9]+", value.casefold())
        normalized = "".join(tokens)
        if len(normalized) > 3 and normalized.endswith("ies"):
            return normalized[:-3] + "y"
        if len(normalized) > 3 and normalized.endswith(("ses", "xes", "zes", "ches", "shes")):
            return normalized[:-2]
        if len(normalized) > 3 and normalized.endswith("s"):
            return normalized[:-1]
        return normalized
