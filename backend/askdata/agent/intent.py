"""Structured description of the result a user's question requires."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class IntentContract(BaseModel):
    """Deterministic requirements shared by SQL and result quality gates."""

    shape: Literal["scalar", "listing", "ranking", "ratio", "grouped", "comparison"]
    entities: list[str] = Field(default_factory=list)
    output_attributes: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    grouping: list[str] = Field(default_factory=list)
    order: Literal["ascending", "descending"] | None = None
    expected_max_rows: int | None = Field(default=None, gt=0)
    time_condition: str | None = None
