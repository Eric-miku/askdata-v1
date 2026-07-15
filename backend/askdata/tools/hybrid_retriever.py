"""Optional hybrid schema retrieval with deterministic recall safeguards."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Callable, Iterable

from askdata.tools.vector_store import RankedChunk, SchemaChunk


@dataclass(frozen=True)
class RetrievalTraceEvent:
    status: str
    message: str


@dataclass(frozen=True)
class HybridRetrievalResult:
    chunks: list[SchemaChunk]
    prompt: str
    trace: list[RetrievalTraceEvent]
    coverage: dict[str, bool]


def ReciprocalRankFusion(
    rankings: Iterable[Iterable[RankedChunk]], k: int = 60
) -> list[RankedChunk]:
    scores: dict[str, float] = {}
    chunks: dict[str, SchemaChunk] = {}
    for ranking in rankings:
        seen: set[str] = set()
        for rank, item in enumerate(ranking, start=1):
            if item.id in seen:
                continue
            seen.add(item.id)
            scores[item.id] = scores.get(item.id, 0.0) + 1.0 / (k + rank)
            chunks[item.id] = item.chunk
    ordered = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))
    return [RankedChunk(chunks[chunk_id], scores[chunk_id]) for chunk_id in ordered]


class HybridRetriever:
    def __init__(
        self,
        lexical,
        vector,
        embedding=None,
        *,
        context_resolver: Callable[[str], str] | None = None,
        terminology_expander: Callable[[str, list[SchemaChunk]], str] | None = None,
        top_k: int = 15,
    ) -> None:
        self.lexical = lexical
        self.vector = vector
        self.embedding = embedding
        self.context_resolver = context_resolver
        self.terminology_expander = terminology_expander
        self.top_k = top_k

    def Retrieve(self, database_id: str, question: str) -> HybridRetrievalResult:
        original = unicodedata.normalize("NFC", question).strip()
        resolved = self.context_resolver(original) if self.context_resolver else original
        representations = list(dict.fromkeys([original, resolved.strip()]))
        trace: list[RetrievalTraceEvent] = []

        lexical_rankings = [
            self.lexical.LexicalCandidates(database_id, text, self.top_k)
            for text in representations
        ]
        rankings: list[list[RankedChunk]] = list(lexical_rankings)
        vector_available = True
        try:
            vectors = self._Embed(representations)
            dense = [
                self.vector.Search(database_id, [vector], self.top_k)
                for vector in vectors
            ]
            rankings.extend(self._SourceRankings(dense))
        except Exception:
            vector_available = False
            trace.append(RetrievalTraceEvent(
                status="warning",
                message="Semantic retrieval is unavailable; lexical schema retrieval was used.",
            ))

        fused = ReciprocalRankFusion(rankings)
        selected = self._AddJoinNeighbors(database_id, fused[: self.top_k])
        chunks = [item.chunk for item in selected]
        coverage = self._Coverage(original, chunks)

        if self.terminology_expander and not all(coverage.values()):
            expanded = self.terminology_expander(original, chunks)
            if expanded and expanded.strip() not in representations:
                expanded = unicodedata.normalize("NFC", expanded).strip()
                expanded_rankings = [
                    self.lexical.LexicalCandidates(database_id, expanded, self.top_k)
                ]
                if vector_available:
                    try:
                        vector = self._Embed([expanded])[0]
                        expanded_rankings.extend(self._SourceRankings([
                            self.vector.Search(database_id, [vector], self.top_k)
                        ]))
                    except Exception:
                        trace.append(RetrievalTraceEvent(
                            status="warning",
                            message="Semantic retrieval is unavailable; lexical schema retrieval was used.",
                        ))
                fused = ReciprocalRankFusion([fused, *expanded_rankings])
                selected = self._AddJoinNeighbors(database_id, fused[: self.top_k])
                chunks = [item.chunk for item in selected]
                coverage = self._Coverage(f"{original} {expanded}", chunks)

        base = self.lexical.Retrieve(database_id, original)["schema_prompt"]
        prompt_parts = [base, self.lexical.SchemaBackbone(database_id)]
        if chunks:
            prompt_parts.append("Retrieved semantic context:")
            prompt_parts.extend(
                f"- [{chunk.source_type}] {chunk.text}" for chunk in chunks
            )
        return HybridRetrievalResult(
            chunks=chunks,
            prompt="\n".join(prompt_parts),
            trace=trace,
            coverage=coverage,
        )

    def _Embed(self, texts: list[str]) -> list[list[float]]:
        if self.embedding is None:
            # This permits storage test doubles that ignore vectors. Runtime
            # construction always supplies the configured EmbeddingClient.
            return [[] for _ in texts]
        return self.embedding.Embed(texts)

    def _SourceRankings(
        self, dense_rankings: list[list[RankedChunk]]
    ) -> list[list[RankedChunk]]:
        result: list[list[RankedChunk]] = []
        for ranking in dense_rankings:
            result.append([item for item in ranking if item.source_type == "schema"])
            result.append([item for item in ranking if item.source_type == "value"])
            result.append([item for item in ranking if item.source_type == "evidence"])
            result.append([item for item in ranking if item.source_type == "example"])
        return [ranking for ranking in result if ranking]

    def _AddJoinNeighbors(
        self, database_id: str, ranking: list[RankedChunk]
    ) -> list[RankedChunk]:
        selected_tables = {item.table_name for item in ranking if item.table_name}
        if not selected_tables:
            return ranking
        database = self.lexical.GetDatabase(database_id)
        neighbors = set(selected_tables)
        from askdata.tools.retriever import GetValue

        for key in GetValue(database, "foreignKeys", "foreign_keys", default=[]):
            left = GetValue(key, "leftTable", "left_table", "source_table")
            right = GetValue(key, "rightTable", "right_table", "target_table")
            if left in selected_tables or right in selected_tables:
                neighbors.update(filter(None, [left, right]))
        present = {item.id for item in ranking}
        additions = []
        by_table = {
            chunk.table_name: chunk
            for chunk in self.lexical.BuildChunks(database_id)
            if chunk.source_type == "schema" and chunk.table_name and not chunk.column_name
        }
        for table in sorted(neighbors):
            chunk = by_table.get(table)
            if chunk and chunk.id not in present:
                additions.append(RankedChunk(chunk, 0.0))
        return [*ranking, *additions]

    def _Coverage(self, question: str, chunks: list[SchemaChunk]) -> dict[str, bool]:
        question_tokens = self._Tokens(question)
        context = " ".join(chunk.text for chunk in chunks)
        context_tokens = self._Tokens(context)
        semantic = any(chunk.source_type in {"value", "evidence"} for chunk in chunks)
        asks_filter = bool(question_tokens & {
            "with", "where", "whose", "category", "special", "only", "状态", "类型",
        })
        asks_metric = bool(question_tokens & {
            "count", "many", "average", "avg", "sum", "total", "ratio", "多少", "平均", "总计",
        })
        asks_group = bool(question_tokens & {"by", "each", "per", "按", "每"})
        asks_time = bool(question_tokens & {
            "date", "year", "month", "daily", "monthly", "annual", "日期", "年", "月",
        })
        asks_comparison = bool(question_tokens & {
            "compare", "versus", "vs", "difference", "higher", "lower", "比较", "对比", "差异",
        })
        asks_ranking = bool(question_tokens & {
            "top", "bottom", "rank", "highest", "lowest", "前", "最高", "最低", "排名",
        })
        identifier_overlap = bool(question_tokens & context_tokens)
        return {
            "entity": identifier_overlap or bool(chunks),
            "metric": (not asks_metric) or identifier_overlap,
            "filter": (not asks_filter) or semantic,
            "group": (not asks_group) or identifier_overlap,
            "time": (not asks_time) or identifier_overlap,
            "comparison": (not asks_comparison) or identifier_overlap,
            "ranking": (not asks_ranking) or identifier_overlap,
        }

    @staticmethod
    def _Tokens(text: str) -> set[str]:
        return set(re.findall(r"[\w]+", text.casefold(), flags=re.UNICODE))


class HybridSchemaIndex:
    """Compatibility adapter retaining ``BirdSchemaIndex.Retrieve``'s mapping."""

    def __init__(self, lexical, hybrid: HybridRetriever) -> None:
        self.lexical = lexical
        self.hybrid = hybrid

    def Retrieve(self, database_id: str, question: str):
        context = self.lexical.Retrieve(database_id, question)
        result = self.hybrid.Retrieve(database_id, question)
        context["schema_prompt"] = result.prompt
        context["retrieval_trace"] = [
            {"status": event.status, "message": event.message} for event in result.trace
        ]
        context["retrieval_coverage"] = result.coverage
        context["retrieved_chunks"] = result.chunks
        return context

    def __getattr__(self, name):
        return getattr(self.lexical, name)
