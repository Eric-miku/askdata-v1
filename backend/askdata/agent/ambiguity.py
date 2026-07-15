"""Schema-grounded material ambiguity decisions for natural-language queries."""

from __future__ import annotations

import json
import re
from typing import Any, Literal, Mapping

from pydantic import BaseModel, Field

from askdata.api.response_models import ClarificationOption


_INTERPRETATION_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_interpretations",
        "description": (
            "Return only plausible meanings of the user's question that the supplied "
            "database schema or business evidence can answer. Do not invent proxy entities."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "interpretations": {
                    "type": "array",
                    "maxItems": 5,
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "label": {"type": "string"},
                            "entities": {"type": "array", "items": {"type": "string"}},
                            "metric": {"type": "string"},
                            "filters": {"type": "array", "items": {"type": "string"}},
                            "aggregation": {"type": "string"},
                            "grouping": {"type": "array", "items": {"type": "string"}},
                            "time_range": {"type": "string"},
                            "ranking": {"type": "string"},
                            "supported_by": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["id", "label", "entities", "supported_by"],
                        "additionalProperties": False,
                    },
                },
                "missing_concepts": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["interpretations", "missing_concepts"],
            "additionalProperties": False,
        },
    },
}


class Interpretation(BaseModel):
    id: str
    label: str
    entities: list[str]
    metric: str | None = None
    filters: list[str] = Field(default_factory=list)
    aggregation: str | None = None
    grouping: list[str] = Field(default_factory=list)
    time_range: str | None = None
    ranking: str | None = None
    supported_by: list[str] = Field(default_factory=list)


class AmbiguityDecision(BaseModel):
    state: Literal[
        "clear", "resolvable_from_context", "materially_ambiguous", "unanswerable"
    ]
    resolved_question: str | None = None
    question: str | None = None
    options: list[ClarificationOption] = Field(default_factory=list)
    missing_concepts: list[str] = Field(default_factory=list)
    interpretations: list[Interpretation] = Field(default_factory=list)


class StructuredInterpreter:
    """Ask an OpenAI-compatible chat client for interpretations via a tool schema."""

    def __init__(self, llm_client) -> None:
        self.llm_client = llm_client
        self.last_missing_concepts: list[str] = []

    def Interpret(
        self,
        question: str,
        schema: Mapping[str, list[str]],
        evidence: str = "",
        session_context: Mapping[str, Any] | None = None,
    ) -> list[Interpretation] | None:
        context = json.dumps(session_context or {}, ensure_ascii=False, default=str)
        prompt = (
            "Identify answerable interpretations of the user question. Use the "
            "submit_interpretations tool. An interpretation is not valid merely because "
            "it sounds related: cite schema fields or supplied business evidence. Return "
            "multiple choices only when choosing one would materially change the query.\n\n"
            f"Question: {question}\n"
            f"Schema: {json.dumps(schema, ensure_ascii=False)}\n"
            f"Business evidence: {evidence}\n"
            f"Conversation context: {context}"
        )
        message = self.llm_client.Chat(
            [{"role": "user", "content": prompt}], tools=[_INTERPRETATION_TOOL]
        )
        for tool_call in getattr(message, "tool_calls", None) or []:
            if getattr(tool_call.function, "name", "") != "submit_interpretations":
                continue
            try:
                payload = json.loads(tool_call.function.arguments)
                self.last_missing_concepts = [
                    str(item) for item in payload.get("missing_concepts", []) if str(item).strip()
                ][:5]
                return [
                    Interpretation.model_validate(item)
                    for item in payload.get("interpretations", [])[:5]
                ]
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
        return None


class AmbiguityGate:
    """Ask only for material choices that are independently grounded in context."""

    def __init__(self, interpreter) -> None:
        self.interpreter = interpreter

    def Check(
        self,
        question: str,
        schema: Mapping[str, list[str]],
        evidence: str = "",
        session_context: Mapping[str, Any] | None = None,
    ) -> AmbiguityDecision:
        raw = self.interpreter.Interpret(
            question, schema, evidence=evidence, session_context=session_context
        )
        # A malformed or unavailable interpreter is not evidence that the database cannot
        # answer the question. Fail open and let deterministic SQL quality gates decide.
        if raw is None:
            return AmbiguityDecision(state="clear", resolved_question=question)

        candidates = [
            item if isinstance(item, Interpretation) else Interpretation.model_validate(item)
            for item in raw
        ]
        supported_by_id = {}
        for item in candidates:
            if (
                item.id.strip()
                and item.label.strip()
                and self._IsSupported(item, schema, evidence, question)
            ):
                supported_by_id.setdefault(item.id, item)
        supported = list(supported_by_id.values())
        if not supported:
            missing = list(getattr(self.interpreter, "last_missing_concepts", []) or [])
            return AmbiguityDecision(
                state="unanswerable",
                missing_concepts=missing or self._MissingConcepts(question, schema),
            )

        if len(supported) == 1:
            chosen = supported[0]
            return AmbiguityDecision(
                state="clear",
                resolved_question=self._ResolveQuestion(question, chosen),
                interpretations=[chosen],
            )

        signatures = {self._MaterialSignature(item) for item in supported}
        if len(signatures) == 1:
            chosen = supported[0]
            return AmbiguityDecision(
                state="resolvable_from_context",
                resolved_question=self._ResolveQuestion(question, chosen),
                interpretations=supported,
            )

        dominant = self._DominantFromContext(supported, session_context, evidence)
        if dominant is not None:
            return AmbiguityDecision(
                state="resolvable_from_context",
                resolved_question=self._ResolveQuestion(question, dominant),
                interpretations=supported,
            )

        options = [
            ClarificationOption(
                id=item.id,
                label=item.label,
                description=self._Description(item),
            )
            for item in supported
        ]
        return AmbiguityDecision(
            state="materially_ambiguous",
            question=f"Which interpretation of “{question}” should I use?",
            options=options,
            interpretations=supported,
        )

    @classmethod
    def _IsSupported(
        cls,
        item: Interpretation,
        schema: Mapping[str, list[str]],
        evidence: str,
        question: str,
    ) -> bool:
        table_names = {cls._Normalize(name) for name in schema}
        column_names = {
            cls._Normalize(column) for columns in schema.values() for column in columns
        }
        field_names = {
            cls._Normalize(f"{table}.{column}")
            for table, columns in schema.items()
            for column in columns
        }
        evidence_normalized = cls._Normalize(evidence)
        question_tokens = set(cls._Tokens(question))
        evidence_tokens = set(cls._Tokens(evidence))

        def concept_supported(value: str) -> bool:
            normalized = cls._Normalize(value)
            return bool(
                normalized
                and (
                    normalized in table_names
                    or normalized in column_names
                    or normalized in field_names
                    or normalized in evidence_normalized
                )
            )

        if not item.supported_by or not any(
            concept_supported(reference.removeprefix("evidence:"))
            for reference in item.supported_by
        ):
            return False
        if any(not concept_supported(entity) for entity in item.entities):
            return False
        if item.metric and not concept_supported(item.metric):
            return False
        if any(not concept_supported(group) for group in item.grouping):
            return False
        for condition in item.filters:
            identifiers = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", condition)
            if identifiers and not concept_supported(identifiers[0]):
                return False
            literals = re.findall(r"['\"]([^'\"]+)['\"]", condition)
            condition_without_quoted_values = re.sub(
                r"(['\"])(?:\\.|(?!\1).)*\1", "", condition
            )
            for bare_value in re.findall(
                r"(?:=|!=|<>|>=|<=|>|<)\s*([A-Za-z0-9_.+-]+)",
                condition_without_quoted_values,
            ):
                if not concept_supported(bare_value):
                    literals.append(bare_value)
            if literals and any(
                not cls._LiteralSupported(
                    value,
                    question,
                    evidence,
                    question_tokens,
                    evidence_tokens,
                    evidence_normalized,
                )
                for value in literals
            ):
                return False
        return True

    @classmethod
    def _LiteralSupported(
        cls,
        value: str,
        question: str,
        evidence: str,
        question_tokens: set[str],
        evidence_tokens: set[str],
        evidence_normalized: str,
    ) -> bool:
        tokens = cls._Tokens(value)
        if len(tokens) == 1:
            return tokens[0] in question_tokens or tokens[0] in evidence_tokens
        if tokens:
            normalized = "".join(tokens)
            return normalized in cls._Normalize(question) or normalized in evidence_normalized
        literal = value.strip().casefold()
        return bool(
            literal
            and (literal in question.casefold() or literal in evidence.casefold())
        )

    @staticmethod
    def _MaterialSignature(item: Interpretation) -> tuple[Any, ...]:
        normalize = AmbiguityGate._Normalize
        return (
            tuple(sorted(normalize(value) for value in item.entities)),
            normalize(item.metric or ""),
            tuple(sorted(normalize(value) for value in item.filters)),
            normalize(item.aggregation or ""),
            tuple(sorted(normalize(value) for value in item.grouping)),
            normalize(item.time_range or ""),
            normalize(item.ranking or ""),
        )

    @classmethod
    def _DominantFromContext(
        cls,
        items: list[Interpretation],
        session_context: Mapping[str, Any] | None,
        evidence: str,
    ) -> Interpretation | None:
        context = json.dumps(session_context or {}, default=str)
        default_text = evidence if "default" in evidence.casefold() else ""
        haystack = set(cls._Tokens(f"{context} {default_text}"))
        if not haystack:
            return None
        scored = []
        for item in items:
            differentiators = set(
                cls._Tokens(
                    " ".join(
                        [
                            item.id,
                            item.label,
                            item.metric or "",
                            *item.filters,
                            item.aggregation or "",
                            *item.grouping,
                            item.time_range or "",
                            item.ranking or "",
                        ]
                    )
                )
            )
            scored.append((len(differentiators & haystack), item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        if scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            return scored[0][1]
        return None

    @staticmethod
    def _ResolveQuestion(question: str, item: Interpretation) -> str:
        detail = AmbiguityGate._Description(item)
        return f"{question}\nResolved interpretation: {item.label}. {detail}".strip()

    @staticmethod
    def _Description(item: Interpretation) -> str:
        parts = []
        if item.metric:
            parts.append(f"metric: {item.metric}")
        if item.filters:
            parts.append(f"filters: {', '.join(item.filters)}")
        if item.aggregation:
            parts.append(f"aggregation: {item.aggregation}")
        if item.grouping:
            parts.append(f"grouping: {', '.join(item.grouping)}")
        if item.time_range:
            parts.append(f"time: {item.time_range}")
        if item.ranking:
            parts.append(f"ranking: {item.ranking}")
        return "; ".join(parts) or f"entity: {', '.join(item.entities)}"

    @classmethod
    def _MissingConcepts(
        cls, question: str, schema: Mapping[str, list[str]]
    ) -> list[str]:
        schema_tokens = cls._Tokens(
            " ".join([*schema.keys(), *(column for values in schema.values() for column in values)])
        )
        ignored = {
            "a", "all", "and", "by", "data", "for", "from", "give", "list",
            "me", "of", "show", "the", "to", "what", "which",
        }
        missing = [
            token for token in cls._Tokens(question) if token not in schema_tokens and token not in ignored
        ]
        return missing[:5] or ["requested concept"]

    @staticmethod
    def _Tokens(value: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", value.casefold())

    @staticmethod
    def _Normalize(value: str) -> str:
        return "".join(AmbiguityGate._Tokens(str(value)))
