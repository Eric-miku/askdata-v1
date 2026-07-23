"""Database adapter interface for Text2SQL execution backends."""

from __future__ import annotations

from abc import ABC, abstractmethod

from askdata.db.validator import ValidationResult


class DatabaseAdapter(ABC):
    dialect: str

    @abstractmethod
    def Validate(self, sql: str) -> ValidationResult:
        ...

    @abstractmethod
    def Execute(self, sql: str, *, preview_limit: int = 100) -> dict:
        ...

    @abstractmethod
    def IntrospectSchema(self) -> dict:
        ...
