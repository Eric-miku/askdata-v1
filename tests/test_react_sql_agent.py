from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.agent.react_sql_agent import ReActSqlAgent


def tool_call(arguments, call_id="call_1"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name="run_query", arguments=json.dumps(arguments)),
    )


class FakeToolCallingLLM:
    def __init__(self):
        self.messages_seen = []

    def Chat(self, messages, tools=None):
        self.messages_seen.append(messages)
        tool_messages = [message for message in messages if message.get("role") == "tool"]
        if not tool_messages:
            return SimpleNamespace(content="I will query the table.", tool_calls=[tool_call({"sql": "SELECT COUNT(missing) AS count FROM items"})])
        if "Error:" in tool_messages[-1]["content"]:
            return SimpleNamespace(content="I will repair the SQL.", tool_calls=[tool_call({"sql": "SELECT COUNT(id) AS count FROM items"}, "call_2")])
        return SimpleNamespace(content="共有 3 条。", tool_calls=[])


def test_react_sql_agent_repairs_sql_after_tool_error(tmp_path):
    database_path = tmp_path / "demo.sqlite"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE items(id INTEGER)")
    connection.executemany("INSERT INTO items(id) VALUES (?)", [(1,), (2,), (3,)])
    connection.commit()
    connection.close()

    llm = FakeToolCallingLLM()
    agent = ReActSqlAgent(llm_client=llm)

    result = agent.Run(
        question="How many items?",
        schema_prompt="Database: demo\nTable items(id integer)",
        database_path=str(database_path),
    )

    assert result["answer"] == "共有 3 条。"
    assert result["sql"] == "SELECT COUNT(id) AS count FROM items"
    assert result["columns"] == ["count"]
    assert result["rows"] == [{"count": 3}]
    assert [step["step"] for step in result["trace"]] == [
        "Reason-1",
        "GenerateSql",
        "ExecuteSql",
        "Reason-2",
        "GenerateSql",
        "ExecuteSql",
        "Reason-3",
    ]
    assert len(llm.messages_seen) == 3
