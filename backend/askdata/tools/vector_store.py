"""Storage-neutral contracts for attributable schema retrieval chunks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any, Protocol, Sequence


SOURCE_VERSION = "askdata-schema-v1"


@dataclass(frozen=True)
class SchemaChunk:
    id: str
    database_id: str
    source_type: str
    text: str
    table_name: str | None = None
    column_name: str | None = None
    source_version: str = SOURCE_VERSION
    join_neighbors: tuple[str, ...] = ()
    foreign_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class RankedChunk:
    chunk: SchemaChunk
    score: float = 0.0

    @property
    def id(self) -> str:
        return self.chunk.id

    @property
    def database_id(self) -> str:
        return self.chunk.database_id

    @property
    def source_type(self) -> str:
        return self.chunk.source_type

    @property
    def text(self) -> str:
        return self.chunk.text

    @property
    def table_name(self) -> str | None:
        return self.chunk.table_name

    @property
    def column_name(self) -> str | None:
        return self.chunk.column_name


class VectorStore(Protocol):
    collection_name: str

    def Search(
        self, database_id: str, vectors: list[list[float]], top_k: int
    ) -> list[RankedChunk]: ...

    def Upsert(
        self, chunks: Sequence[SchemaChunk], vectors: Sequence[Sequence[float]]
    ) -> None: ...


class DisabledVectorStore:
    collection_name = "disabled"

    def Search(self, database_id, vectors, top_k):
        return []

    def Upsert(self, chunks, vectors) -> None:
        return None


class MilvusVectorStore:
    """Thin lazy wrapper; importing AskData does not require ``pymilvus``."""

    def __init__(
        self,
        uri: str,
        collection_name: str,
        *,
        client: Any = None,
    ) -> None:
        self.uri = uri
        self.collection_name = collection_name
        self._client = client

    @property
    def client(self):
        if self._client is None:
            try:
                from pymilvus import MilvusClient
            except ImportError as exc:
                raise RuntimeError(
                    "Vector retrieval requires the optional 'vector' dependency"
                ) from exc
            self._client = MilvusClient(uri=self.uri)
        return self._client

    def Search(self, database_id, vectors, top_k):
        if not vectors:
            return []
        # json.dumps produces a quoted and escaped scalar safe for Milvus filters.
        database_literal = json.dumps(str(database_id), ensure_ascii=True)
        response = self.client.search(
            collection_name=self.collection_name,
            data=vectors,
            limit=top_k,
            filter=f"database_id == {database_literal}",
            output_fields=[
                "chunk_id", "database_id", "source_type", "text",
                "table_name", "column_name", "source_version",
                "join_neighbors", "foreign_keys",
            ],
        )
        results: list[RankedChunk] = []
        for query_hits in response or []:
            for hit in query_hits or []:
                entity = hit.get("entity", hit) if isinstance(hit, dict) else {}
                chunk_id = entity.get("chunk_id") or entity.get("id")
                if not chunk_id or entity.get("database_id") != database_id:
                    continue
                chunk = SchemaChunk(
                    id=str(chunk_id),
                    database_id=str(entity["database_id"]),
                    source_type=str(entity.get("source_type") or "schema"),
                    text=str(entity.get("text") or ""),
                    table_name=entity.get("table_name"),
                    column_name=entity.get("column_name"),
                    source_version=str(entity.get("source_version") or SOURCE_VERSION),
                    join_neighbors=tuple(entity.get("join_neighbors") or ()),
                    foreign_keys=tuple(entity.get("foreign_keys") or ()),
                )
                results.append(RankedChunk(chunk, float(hit.get("distance", 0.0))))
        return results

    def Upsert(self, chunks, vectors) -> None:
        chunks = list(chunks)
        vectors = [list(vector) for vector in vectors]
        if len(chunks) != len(vectors):
            raise ValueError("Chunk and vector counts must match")
        rows = []
        for chunk, vector in zip(chunks, vectors):
            row = asdict(chunk)
            row["chunk_id"] = row.pop("id")
            row["vector"] = vector
            rows.append(row)
        if rows:
            self.client.upsert(collection_name=self.collection_name, data=rows)
