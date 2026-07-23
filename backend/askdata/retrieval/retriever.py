"""Schema index and semantic retriever — loads BIRD database schema, token-matches question keywords to tables/columns, builds structured prompt context with foreign key JOIN hints and per-DB business instructions."""

import re
import hashlib
from pathlib import Path
from typing import Any

from askdata.core.config import settings
from askdata.data.bird_io import LoadProcessedDatabases, LoadProcessedQuestions, ResolveProcessedDir
from askdata.core.paths import project_path
from askdata.retrieval.vector_store import RankedChunk, SchemaChunk


_VECTOR_VALIDATION_FAILURES: set[tuple[str, str, int, str, str]] = set()
_SAFE_VECTOR_WARNING = {
    "status": "warning",
    "message": "Semantic retrieval is unavailable; lexical schema retrieval was used.",
}


def _ResetVectorValidationFailuresForTests() -> None:
    _VECTOR_VALIDATION_FAILURES.clear()


def GetValue(item: Any, *names: str, default=None):
    for name in names:
        if isinstance(item, dict) and name in item:
            return item[name]
        if hasattr(item, name):
            return getattr(item, name)
    return default


def _Tokens(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z0-9]+", (text or "").lower()))


class BirdSchemaIndex:
    """BIRD-first schema index. TODO: add a Spider adapter when Spider data is finalized."""

    def __init__(self, instructions_dir=None):
        self.databases: dict[str, Any] = {}
        self.instructions_dir = project_path(instructions_dir or settings.BIRD_INSTRUCTIONS_DIR)

    def Build(self, databases: list[Any], instructions_dir=None, questions: list[dict] | None = None):
        self.databases = {GetValue(database, "databaseId", "database_id"): database for database in databases}
        self.questions = questions or []
        if instructions_dir:
            self.instructions_dir = project_path(instructions_dir)
        return self

    def GetDatabase(self, database_id: str) -> Any:
        database = self.databases.get(database_id)
        if not database:
            raise ValueError(f"Unknown BIRD database_id: {database_id}")
        return database

    def LexicalCandidates(
        self, database_id: str, question: str, top_k: int = 20
    ) -> list[RankedChunk]:
        """Return attributable lexical candidates without changing ``Retrieve``."""
        tokens = _Tokens(question)
        ranked = []
        for chunk in self.BuildChunks(database_id):
            chunk_tokens = _Tokens(chunk.text)
            overlap = len(tokens & chunk_tokens)
            identifiers = _Tokens(f"{chunk.table_name or ''} {chunk.column_name or ''}")
            identifier_overlap = len(tokens & identifiers)
            if overlap or identifier_overlap:
                score = float(overlap + (2 * identifier_overlap))
                ranked.append(RankedChunk(chunk, score))
        ranked.sort(key=lambda item: (-item.score, item.id))
        return ranked[:top_k]

    def BuildChunks(self, database_id: str) -> list[SchemaChunk]:
        """Build canonical, stable chunks for schema and semantic sources."""
        database = self.GetDatabase(database_id)
        chunks: list[SchemaChunk] = []
        foreign_keys = GetValue(database, "foreignKeys", "foreign_keys", default=[])
        table_relationships: dict[str, list[str]] = {}
        table_neighbors: dict[str, set[str]] = {}
        column_relationships: dict[tuple[str, str], list[str]] = {}
        for key in foreign_keys:
            left_table = GetValue(key, "leftTable", "left_table", "source_table", default="")
            left_column = GetValue(key, "leftColumn", "left_column", "source_column", default="")
            right_table = GetValue(key, "rightTable", "right_table", "target_table", default="")
            right_column = GetValue(key, "rightColumn", "right_column", "target_column", default="")
            relationship = f"{left_table}.{left_column} -> {right_table}.{right_column}"
            for table_name, neighbor in ((left_table, right_table), (right_table, left_table)):
                if table_name:
                    table_relationships.setdefault(table_name, []).append(relationship)
                    if neighbor:
                        table_neighbors.setdefault(table_name, set()).add(neighbor)
            column_relationships.setdefault((left_table, left_column), []).append(relationship)
            column_relationships.setdefault((right_table, right_column), []).append(relationship)
        for table in GetValue(database, "tables", default=[]):
            table_name = GetValue(table, "tableName", "table_name", default="")
            table_description = GetValue(table, "description", "display_name", "displayName", default="") or ""
            columns = []
            for column in GetValue(table, "columns", default=[]):
                name = GetValue(column, "columnName", "column_name", default="")
                data_type = GetValue(column, "columnType", "column_type", "data_type", default="text")
                description = GetValue(column, "description", "display_name", "displayName", default="") or ""
                key = " primary key" if GetValue(
                    column, "isPrimary", "is_primary", "is_primary_key", default=False
                ) else ""
                column_text = f"{name} {data_type}{key} {description}".strip()
                columns.append(column_text)
                column_fks = tuple(column_relationships.get((table_name, name), []))
                chunks.append(SchemaChunk(
                    id=f"{database_id}:schema:{table_name}:{name}",
                    database_id=database_id,
                    source_type="schema",
                    text=(
                        f"Column {table_name}.{column_text}"
                        + (f". Foreign keys: {'; '.join(column_fks)}" if column_fks else "")
                    ),
                    table_name=table_name,
                    column_name=name,
                    join_neighbors=tuple(sorted(table_neighbors.get(table_name, set()))),
                    foreign_keys=column_fks,
                ))
                raw_values = GetValue(
                    column,
                    "sample_values",
                    "sampleValues",
                    "representative_values",
                    "representativeValues",
                    default=[],
                ) or []
                for value in list(raw_values)[:20]:
                    if not isinstance(value, (str, int, float, bool)):
                        continue
                    value_text = str(value)[:200]
                    digest = hashlib.sha256(
                        f"{table_name}:{name}:{value_text}".encode("utf-8")
                    ).hexdigest()[:16]
                    chunks.append(SchemaChunk(
                        id=f"{database_id}:value:{table_name}:{name}:{digest}",
                        database_id=database_id,
                        source_type="value",
                        text=f"{table_name}.{name} representative value: {value_text}",
                        table_name=table_name,
                        column_name=name,
                    ))
            relationships = tuple(table_relationships.get(table_name, []))
            neighbors = tuple(sorted(table_neighbors.get(table_name, set())))
            text = f"Table {table_name}. {table_description} Columns: {', '.join(columns)}".strip()
            if relationships:
                text += f". Foreign keys: {'; '.join(relationships)}"
            if neighbors:
                text += f". Join neighbors: {', '.join(neighbors)}"
            chunks.append(SchemaChunk(
                id=f"{database_id}:schema:{table_name}",
                database_id=database_id,
                source_type="schema",
                text=text,
                table_name=table_name,
                join_neighbors=neighbors,
                foreign_keys=relationships,
            ))

        sections = self._InstructionSections(database_id)
        for content in sections.get("Business Term Mappings", []):
            source_type = "value" if self._LooksLikeValueMapping(content) else "evidence"
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
            chunks.append(SchemaChunk(
                id=f"{database_id}:{source_type}:{digest}", database_id=database_id,
                source_type=source_type, text=content,
            ))
        for content in sections.get("JOIN Patterns", []):
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
            tables = tuple(dict.fromkeys(re.findall(r"([A-Za-z_][\w]*)\.", content)))
            chunks.append(SchemaChunk(
                id=f"{database_id}:evidence:join:{digest}", database_id=database_id,
                source_type="evidence", text=f"JOIN pattern: {content}",
                join_neighbors=tables,
            ))
        for heading, lines in sections.items():
            if heading in {"Business Term Mappings", "JOIN Patterns"}:
                continue
            for content in lines:
                digest = hashlib.sha256(f"{heading}:{content}".encode("utf-8")).hexdigest()[:16]
                chunks.append(SchemaChunk(
                    id=f"{database_id}:evidence:{digest}", database_id=database_id,
                    source_type="evidence", text=f"{heading}: {content}",
                ))

        for position, item in enumerate(self.questions):
            if GetValue(item, "databaseId", "database_id", default="") != database_id:
                continue
            question = GetValue(item, "question", default="") or ""
            evidence = GetValue(item, "evidence", default="") or ""
            question_id = GetValue(item, "questionId", "question_id", default="") or str(position)
            if evidence:
                chunks.append(SchemaChunk(
                    id=f"{database_id}:evidence:{question_id}",
                    database_id=database_id,
                    source_type="evidence",
                    text=f"Question: {question}\nEvidence: {evidence}",
                ))
            gold_sql = GetValue(item, "goldSql", "gold_sql", "SQL", default="") or ""
            if gold_sql:
                chunks.append(SchemaChunk(
                    id=f"{database_id}:example:{question_id}",
                    database_id=database_id,
                    source_type="example",
                    text=f"Validated example only. Question: {question}\nSQL: {gold_sql}",
                ))
        return chunks

    def SchemaBackbone(self, database_id: str) -> str:
        database = self.GetDatabase(database_id)
        lines = ["Schema backbone:"]
        for table in GetValue(database, "tables", default=[]):
            table_name = GetValue(table, "tableName", "table_name", default="")
            for column in GetValue(table, "columns", default=[]):
                column_name = GetValue(column, "columnName", "column_name", default="")
                suffix = " [primary key]" if GetValue(
                    column, "isPrimary", "is_primary", "is_primary_key", default=False
                ) else ""
                lines.append(f"- {table_name}.{column_name}{suffix}")
        for key in GetValue(database, "foreignKeys", "foreign_keys", default=[]):
            left_table = GetValue(key, "leftTable", "left_table", "source_table", default="")
            left_column = GetValue(key, "leftColumn", "left_column", "source_column", default="")
            right_table = GetValue(key, "rightTable", "right_table", "target_table", default="")
            right_column = GetValue(key, "rightColumn", "right_column", "target_column", default="")
            lines.append(f"- {left_table}.{left_column} -> {right_table}.{right_column}")
        return "\n".join(lines)

    def StructuredSchema(self, database_id: str) -> dict[str, list[str]]:
        """Return the complete authoritative table/column map for quality gates."""
        database = self.GetDatabase(database_id)
        return {
            GetValue(table, "tableName", "table_name", default=""): [
                GetValue(column, "columnName", "column_name", default="")
                for column in GetValue(table, "columns", default=[])
                if GetValue(column, "columnName", "column_name", default="")
            ]
            for table in GetValue(database, "tables", default=[])
            if GetValue(table, "tableName", "table_name", default="")
        }

    def Retrieve(self, database_id: str, question: str) -> dict[str, Any]:
        database = self.GetDatabase(database_id)

        evidence = self._FindEvidence(database_id, question)
        question_tokens = _Tokens(f"{question} {evidence}")
        matched_tables = []
        matched_columns = []
        tables = list(GetValue(database, "tables", default=[]))

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

        selected_names = {table["table_name"] for table in matched_tables}
        foreign_keys = GetValue(database, "foreignKeys", "foreign_keys", default=[])
        for key in foreign_keys:
            left_table = GetValue(key, "leftTable", "left_table", "source_table")
            right_table = GetValue(key, "rightTable", "right_table", "target_table")
            if left_table in selected_names or right_table in selected_names:
                for neighbor in (left_table, right_table):
                    if neighbor and neighbor not in selected_names:
                        matched_tables.append({"table_name": neighbor, "reason": "Foreign-key neighbor."})
                        selected_names.add(neighbor)
        for table in tables:
            table_name = GetValue(table, "tableName", "table_name")
            if table_name not in selected_names:
                continue
            for column in GetValue(table, "columns", default=[]):
                if GetValue(column, "isPrimary", "is_primary", "is_primary_key", default=False):
                    exists = any(
                        item["table_name"] == table_name and item["column_name"] == GetValue(column, "columnName", "column_name")
                        for item in matched_columns
                    )
                    if not exists:
                        matched_columns.append(self._ColumnDict(table_name, column, "Primary key."))

        matched_joins = []
        for key in foreign_keys:
            left_table = GetValue(key, "leftTable", "left_table", "source_table")
            right_table = GetValue(key, "rightTable", "right_table", "target_table")
            if left_table in selected_names or right_table in selected_names:
                matched_joins.append({
                    "left_table": left_table,
                    "left_column": GetValue(key, "leftColumn", "left_column", "source_column"),
                    "right_table": right_table,
                    "right_column": GetValue(key, "rightColumn", "right_column", "target_column"),
                })

        schema_prompt = self.BuildSchemaPrompt(database, selected_names, matched_joins, evidence)
        return {
            "database_id": database_id,
            "database_path": GetValue(database, "databasePath", "database_path", default=""),
            "evidence": evidence,
            "schema": self.StructuredSchema(database_id),
            "matched_tables": matched_tables,
            "matched_columns": matched_columns,
            "matched_joins": matched_joins,
            "schema_prompt": schema_prompt,
        }

    def _FindEvidence(self, database_id: str, question: str) -> str:
        normalized = self._NormalizeQuestion(question)
        for item in self.questions:
            q_db = GetValue(item, "databaseId", "database_id", default="")
            q_text = self._NormalizeQuestion(GetValue(item, "question", default="") or "")
            if q_db == database_id and q_text == normalized:
                return GetValue(item, "evidence", default="") or ""
        return ""

    @staticmethod
    def _NormalizeQuestion(question: str) -> str:
        tokens = re.findall(r"[A-Za-z0-9]+", question.casefold())
        return " ".join(tokens)

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
            lines.append(
                f"Join {join['left_table']}.{join['left_column']} = {join['right_table']}.{join['right_column']}"
            )
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

    def _InstructionSections(self, database_id: str) -> dict[str, list[str]]:
        path = self.instructions_dir / f"{database_id}.md"
        if not path.exists():
            return {}
        sections: dict[str, list[str]] = {}
        heading = ""
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("## "):
                heading = stripped[3:].strip()
                sections.setdefault(heading, [])
            elif heading and stripped and not stripped.startswith(("#", "```")):
                sections[heading].append(stripped.lstrip("- "))
        return sections

    def _LooksLikeValueMapping(self, content: str) -> bool:
        if re.search(r"=\s*(['\"]).+?\1", content):
            return True
        match = re.match(r"\s*([A-Z][A-Z0-9_-]{1,11})\s*=", content)
        return bool(match)

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

    def __init__(self, processed_dir=None, index: BirdSchemaIndex | None = None):
        self.processed_dir = ResolveProcessedDir(processed_dir or settings.BIRD_DATA_DIR) if not index else None
        self.index = index

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
        lexical = BirdSchemaIndex().Build(databases, questions=questions)
        self.index = lexical
        milvus_uri = settings.ResolvedMilvusUri()
        if (
            settings.VECTOR_RETRIEVAL_ENABLED
            and settings.EMBEDDING_API_URL
            and milvus_uri
        ):
            from askdata.retrieval.embedding_client import EmbeddingClient
            from askdata.retrieval.hybrid_retriever import HybridRetriever, HybridSchemaIndex
            from askdata.retrieval.vector_store import MilvusVectorStore

            key = (
                settings.EMBEDDING_API_URL, settings.EMBEDDING_MODEL,
                settings.EMBEDDING_DIMENSION, milvus_uri,
                settings.MILVUS_COLLECTION,
            )
            if key in _VECTOR_VALIDATION_FAILURES:
                self.index = HybridSchemaIndex(lexical, fallback_warning=_SAFE_VECTOR_WARNING)
            else:
                try:
                    embedding = EmbeddingClient(
                        base_url=settings.EMBEDDING_API_URL,
                        api_key=settings.EMBEDDING_API_KEY,
                        model=settings.EMBEDDING_MODEL,
                        dimension=settings.EMBEDDING_DIMENSION,
                        timeout=settings.EMBEDDING_TIMEOUT_SECONDS,
                    )
                    probe = embedding.Validate()
                    vector = MilvusVectorStore(milvus_uri, settings.MILVUS_COLLECTION)
                    vector.Search(GetValue(databases[0], "databaseId", "database_id"), [probe], 1)
                except Exception:
                    _VECTOR_VALIDATION_FAILURES.add(key)
                    self.index = HybridSchemaIndex(
                        lexical, fallback_warning=_SAFE_VECTOR_WARNING
                    )
                else:
                    self.index = HybridSchemaIndex(
                        lexical,
                        HybridRetriever(lexical, vector, embedding),
                    )
        return self

    def Retrieve(self, database_id: str, question: str) -> str:
        if not self.index:
            self.Build()
        return self.index.Retrieve(database_id, question)["schema_prompt"]
