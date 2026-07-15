from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.agent.intent import IntentContract
from askdata.agent.pipeline import StagedSqlPipeline
from askdata.agent.react_sql_agent import SqlCandidateDraft


def retrieval(intent=None):
    return {
        "database_id": "demo",
        "database_path": "/tmp/demo.sqlite",
        "schema_prompt": "Database: demo\nTable items(id integer, name text)",
        "schema": {"items": ["id", "name"]},
        "intent": intent or IntentContract(shape="scalar", metrics=["count"], expected_max_rows=1),
    }


class FakeReact:
    def __init__(self, batches):
        self.batches = list(batches)
        self.contexts = []

    def GenerateCandidates(self, question, schema_prompt, session_context=None):
        self.contexts.append(session_context or {})
        return self.batches.pop(0) if self.batches else []


class RecordingAnalyzer:
    def __init__(self):
        self.sql_seen = None
        self.rows_seen = None

    def Analyze(self, question, sql, columns, rows):
        self.sql_seen = sql
        self.rows_seen = rows
        return f"answer from {sql}"


class MappingRunner:
    def __init__(self, results):
        self.results = results
        self.sql_seen = []

    def __call__(self, sql, database_path):
        self.sql_seen.append(sql)
        return self.results[sql]


class AlwaysFailingRunner:
    def __init__(self, error="syntax error"):
        self.call_count = 0

    def __call__(self, sql, database_path):
        self.call_count += 1
        return {"success": False, "sql": sql, "error": "syntax error"}


class InfiniteReact:
    def __init__(self):
        self.index = 0

    def GenerateCandidates(self, question, schema_prompt, session_context=None):
        self.index += 1
        return [SqlCandidateDraft(sql=f"SELECT id FROM items WHERE id = {self.index}")]


def test_pipeline_synthesizes_answer_after_selecting_final_candidate():
    count_sql = "SELECT COUNT(*) AS count FROM items"
    inspect_sql = "SELECT id, name FROM items"
    react = FakeReact([[
        SqlCandidateDraft(sql=count_sql),
        SqlCandidateDraft(sql=inspect_sql),
    ]])
    analyzer = RecordingAnalyzer()
    runner = MappingRunner({
        count_sql: {"success": True, "columns": ["count"], "rows": [{"count": 3}]},
        inspect_sql: {"success": True, "columns": ["id", "name"], "rows": [{"id": 1, "name": "a"}]},
    })

    result = StagedSqlPipeline(react=react, analyzer=analyzer, runner=runner).Run(
        question="How many items?", retrieval=retrieval()
    )

    assert result["sql"] == count_sql
    assert analyzer.sql_seen == result["sql"]
    assert analyzer.rows_seen == result["rows"]
    assert result["answer"] == f"answer from {count_sql}"


def test_pipeline_never_executes_more_than_six_candidates():
    runner = AlwaysFailingRunner()

    result = StagedSqlPipeline(react=InfiniteReact(), runner=runner).Run(
        question="How many items?", retrieval=retrieval()
    )

    assert runner.call_count == 6
    assert result["kind"] == "error"


def test_pipeline_stops_repeated_identical_sql_early():
    sql = "SELECT missing FROM items"
    runner = AlwaysFailingRunner()
    react = FakeReact([[SqlCandidateDraft(sql=sql)]] * 6)

    result = StagedSqlPipeline(react=react, runner=runner).Run(
        question="List items", retrieval=retrieval(IntentContract(shape="listing"))
    )

    assert runner.call_count == 1
    assert result["failure_class"] == "repeated_no_progress"


def test_pipeline_expands_retrieval_only_after_schema_grounding_failure():
    generated = [f"SELECT missing_{index} FROM items" for index in range(1, 7)]
    react = FakeReact([[SqlCandidateDraft(sql=sql)] for sql in generated])
    expanded = []

    result = StagedSqlPipeline(
        react=react,
        runner=AlwaysFailingRunner(),
        retrieval_expander=lambda question, current: expanded.append(question) or current,
    ).Run(question="List items", retrieval=retrieval(IntentContract(shape="listing")))

    assert result["kind"] == "error"
    assert expanded == ["List items"]


def test_pipeline_does_not_expand_retrieval_for_syntax_failure():
    expanded = []
    react = FakeReact([
        [SqlCandidateDraft(sql=f"SELECT id FROM items WHERE id = {index}")]
        for index in range(1, 7)
    ])

    StagedSqlPipeline(
        react=react,
        runner=AlwaysFailingRunner(),
        retrieval_expander=lambda question, current: expanded.append(question) or current,
    ).Run(question="List items", retrieval=retrieval(IntentContract(shape="listing")))

    assert expanded == []


def test_pipeline_emits_operational_events_without_sql_or_database_errors():
    secret = "secret-column-name"
    events = []
    react = FakeReact([[SqlCandidateDraft(sql=f"SELECT {secret} FROM items")]] * 2)

    result = StagedSqlPipeline(react=react, runner=AlwaysFailingRunner()).Run(
        question="List items",
        retrieval=retrieval(IntentContract(shape="listing")),
        emit=events.append,
    )

    serialized = str(events) + str(result["trace"])
    assert secret not in serialized
    assert {event["step"] for event in events} <= {
        "GenerateSql", "ValidateSql", "ExecuteSql", "RepairSql", "RetrieveSchema"
    }


def test_pipeline_classifies_answer_shape_and_empty_result_failures():
    wrong_shape = "SELECT id FROM items"
    empty = "SELECT COUNT(*) AS count FROM items WHERE id < 0"
    react = FakeReact([
        [SqlCandidateDraft(sql=wrong_shape)],
        [SqlCandidateDraft(sql=empty)],
    ] + [[]] * 4)
    runner = MappingRunner({
        wrong_shape: {"success": True, "columns": ["id"], "rows": [{"id": 1}]},
        empty: {"success": True, "columns": ["count"], "rows": []},
    })

    result = StagedSqlPipeline(react=react, runner=runner).Run(
        question="How many items?", retrieval=retrieval()
    )

    assert result["kind"] == "answer"
    assert {candidate["failure_class"] for candidate in result["ledger"]} == {
        "answer_shape", "empty_or_suspicious"
    }


def test_pipeline_gives_the_next_generation_targeted_previous_candidate_feedback():
    first = "SELECT name FROM items WHERE id = 1"
    repaired = "SELECT name FROM items"
    react = FakeReact([
        [SqlCandidateDraft(sql=first)],
        [SqlCandidateDraft(sql=repaired)],
    ])
    runner = MappingRunner({
        first: {"success": False, "error": "syntax error"},
        repaired: {"success": True, "columns": ["name"], "rows": [{"name": "a"}]},
    })

    result = StagedSqlPipeline(
        react=react,
        analyzer=RecordingAnalyzer(),
        runner=runner,
    ).Run(
        question="List name",
        retrieval=retrieval(IntentContract(shape="listing", output_attributes=["name"])),
    )

    assert result["sql"] == repaired
    assert react.contexts[1]["pipeline_previous_sql"] == first
    assert react.contexts[1]["pipeline_feedback"] == "Correct SQL syntax or safety validation."
