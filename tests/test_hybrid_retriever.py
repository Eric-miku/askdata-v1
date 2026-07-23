from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.retrieval.hybrid_retriever import HybridRetriever, ReciprocalRankFusion
from askdata.retrieval.retriever import BirdSchemaIndex
from askdata.retrieval.vector_store import (
    DisabledVectorStore,
    MilvusVectorStore,
    RankedChunk,
    SchemaChunk,
)


def sample_database():
    return {
        "databaseId": "demo",
        "databasePath": "/tmp/demo.sqlite",
        "tables": [
            {
                "tableName": "schools",
                "columns": [
                    {"columnName": "id", "columnType": "integer", "isPrimary": True},
                    {"columnName": "EdOpsCode", "columnType": "text", "description": "school type code"},
                ],
            },
            {
                "tableName": "students",
                "columns": [
                    {"columnName": "school_id", "columnType": "integer"},
                    {"columnName": "score", "columnType": "real"},
                ],
            },
        ],
        "foreignKeys": [{
            "leftTable": "students", "leftColumn": "school_id",
            "rightTable": "schools", "rightColumn": "id",
        }],
    }


class FakeEmbedding:
    def __init__(self):
        self.calls = []

    def Embed(self, texts):
        self.calls.append(list(texts))
        return [[float(len(text))] for text in texts]


class FakeVectorStore:
    def __init__(self, ranked=None, error=None):
        self.ranked = ranked or []
        self.error = error
        self.calls = []

    def Search(self, database_id, vectors, top_k):
        self.calls.append((database_id, vectors, top_k))
        if self.error:
            raise self.error
        return list(self.ranked)


def ranked(chunk, score=1.0):
    return RankedChunk(chunk=chunk, score=score)


def test_rrf_fuses_rankings_without_comparing_raw_scores():
    first = SchemaChunk("a", "demo", "schema", "A", table_name="a")
    second = SchemaChunk("b", "demo", "schema", "B", table_name="b")

    fused = ReciprocalRankFusion([
        [ranked(first, 0.01), ranked(second, 0.001)],
        [ranked(second, 999.0)],
    ])

    assert [item.id for item in fused] == ["b", "a"]


def test_hybrid_retriever_falls_back_to_lexical_with_safe_warning():
    lexical = BirdSchemaIndex().Build([sample_database()])
    vector = FakeVectorStore(error=RuntimeError("secret-token remote outage"))
    result = HybridRetriever(lexical, vector, FakeEmbedding()).Retrieve("demo", "list schools")

    assert result.chunks[0].table_name == "schools"
    assert any(event.status == "warning" for event in result.trace)
    assert "secret-token" not in result.prompt
    assert all("secret-token" not in event.message for event in result.trace)


def test_value_chunk_bridges_business_term_to_code_and_keeps_schema_backbone():
    value = SchemaChunk(
        "demo:value:schools:EdOpsCode:sss", "demo", "value",
        "SSS = State Special School", table_name="schools", column_name="EdOpsCode",
    )
    lexical = BirdSchemaIndex().Build([sample_database()])
    result = HybridRetriever(lexical, FakeVectorStore([ranked(value)]), FakeEmbedding()).Retrieve(
        "demo", "State Special Schools"
    )

    assert "SSS = State Special School" in result.prompt
    assert "Schema backbone:" in result.prompt
    assert "schools.EdOpsCode" in result.prompt
    assert "Join students.school_id = schools.id" in result.prompt
    assert {chunk.table_name for chunk in result.chunks} >= {"schools", "students"}


def test_searches_original_and_context_resolved_question_then_fuses_sources():
    schema = SchemaChunk("demo:schema:schools", "demo", "schema", "Table schools", table_name="schools")
    evidence = SchemaChunk("demo:evidence:q1", "demo", "evidence", "Enrollment means student count")
    vector = FakeVectorStore([ranked(schema), ranked(evidence)])
    embedding = FakeEmbedding()
    lexical = BirdSchemaIndex().Build([sample_database()])

    result = HybridRetriever(
        lexical, vector, embedding,
        context_resolver=lambda question: f"{question} in the schools database",
    ).Retrieve("demo", "show enrollment")

    assert embedding.calls == [["show enrollment", "show enrollment in the schools database"]]
    assert len(vector.calls) == 2
    assert {chunk.source_type for chunk in result.chunks} >= {"schema", "evidence"}


def test_one_terminology_expansion_pass_recovers_missing_filter_term():
    value = SchemaChunk(
        "demo:value:schools:EdOpsCode:sss", "demo", "value",
        "SSS = State Special School", table_name="schools", column_name="EdOpsCode",
    )
    vector = FakeVectorStore([], error=None)
    calls = []

    def expand(question, chunks):
        calls.append((question, chunks))
        vector.ranked = [ranked(value)]
        return "schools where EdOpsCode is SSS"

    result = HybridRetriever(
        BirdSchemaIndex().Build([sample_database()]), vector, FakeEmbedding(),
        terminology_expander=expand,
    ).Retrieve("demo", "schools with the special category")

    assert len(calls) == 1
    assert "SSS = State Special School" in result.prompt
    assert result.coverage["filter"] is True
    assert set(result.coverage) == {
        "entity", "metric", "filter", "group", "time", "comparison", "ranking"
    }


def test_disabled_store_is_an_explicit_noop():
    assert DisabledVectorStore().Search("demo", [[1.0]], 5) == []


def test_test_double_vector_store_can_run_without_an_embedding_client():
    value = SchemaChunk(
        "demo:value:schools:type", "demo", "value", "SSS = State Special School",
        table_name="schools", column_name="EdOpsCode",
    )

    result = HybridRetriever(
        BirdSchemaIndex().Build([sample_database()]), FakeVectorStore([ranked(value)])
    ).Retrieve("demo", "State Special School")

    assert value in result.chunks


def test_join_neighbors_are_not_pruned_by_dense_top_k():
    school = SchemaChunk(
        "demo:schema:schools", "demo", "schema", "Table schools", table_name="schools"
    )
    result = HybridRetriever(
        BirdSchemaIndex().Build([sample_database()]),
        FakeVectorStore([ranked(school)]),
        FakeEmbedding(),
        top_k=1,
    ).Retrieve("demo", "schools")

    assert {chunk.table_name for chunk in result.chunks} >= {"schools", "students"}


def test_milvus_search_always_filters_by_database_id():
    class FakeClient:
        def __init__(self):
            self.kwargs = None

        def search(self, **kwargs):
            self.kwargs = kwargs
            return [[]]

    client = FakeClient()
    store = MilvusVectorStore(uri="unused", collection_name="chunks", client=client)

    assert store.Search("school's-demo", [[0.1]], 4) == []
    assert client.kwargs["filter"] == 'database_id == "school\'s-demo"'
