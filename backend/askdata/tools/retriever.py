"""Schema vector retriever for BIRD processed databases."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Protocol
import urllib.error
import urllib.request

from askdata.core.config import settings
from askdata.core.paths import project_path
from askdata.data.bird_io import LoadProcessedDatabases, LoadProcessedQuestions, ResolveProcessedDir


def GetValue(item: Any, *names: str, default=None):
    for name in names:
        if isinstance(item, dict) and name in item:
            return item[name]
        if hasattr(item, name):
            return getattr(item, name)
    return default


def _Tokens(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z0-9]+", (text or "").lower()))


class EmbeddingClient(Protocol):
    def EmbedTexts(self, texts: list[str]) -> list[list[float]]:
        ...


class OpenAICompatibleEmbeddingClient:
    """Small stdlib client for OpenAI-compatible /embeddings endpoints."""

    def __init__(self, api_base: str | None, api_key: str | None, model: str, timeout_seconds: int = 60):
        if not api_base:
            raise ValueError("Embedding API base is required for openai-compatible embeddings.")
        cleaned = api_base.rstrip("/")
        self.endpoint = cleaned if cleaned.endswith("/embeddings") else f"{cleaned}/embeddings"
        self.api_key = api_key or ""
        self.model = model
        self.timeout_seconds = timeout_seconds

    def EmbedTexts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(self.endpoint, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Embedding request failed: HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Embedding request failed: {exc.reason}") from exc

        rows = data.get("data")
        if not isinstance(rows, list):
            raise RuntimeError("Embedding response is missing a data array.")
        rows = sorted(rows, key=lambda item: item.get("index", 0))
        vectors = [item.get("embedding") for item in rows]
        if len(vectors) != len(texts) or not all(isinstance(vector, list) for vector in vectors):
            raise RuntimeError("Embedding response count does not match input count.")
        return [[float(value) for value in vector] for vector in vectors]


class HashEmbeddingClient:
    """Deterministic local embeddings matching data-processing contract tests."""

    def __init__(self, dimension: int):
        if dimension <= 0:
            raise ValueError("Hash embedding dimension must be positive.")
        self.dimension = dimension

    def EmbedTexts(self, texts: list[str]) -> list[list[float]]:
        return [self._Embed(text) for text in texts]

    def _Embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for index, token in enumerate(text.lower().split()):
            digest = hashlib.sha256(f"{index}:{token}".encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimension
            sign = -1.0 if digest[4] % 2 else 1.0
            vector[bucket] += sign
        return _NormalizeVector(vector)


@dataclass
class VectorSearchResult:
    metadata: dict[str, Any]
    score: float


class SchemaVectorIndex:
    """Loads schema_metadata plus FAISS/JSONL vectors and performs Top-K search."""

    def __init__(
        self,
        index_dir: Path,
        manifest: dict[str, Any],
        metadata: list[dict[str, Any]],
        embedding_client: EmbeddingClient,
        vectors: list[list[float]] | None = None,
    ):
        self.index_dir = index_dir
        self.manifest = manifest
        self.metadata = metadata
        self.embedding_client = embedding_client
        self.vectors = vectors

    @classmethod
    def Load(cls, index_dir: str | Path | None) -> "SchemaVectorIndex | None":
        if not index_dir:
            return None
        index_path = Path(index_dir)
        manifest_path = index_path / "manifest.json"
        metadata_path = index_path / "schema_metadata.jsonl"
        if not manifest_path.is_file() or not metadata_path.is_file():
            return None

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        metadata = [json.loads(line) for line in metadata_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        client = _BuildEmbeddingClient(manifest)
        vectors = None
        if manifest.get("index_type") == "jsonl":
            vectors_file = manifest.get("vectors_file") or "schema_vectors.jsonl"
            vectors_path = index_path / vectors_file
            if not vectors_path.is_file():
                return None
            vector_rows = [json.loads(line) for line in vectors_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            vector_by_id = {row["id"]: row["vector"] for row in vector_rows}
            vectors = [_NormalizeVector([float(value) for value in vector_by_id[item["id"]]]) for item in metadata]
        return cls(index_path, manifest, metadata, client, vectors=vectors)

    def Search(self, question: str, database_id: str, top_k: int = 12) -> list[VectorSearchResult]:
        query_vector = self.embedding_client.EmbedTexts([question])[0]
        query_vector = _NormalizeVector(query_vector)
        if self.manifest.get("index_type") == "faiss":
            return self._SearchFaiss(query_vector, database_id, top_k)
        return self._SearchJsonl(query_vector, database_id, top_k)

    def _SearchJsonl(self, query_vector: list[float], database_id: str, top_k: int) -> list[VectorSearchResult]:
        if self.vectors is None:
            return []
        scored = []
        for metadata, vector in zip(self.metadata, self.vectors):
            if metadata.get("database_id") != database_id:
                continue
            scored.append(VectorSearchResult(metadata=metadata, score=_Dot(query_vector, vector)))
        return sorted(scored, key=lambda item: item.score, reverse=True)[:top_k]

    def _SearchFaiss(self, query_vector: list[float], database_id: str, top_k: int) -> list[VectorSearchResult]:
        try:
            import faiss  # type: ignore[import-not-found]
            import numpy as np  # type: ignore[import-not-found]
        except ModuleNotFoundError:
            return []
        index_file = self.manifest.get("index_file") or "schema.faiss"
        index_path = self.index_dir / index_file
        if not index_path.is_file():
            return []
        index = faiss.read_index(str(index_path))
        query = np.asarray([query_vector], dtype="float32")
        distances, indices = index.search(query, min(len(self.metadata), max(top_k * 8, top_k)))
        results = []
        for distance, index_id in zip(distances[0], indices[0]):
            if index_id < 0 or index_id >= len(self.metadata):
                continue
            metadata = self.metadata[int(index_id)]
            if metadata.get("database_id") != database_id:
                continue
            results.append(VectorSearchResult(metadata=metadata, score=-float(distance)))
            if len(results) >= top_k:
                break
        return results


class BirdSchemaIndex:
    """BIRD-first schema index backed by optional schema vector search."""

    def __init__(self, instructions_dir=None, vector_index: SchemaVectorIndex | None = None, top_k: int = 12):
        self.databases: dict[str, Any] = {}
        self.questions: list[dict[str, Any]] = []
        self.instructions_dir = project_path(instructions_dir or settings.BIRD_INSTRUCTIONS_DIR)
        self.vector_index = vector_index
        self.top_k = top_k

    def Build(
        self,
        databases: list[Any],
        instructions_dir=None,
        questions: list[dict] | None = None,
        vector_index: SchemaVectorIndex | None = None,
    ):
        self.databases = {GetValue(database, "databaseId", "database_id"): database for database in databases}
        self.questions = questions or []
        if instructions_dir:
            self.instructions_dir = project_path(instructions_dir)
        if vector_index is not None:
            self.vector_index = vector_index
        return self

    def Retrieve(self, database_id: str, question: str) -> dict[str, Any]:
        database = self.databases.get(database_id)
        if not database:
            raise ValueError(f"Unknown BIRD database_id: {database_id}")

        evidence = self._FindEvidence(database_id, question)
        tables = list(GetValue(database, "tables", default=[]))
        matched_tables: list[dict[str, str]] = []
        matched_columns: list[dict[str, str]] = []
        if self.vector_index:
            try:
                matched_tables, matched_columns = self._RetrieveWithVectors(database_id, question, evidence)
            except Exception:
                matched_tables, matched_columns = [], []

        if not matched_tables:
            matched_tables, matched_columns = self._RetrieveWithLexicalFallback(tables, question, evidence)

        selected_names = {table["table_name"] for table in matched_tables}
        self._ExpandForeignKeyNeighbors(database, matched_tables, selected_names)
        self._AddPrimaryKeys(tables, matched_columns, selected_names)
        matched_joins = self._MatchedJoins(database, selected_names)

        schema_prompt = self.BuildSchemaPrompt(database, selected_names, matched_joins, evidence)
        return {
            "database_id": database_id,
            "database_path": GetValue(database, "databasePath", "database_path", default=""),
            "matched_tables": matched_tables,
            "matched_columns": matched_columns,
            "matched_joins": matched_joins,
            "schema_prompt": schema_prompt,
        }

    def _RetrieveWithVectors(
        self,
        database_id: str,
        question: str,
        evidence: str,
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        assert self.vector_index is not None
        query_text = f"{question}\n{evidence}".strip()
        results = self.vector_index.Search(query_text, database_id, top_k=self.top_k)
        matched_tables: list[dict[str, str]] = []
        matched_columns: list[dict[str, str]] = []
        seen_tables: set[str] = set()
        seen_columns: set[tuple[str, str]] = set()
        for result in results:
            item = result.metadata
            table_name = item.get("table_name")
            if not table_name:
                continue
            reason = f"Vector similarity {result.score:.3f}."
            if table_name not in seen_tables:
                matched_tables.append({"table_name": table_name, "reason": reason})
                seen_tables.add(table_name)
            if item.get("doc_type") == "column" and item.get("column_name"):
                key = (table_name, item["column_name"])
                if key not in seen_columns:
                    matched_columns.append({
                        "table_name": table_name,
                        "column_name": item["column_name"],
                        "column_type": item.get("data_type") or "text",
                        "reason": reason,
                    })
                    seen_columns.add(key)
        return matched_tables, matched_columns

    def _RetrieveWithLexicalFallback(
        self,
        tables: list[Any],
        question: str,
        evidence: str,
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        question_tokens = _Tokens(f"{question} {evidence}")
        matched_tables = []
        matched_columns = []
        for table in tables:
            table_name = GetValue(table, "tableName", "table_name")
            table_text = " ".join([
                table_name or "",
                GetValue(table, "display_name", "displayName", default="") or "",
            ])
            table_matched = bool(question_tokens & _Tokens(table_text))
            column_matches = []
            for column in GetValue(table, "columns", default=[]):
                column_name = GetValue(column, "columnName", "column_name")
                column_text = " ".join([
                    column_name or "",
                    GetValue(column, "display_name", "displayName", default="") or "",
                    GetValue(column, "description", default="") or "",
                ])
                if question_tokens & _Tokens(column_text):
                    column_matches.append(column)
                    matched_columns.append(self._ColumnDict(table_name, column, "Token match."))
            if table_matched or column_matches:
                matched_tables.append({"table_name": table_name, "reason": "Token match."})

        if not matched_tables:
            matched_tables = [
                {"table_name": GetValue(table, "tableName", "table_name"), "reason": "Included for compact database context."}
                for table in tables[:8]
            ]
        return matched_tables, matched_columns

    def _ExpandForeignKeyNeighbors(self, database: Any, matched_tables: list[dict[str, str]], selected_names: set[str]) -> None:
        for key in GetValue(database, "foreignKeys", "foreign_keys", default=[]):
            left_table = GetValue(key, "leftTable", "left_table", "source_table")
            right_table = GetValue(key, "rightTable", "right_table", "target_table")
            if left_table in selected_names or right_table in selected_names:
                for neighbor in (left_table, right_table):
                    if neighbor and neighbor not in selected_names:
                        matched_tables.append({"table_name": neighbor, "reason": "Foreign-key neighbor."})
                        selected_names.add(neighbor)

    def _AddPrimaryKeys(self, tables: list[Any], matched_columns: list[dict[str, str]], selected_names: set[str]) -> None:
        for table in tables:
            table_name = GetValue(table, "tableName", "table_name")
            if table_name not in selected_names:
                continue
            for column in GetValue(table, "columns", default=[]):
                if GetValue(column, "isPrimary", "is_primary", "is_primary_key", default=False):
                    column_name = GetValue(column, "columnName", "column_name")
                    exists = any(
                        item["table_name"] == table_name and item["column_name"] == column_name
                        for item in matched_columns
                    )
                    if not exists:
                        matched_columns.append(self._ColumnDict(table_name, column, "Primary key."))

    def _MatchedJoins(self, database: Any, selected_names: set[str]) -> list[dict[str, str]]:
        matched_joins = []
        for key in GetValue(database, "foreignKeys", "foreign_keys", default=[]):
            left_table = GetValue(key, "leftTable", "left_table", "source_table")
            right_table = GetValue(key, "rightTable", "right_table", "target_table")
            if left_table in selected_names or right_table in selected_names:
                matched_joins.append({
                    "left_table": left_table,
                    "left_column": GetValue(key, "leftColumn", "left_column", "source_column"),
                    "right_table": right_table,
                    "right_column": GetValue(key, "rightColumn", "right_column", "target_column"),
                })
        return matched_joins

    def _FindEvidence(self, database_id: str, question: str) -> str:
        normalized = question.strip().lower()
        for item in self.questions:
            q_db = GetValue(item, "databaseId", "database_id", default="")
            q_text = (GetValue(item, "question", default="") or "").strip().lower()
            if q_db == database_id and q_text == normalized:
                return GetValue(item, "evidence", default="") or ""
        return ""

    def _ColumnDict(self, table_name: str, column: Any, reason: str) -> dict[str, str]:
        return {
            "table_name": table_name,
            "column_name": GetValue(column, "columnName", "column_name"),
            "column_type": GetValue(column, "columnType", "column_type", "data_type", default="text"),
            "reason": reason,
        }

    def BuildSchemaPrompt(self, database: Any, selected_names: set[str], joins: list[dict[str, str]], evidence: str = "") -> str:
        database_id = GetValue(database, "databaseId", "database_id")
        database_path = GetValue(database, "databasePath", "database_path", default="")
        lines = [f"Database: {database_id}", "Dialect: SQLite"]
        if database_path:
            lines.append(f"SQLite path: {database_path}")
        if evidence:
            lines.append(f"Evidence: {evidence}")
        instructions = self._LoadInstructions(database_id)
        if instructions:
            lines.append(f"\n--- Business Context ---\n{instructions}\n---")
        tables = GetValue(database, "tables", default=[])
        for table in tables:
            table_name = GetValue(table, "tableName", "table_name")
            if selected_names and table_name not in selected_names and len(tables) > 8:
                continue
            columns = ", ".join(
                f"{GetValue(column, 'columnName', 'column_name')} {GetValue(column, 'columnType', 'column_type', 'data_type', default='text')}".strip()
                for column in GetValue(table, "columns", default=[])
            )
            lines.append(f"Table {table_name}({columns})")
        for join in joins:
            lines.append(f"Join {join['left_table']}.{join['left_column']} = {join['right_table']}.{join['right_column']}")
        return "\n".join(lines)

    def _LoadInstructions(self, database_id: str) -> str:
        path = self.instructions_dir / f"{database_id}.md"
        if not path.exists():
            return ""
        content = path.read_text(encoding="utf-8")
        parts = []
        business = self._ExtractSection(content, "Business Term Mappings")
        joins = self._ExtractSection(content, "JOIN Patterns")
        if business:
            parts.append(f"Term mappings:\n{business}")
        if joins:
            parts.append(f"JOIN patterns:\n{joins}")
        return "\n\n".join(parts)

    def _ExtractSection(self, content: str, heading: str) -> str:
        collecting = False
        result = []
        for line in content.splitlines():
            stripped = line.strip()
            if heading in stripped:
                collecting = True
                continue
            if collecting and stripped.startswith("##"):
                break
            if collecting and stripped and not stripped.startswith("#") and not stripped.startswith("```"):
                result.append(stripped)
        return "\n".join(result)


class SchemaIndex(BirdSchemaIndex):
    """Neutral alias for the BIRD-first schema index."""


class SemanticRetriever:
    """Loads BIRD processed schemas and returns prompt text for AgentState.schema_context."""

    def __init__(self, processed_dir=None, index: BirdSchemaIndex | None = None, top_k: int = 12):
        self.processed_dir = ResolveProcessedDir(processed_dir or settings.BIRD_DATA_DIR) if not index else None
        self.index = index
        self.top_k = top_k

    def Build(self):
        if self.index:
            return self
        databases = LoadProcessedDatabases(self.processed_dir)
        try:
            questions = LoadProcessedQuestions(
                self.processed_dir,
                database_ids={database["database_id"] for database in databases},
            )
        except FileNotFoundError:
            questions = []
        vector_index = SchemaVectorIndex.Load(self.processed_dir / "vector_index")
        self.index = BirdSchemaIndex(vector_index=vector_index, top_k=self.top_k).Build(databases, questions=questions)
        return self

    def Retrieve(self, database_id: str, question: str) -> str:
        if not self.index:
            self.Build()
        return self.index.Retrieve(database_id, question)["schema_prompt"]


def _BuildEmbeddingClient(manifest: dict[str, Any]) -> EmbeddingClient:
    provider = manifest.get("embedding_provider")
    model = manifest.get("embedding_model") or settings.LLM_MODEL_NAME
    dimension = int(manifest.get("dimension") or 64)
    if provider == "hash":
        return HashEmbeddingClient(dimension=dimension)
    if provider == "openai-compatible":
        return OpenAICompatibleEmbeddingClient(
            api_base=os.getenv("EMBEDDING_API_URL") or os.getenv("EMBEDDING_API_BASE") or settings.LLM_API_BASE,
            api_key=os.getenv("EMBEDDING_API_KEY") or settings.LLM_API_KEY,
            model=model,
        )
    raise ValueError(f"Unsupported embedding provider: {provider}")


def _NormalizeVector(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [float(value) / norm for value in vector]


def _Dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))
