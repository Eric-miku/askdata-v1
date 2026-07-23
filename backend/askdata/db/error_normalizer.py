"""Normalize backend-specific database errors into stable agent-facing codes."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class NormalizedDatabaseError:
    code: str
    message: str


def NormalizeDatabaseError(dialect: str, error: object) -> NormalizedDatabaseError:
    text = str(error or "")
    lowered = text.casefold()

    table = _match_first(
        text,
        [
            r"no such table:\s*([^\s,)]+)",
            r"table ['\"]?([^'\"]+)['\"]? doesn't exist",
            r"relation ['\"]?([^'\"]+)['\"]? does not exist",
        ],
    )
    if table:
        name = _clean_identifier(table, strip_qualifier=True)
        return NormalizedDatabaseError("unknown_table", f"unknown_table: {name}")

    column = _match_first(
        text,
        [
            r"no such column:\s*([^\s,)]+)",
            r"unknown column ['\"]?([^'\"]+)['\"]?",
            r"column ['\"]?([^'\"]+)['\"]? does not exist",
        ],
    )
    if column:
        name = _clean_identifier(column, strip_qualifier=False)
        return NormalizedDatabaseError("unknown_column", f"unknown_column: {name}")

    if "ambiguous column" in lowered or ("column reference" in lowered and "ambiguous" in lowered):
        return NormalizedDatabaseError("ambiguous_column", "ambiguous_column")
    if "syntax error" in lowered or "you have an error in your sql syntax" in lowered:
        return NormalizedDatabaseError("syntax_error", "syntax_error")
    if "timeout" in lowered or "timed out" in lowered or "statement timeout" in lowered:
        return NormalizedDatabaseError("timeout", "timeout")
    return NormalizedDatabaseError("database_error", f"database_error: {text}")


def _clean_identifier(value: str, *, strip_qualifier: bool) -> str:
    value = value.strip().strip("\"'`")
    if strip_qualifier and "." in value:
        value = value.rsplit(".", 1)[-1]
    return value.strip().strip("\"'`")


def _match_first(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""
