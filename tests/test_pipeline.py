from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.agent.intent import IntentContract
from askdata.agent.pipeline import StagedSqlPipeline
from askdata.agent.react_sql_agent import SqlCandidateDraft
from askdata.tools.retriever import BirdSchemaIndex


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


class FailingAnalyzer:
    def Analyze(self, question, sql, columns, rows):
        raise AssertionError("analyzer must not run without a trustworthy candidate")


class RecordingChartBuilder:
    def __init__(self):
        self.calls = []

    def Build(self, question, intent, columns, rows):
        self.calls.append((question, intent, columns, rows))
        return {
            "type": "horizontal_bar",
            "title": "Top items",
            "category_field": "name",
            "value_fields": ["count"],
            "reason": "ranking",
        }


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


def test_pipeline_builds_chart_from_the_analyzed_selected_candidate():
    sql = "SELECT name, count FROM items ORDER BY count DESC LIMIT 5"
    rows = [{"name": "A", "count": 3}]
    intent = IntentContract(shape="ranking", expected_max_rows=5)
    analyzer = RecordingAnalyzer()
    chart_builder = RecordingChartBuilder()
    runner = MappingRunner({
        sql: {"success": True, "columns": ["name", "count"], "rows": rows},
    })
    chart_retrieval = retrieval(intent)
    chart_retrieval["schema"] = {"items": ["name", "count"]}
    chart_retrieval["schema_prompt"] = "Database: demo\nTable items(name text, count integer)"

    result = StagedSqlPipeline(
        react=FakeReact([[SqlCandidateDraft(sql=sql)]]),
        analyzer=analyzer,
        chart_builder=chart_builder,
        runner=runner,
    ).Run(question="top five items by count", retrieval=chart_retrieval)

    assert analyzer.sql_seen == sql
    assert analyzer.rows_seen == rows
    assert chart_builder.calls == [
        ("top five items by count", intent, ["name", "count"], rows)
    ]
    assert result["chart"]["type"] == "horizontal_bar"


def test_pipeline_intent_infers_singular_schema_column_from_plural_question():
    intent = StagedSqlPipeline(react=FakeReact([]))._InferIntent(
        "What are the elements and label of molecule TR060?",
        {"atom": ["element"], "molecule": ["label"]},
    )

    assert intent.output_attributes == ["element", "label"]


def test_pipeline_rejects_partial_molecule_label_answer_and_repairs_to_elements_join():
    partial_sql = "SELECT * FROM molecule WHERE molecule_id = 'TR060'"
    final_sql = (
        "SELECT DISTINCT atom.element, molecule.label "
        "FROM atom JOIN molecule ON atom.molecule_id = molecule.molecule_id "
        "WHERE molecule.molecule_id = 'TR060'"
    )
    toxicology_retrieval = {
        "database_id": "demo",
        "database_path": "/tmp/demo.sqlite",
        "schema_prompt": (
            "Database: demo\n"
            "Table atom(atom_id text, molecule_id text, element text)\n"
            "Table molecule(molecule_id text, label text)"
        ),
        "schema": {
            "atom": ["atom_id", "molecule_id", "element"],
            "molecule": ["molecule_id", "label"],
        },
    }
    runner = MappingRunner({
        partial_sql: {
            "success": True,
            "columns": ["molecule_id", "label"],
            "rows": [{"molecule_id": "TR060", "label": "-"}],
        },
        final_sql: {
            "success": True,
            "columns": ["element", "label"],
            "rows": [{"element": "c", "label": "-"}, {"element": "h", "label": "-"}],
        },
    })

    result = StagedSqlPipeline(
        react=FakeReact([
            [SqlCandidateDraft(sql=partial_sql)],
            [SqlCandidateDraft(sql=final_sql)],
        ]),
        analyzer=RecordingAnalyzer(),
        runner=runner,
    ).Run(
        question="What are the elements of the toxicology and label of molecule TR060?",
        retrieval=toxicology_retrieval,
    )

    assert result["kind"] == "answer"
    assert result["sql"] == final_sql


def test_pipeline_does_not_build_chart_for_an_error():
    chart_builder = RecordingChartBuilder()

    result = StagedSqlPipeline(
        react=FakeReact([[SqlCandidateDraft(sql="SELECT missing FROM items")]]),
        analyzer=FailingAnalyzer(),
        chart_builder=chart_builder,
        runner=AlwaysFailingRunner(),
    ).Run(
        question="top items",
        retrieval=retrieval(IntentContract(shape="ranking", expected_max_rows=5)),
    )

    assert result["kind"] == "error"
    assert result["chart"] is None
    assert chart_builder.calls == []


def test_pipeline_never_executes_more_than_six_candidates():
    runner = AlwaysFailingRunner()

    result = StagedSqlPipeline(react=InfiniteReact(), runner=runner).Run(
        question="How many items?", retrieval=retrieval()
    )

    assert runner.call_count <= 6
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


def test_pipeline_allows_answer_shape_repairs_to_progress_to_final_candidate():
    inspect_city = "SELECT DISTINCT City FROM schools WHERE City LIKE '%Monterey%'"
    inspect_exact = "SELECT DISTINCT City FROM schools WHERE City = 'Monterey'"
    final_sql = (
        "SELECT `School Name`, Street, City, State, Zip "
        "FROM schools WHERE County = 'Monterey'"
    )
    answer_intent = IntentContract(shape="listing", output_attributes=["School Name", "Street"])
    answer_retrieval = {
        "database_id": "demo",
        "database_path": "/tmp/demo.sqlite",
        "schema_prompt": (
            "Database: demo\n"
            "Table schools(`School Name` text, Street text, City text, State text, Zip text, County text)"
        ),
        "schema": {
            "schools": ["School Name", "Street", "City", "State", "Zip", "County"]
        },
        "intent": answer_intent,
    }
    runner = MappingRunner({
        inspect_city: {"success": True, "columns": ["City"], "rows": [{"City": "Monterey"}]},
        inspect_exact: {"success": True, "columns": ["City"], "rows": [{"City": "Monterey"}]},
        final_sql: {
            "success": True,
            "columns": ["School Name", "Street", "City", "State", "Zip"],
            "rows": [{"School Name": "A", "Street": "1 Main", "City": "Monterey", "State": "CA", "Zip": "93940"}],
        },
    })

    result = StagedSqlPipeline(
        react=FakeReact([
            [SqlCandidateDraft(sql=inspect_city)],
            [SqlCandidateDraft(sql=inspect_exact)],
            [SqlCandidateDraft(sql=final_sql)],
        ]),
        analyzer=RecordingAnalyzer(),
        runner=runner,
    ).Run(question="State the names and full communication address", retrieval=answer_retrieval)

    assert result["kind"] == "answer"
    assert result["sql"] == final_sql


def test_pipeline_uses_alternate_plan_for_late_ratio_formula_repair():
    inspect_tx = "SELECT * FROM transactions_1k WHERE Date = '2012-08-25' AND Price = 634.8"
    inspect_dates = "SELECT DISTINCT Date FROM yearmonth LIMIT 20"
    inspect_customer = "SELECT * FROM yearmonth WHERE CustomerID = 6718 ORDER BY Date"
    intermediate = (
        "SELECT SUM(CASE WHEN substr(Date,1,4) = '2012' THEN Consumption ELSE 0 END) AS consumption_2012, "
        "SUM(CASE WHEN substr(Date,1,4) = '2013' THEN Consumption ELSE 0 END) AS consumption_2013 "
        "FROM yearmonth WHERE CustomerID = 6718"
    )
    final_sql = (
        "SELECT (SUM(CASE WHEN substr(Date,1,4) = '2012' THEN Consumption ELSE 0 END) - "
        "SUM(CASE WHEN substr(Date,1,4) = '2013' THEN Consumption ELSE 0 END)) * 1.0 / "
        "SUM(CASE WHEN substr(Date,1,4) = '2012' THEN Consumption ELSE 0 END) AS decrease_rate "
        "FROM yearmonth WHERE CustomerID = 6718"
    )
    ratio_retrieval = {
        "database_id": "demo",
        "database_path": "/tmp/demo.sqlite",
        "schema_prompt": (
            "Database: demo\n"
            "Table transactions_1k(Date date, CustomerID integer, Price real)\n"
            "Table yearmonth(CustomerID integer, Date text, Consumption real)"
        ),
        "schema": {
            "transactions_1k": ["Date", "CustomerID", "Price"],
            "yearmonth": ["CustomerID", "Date", "Consumption"],
        },
        "intent": IntentContract(shape="ratio", metrics=["ratio"], expected_max_rows=1),
    }
    runner = MappingRunner({
        inspect_tx: {"success": True, "columns": ["Date", "CustomerID", "Price"], "rows": [{"CustomerID": 6718}]},
        inspect_dates: {"success": True, "columns": ["Date"], "rows": [{"Date": "201201"}, {"Date": "201301"}]},
        inspect_customer: {"success": True, "columns": ["CustomerID", "Date", "Consumption"], "rows": [{"CustomerID": 6718, "Date": "201201", "Consumption": 100.0}]},
        intermediate: {"success": True, "columns": ["consumption_2012", "consumption_2013"], "rows": [{"consumption_2012": 100.0, "consumption_2013": 80.0}]},
        final_sql: {"success": True, "columns": ["decrease_rate"], "rows": [{"decrease_rate": 0.2}]},
    })

    result = StagedSqlPipeline(
        react=FakeReact([
            [SqlCandidateDraft(sql=inspect_tx)],
            [SqlCandidateDraft(sql=inspect_dates)],
            [SqlCandidateDraft(sql=inspect_customer)],
            [SqlCandidateDraft(sql=intermediate)],
            [SqlCandidateDraft(sql=final_sql)],
        ]),
        analyzer=RecordingAnalyzer(),
        runner=runner,
    ).Run(
        question="what was the consumption decrease rate from Year 2012 to 2013?",
        retrieval=ratio_retrieval,
    )

    assert result["kind"] == "answer"
    assert result["sql"] == final_sql


def test_pipeline_expands_retrieval_only_after_schema_grounding_failure():
    generated = [
        "SELECT missing_1 FROM items",
        "SELECT id FROM items WHERE id = 2",
        "SELECT missing_3 FROM items",
        "SELECT missing_4 FROM items",
        "SELECT missing_5 FROM items",
    ]
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

    result = StagedSqlPipeline(react=react, analyzer=FailingAnalyzer(), runner=runner).Run(
        question="How many items?", retrieval=retrieval()
    )

    assert result["kind"] == "error"
    assert result["error"] == "no_trustworthy_candidate"
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


def test_pipeline_executes_at_most_one_candidate_per_recovery_stage():
    first = "SELECT id FROM items"
    ignored = "SELECT name FROM items"
    runner = MappingRunner({
        first: {"success": True, "columns": ["id"], "rows": [{"id": 1}]},
        ignored: {"success": True, "columns": ["name"], "rows": [{"name": "a"}]},
    })
    react = FakeReact([[
        SqlCandidateDraft(sql=first),
        SqlCandidateDraft(sql=ignored),
    ]] + [[]] * 5)

    StagedSqlPipeline(react=react, analyzer=FailingAnalyzer(), runner=runner).Run(
        question="How many items?", retrieval=retrieval()
    )

    assert runner.sql_seen == [first]


def test_pipeline_stops_after_same_failure_class_repeats_without_progress():
    runner = AlwaysFailingRunner()
    events = []
    react = FakeReact([
        [SqlCandidateDraft(sql="SELECT id FROM items WHERE id = 1")],
        [SqlCandidateDraft(sql="SELECT id FROM items WHERE id = 2")],
        [SqlCandidateDraft(sql="SELECT id FROM items WHERE id = 3")],
    ])

    result = StagedSqlPipeline(react=react, runner=runner).Run(
        question="List name",
        retrieval=retrieval(IntentContract(shape="listing", output_attributes=["name"])),
        emit=events.append,
    )

    assert runner.call_count == 2
    assert result["kind"] == "error"
    assert result["failure_class"] == "repeated_no_progress"
    assert any(event.get("failure_class") == "repeated_no_progress" for event in events)


def test_pipeline_runs_alternate_plan_only_after_empty_or_suspicious_result():
    empty_sql = "SELECT COUNT(*) AS count FROM items WHERE id < 0"
    wrong_shape_sql = "SELECT id FROM items"
    second_empty_sql = "SELECT COUNT(id) AS count FROM items WHERE id < -1"
    alternate_sql = "SELECT COUNT(*) AS count FROM items"
    react = FakeReact([
        [SqlCandidateDraft(sql=empty_sql)],
        [SqlCandidateDraft(sql=wrong_shape_sql)],
        [SqlCandidateDraft(sql=second_empty_sql)],
        [SqlCandidateDraft(sql=alternate_sql)],
    ])
    runner = MappingRunner({
        empty_sql: {"success": True, "columns": ["count"], "rows": []},
        wrong_shape_sql: {"success": True, "columns": ["id"], "rows": [{"id": 1}]},
        second_empty_sql: {"success": True, "columns": ["count"], "rows": []},
        alternate_sql: {"success": True, "columns": ["count"], "rows": [{"count": 3}]},
    })

    result = StagedSqlPipeline(
        react=react,
        analyzer=RecordingAnalyzer(),
        runner=runner,
    ).Run(question="How many items?", retrieval=retrieval())

    assert result["sql"] == alternate_sql
    assert [context["pipeline_stage"] for context in react.contexts] == [
        "initial", "targeted_repair_1", "targeted_repair_2", "alternate_plan"
    ]


def ten_table_retrieval_context():
    database = {
        "databaseId": "wide_demo",
        "databasePath": "/tmp/wide-demo.sqlite",
        "tables": [
            {
                "tableName": f"t{index}",
                "columns": [{"columnName": "id", "columnType": "integer"}],
            }
            for index in range(1, 10)
        ],
        "foreignKeys": [],
    }
    context = BirdSchemaIndex().Build([database]).Retrieve(
        "wide_demo", "show ninth records"
    )
    context["intent"] = IntentContract(shape="listing", output_attributes=["id"])
    return context


def test_pipeline_quality_gate_uses_authoritative_schema_beyond_compact_prompt():
    context = ten_table_retrieval_context()
    sql = "SELECT id FROM t9"
    runner = MappingRunner({
        sql: {
            "success": True,
            "columns": ["id"],
            "rows": [{"id": 9}, {"id": 90}],
        },
    })

    result = StagedSqlPipeline(
        react=FakeReact([[SqlCandidateDraft(sql=sql)]]),
        analyzer=RecordingAnalyzer(),
        runner=runner,
    ).Run(question="show ninth records", retrieval=context)

    assert "Table t9" not in context["schema_prompt"]
    assert result["sql"] == sql
    assert runner.sql_seen == [sql]


def test_pipeline_authoritative_schema_still_rejects_nonexistent_table():
    context = ten_table_retrieval_context()
    sql = "SELECT id FROM t10"
    runner = MappingRunner({
        sql: {"success": False, "error": "no such table: t10"},
    })

    result = StagedSqlPipeline(
        react=FakeReact([[SqlCandidateDraft(sql=sql)]]),
        analyzer=FailingAnalyzer(),
        runner=runner,
    ).Run(question="show tenth records", retrieval=context)

    assert result["kind"] == "error"
    assert runner.sql_seen == [sql]
