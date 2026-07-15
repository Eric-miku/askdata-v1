import asyncio
from pathlib import Path
import json
import sqlite3
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.agent.graph import AgentGraph


class FakeLLM:
    def Complete(self, prompt):
        assert "COLUMN SELECTION" in prompt
        assert "Table items" in prompt
        return "SELECT COUNT(id) AS count FROM items"


class FakeReactAgent:
    def __init__(self):
        self.called = False

    def Run(self, question, schema_prompt, database_path, session_context=None):
        self.called = True
        assert question == "How many items?"
        assert "Table items" in schema_prompt
        assert database_path.endswith("demo.sqlite")
        return {
            "answer": "共有 3 条。",
            "sql": "SELECT COUNT(id) AS count FROM items",
            "columns": ["count"],
            "rows": [{"count": 3}],
            "trace": [{"step": "Reason-1", "status": "success", "message": "using tool"}],
        }

    def GenerateCandidates(self, question, schema_prompt, session_context=None):
        from askdata.agent.react_sql_agent import SqlCandidateDraft

        self.called = True
        assert question == "How many items?"
        assert "Table items" in schema_prompt
        return [SqlCandidateDraft(sql="SELECT COUNT(id) AS count FROM items")]


class FakeAnalyzer:
    def Analyze(self, question, sql, columns, rows):
        return f"共有 {rows[0]['count']} 条。"


class GroundingFailureReact:
    def __init__(self):
        self.index = 0

    def GenerateCandidates(self, question, schema_prompt, session_context=None):
        from askdata.agent.react_sql_agent import SqlCandidateDraft

        self.index += 1
        if self.index == 2:
            return [SqlCandidateDraft(sql="SELECT id FROM items")]
        return [SqlCandidateDraft(sql=f"SELECT missing_{self.index} FROM items")]


class TrackingRetriever:
    def __init__(self, context):
        self.index = self
        self.context = context
        self.calls = 0

    def Retrieve(self, database_id, question):
        self.calls += 1
        return dict(self.context)


def write_processed_dataset(root, database_path):
    processed = root / "processed"
    processed.mkdir()
    (processed / "databases.json").write_text(json.dumps([
        {
            "databaseId": "demo",
            "databasePath": str(database_path),
            "tables": [
                {
                    "tableName": "items",
                    "columns": [
                        {"columnName": "id", "columnType": "integer", "isPrimary": True},
                    ],
                }
            ],
            "foreignKeys": [],
        }
    ]), encoding="utf-8")
    return processed


def test_agent_graph_runs_real_retriever_llm_executor_analyzer_chain(tmp_path):
    database_path = tmp_path / "demo.sqlite"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE items(id INTEGER)")
    connection.executemany("INSERT INTO items(id) VALUES (?)", [(1,), (2,), (3,)])
    connection.commit()
    connection.close()
    processed = write_processed_dataset(tmp_path, database_path)

    graph = AgentGraph(processed_dir=processed, llm_client=FakeLLM(), analyzer=FakeAnalyzer())
    result = graph.Run(question="How many items?", database_id="demo")

    assert result["answer"] == "共有 3 条。"
    assert result["sql"] == "SELECT COUNT(id) AS count FROM items"
    assert result["columns"] == ["count"]
    assert result["rows"] == [{"count": 3}]
    assert [step["step"] for step in result["trace"]] == [
        "RetrieveSchema",
        "GenerateSql",
        "ValidateSql",
        "ExecuteSql",
        "AnalyzeResult",
    ]


def test_agent_graph_can_delegate_sql_work_to_react_agent(tmp_path):
    database_path = tmp_path / "demo.sqlite"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE items(id INTEGER)")
    connection.executemany("INSERT INTO items(id) VALUES (?)", [(1,), (2,), (3,)])
    connection.commit()
    connection.close()
    processed = write_processed_dataset(tmp_path, database_path)
    react_agent = FakeReactAgent()

    graph = AgentGraph(processed_dir=processed, react_agent=react_agent, analyzer=FakeAnalyzer())
    result = graph.Run(question="How many items?", database_id="demo")

    assert react_agent.called is True
    assert result["answer"] == "共有 3 条。"
    assert result["sql"] == "SELECT COUNT(id) AS count FROM items"
    assert [step["step"] for step in result["trace"]] == [
        "RetrieveSchema", "GenerateSql", "ValidateSql", "ExecuteSql", "AnalyzeResult"
    ]


def test_agent_graph_uses_staged_pipeline_for_chat_llm(tmp_path):
    database_path = tmp_path / "demo.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE items(id INTEGER)")
        connection.executemany("INSERT INTO items(id) VALUES (?)", [(1,), (2,), (3,)])
    processed = write_processed_dataset(tmp_path, database_path)
    react_agent = FakeReactAgent()
    events = []

    result = AgentGraph(
        processed_dir=processed,
        react_agent=react_agent,
        analyzer=FakeAnalyzer(),
    ).Run(question="How many items?", database_id="demo", emit=events.append)

    assert react_agent.called is True
    assert result["sql"] == "SELECT COUNT(id) AS count FROM items"
    assert result["rows"] == [{"count": 3}]
    assert events[0] == {
        "step": "RetrieveSchema",
        "status": "success",
        "message": "Schema matched.",
    }


def test_agent_graph_keeps_one_shot_fallback_for_llm_without_chat(tmp_path):
    database_path = tmp_path / "demo.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE items(id INTEGER)")
        connection.executemany("INSERT INTO items(id) VALUES (?)", [(1,), (2,), (3,)])
    processed = write_processed_dataset(tmp_path, database_path)

    result = AgentGraph(
        processed_dir=processed,
        llm_client=FakeLLM(),
        analyzer=FakeAnalyzer(),
    ).Run(question="How many items?", database_id="demo")

    assert result["sql"] == "SELECT COUNT(id) AS count FROM items"
    assert result["rows"] == [{"count": 3}]


def test_agent_graph_retrieves_again_at_grounding_expansion_stage(tmp_path):
    database_path = tmp_path / "demo.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE items(id INTEGER)")
    retriever = TrackingRetriever({
        "database_id": "demo",
        "database_path": str(database_path),
        "schema_prompt": "Database: demo\nTable items(id integer)",
    })

    result = AgentGraph(
        retriever=retriever,
        react_agent=GroundingFailureReact(),
        analyzer=FakeAnalyzer(),
    ).Run(question="List items", database_id="demo")

    assert result["kind"] == "error"
    assert result["retrieval_expanded"] is True
    assert retriever.calls == 2


@pytest.mark.asyncio
async def test_agent_graph_arun_does_not_block_event_loop():
    graph = object.__new__(AgentGraph)
    run_entered = threading.Event()
    release_run = threading.Event()
    heartbeat_ran = threading.Event()

    def blocking_run(**kwargs):
        run_entered.set()
        release_run.wait(timeout=3)
        return {"answer": "done"}

    graph.Run = blocking_run

    def coordinate_release():
        assert run_entered.wait(timeout=2)
        observed_heartbeat = heartbeat_ran.wait(timeout=2)
        release_run.set()
        return observed_heartbeat

    coordinator = asyncio.create_task(asyncio.to_thread(coordinate_release))
    await asyncio.sleep(0)
    result_task = asyncio.create_task(graph.ARun("question", "demo"))
    asyncio.get_running_loop().call_soon(heartbeat_ran.set)

    result = await result_task

    assert result == {"answer": "done"}
    assert await coordinator is True
