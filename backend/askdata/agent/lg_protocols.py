from typing import Dict, Any, Optional, Protocol


class ModelClient(Protocol):
    def generate(self, prompt: str) -> str:
        ...


class Retriever(Protocol):
    def retrieve(self, query: str, database_id: Optional[str] = None) -> str:
        ...


class SqlExecutor(Protocol):
    def execute(self, sql: str) -> Dict[str, Any]:
        ...


class ResultAnalyzer(Protocol):
    def analyze(self, question: str, sql: str, result: Dict[str, Any]) -> str:
        ...