from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.agent.react_sql_agent import ReActSqlAgent
from askdata.agent.prompts import BuildReActSystemPrompt


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


class ScriptedToolCallingLLM:
    def __init__(self, steps):
        self.steps = steps
        self.index = 0
        self.messages_seen = []

    def Chat(self, messages, tools=None):
        self.messages_seen.append(list(messages))
        step = self.steps[self.index]
        self.index += 1
        if "sql" in step:
            return SimpleNamespace(
                content=step.get("content", "I will query."),
                tool_calls=[tool_call({"sql": step["sql"]}, f"call_{self.index}")],
            )
        return SimpleNamespace(content=step["content"], tool_calls=[])


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


def test_react_sql_agent_prompt_includes_bird_specific_intern_agent_rules():
    agent = ReActSqlAgent(llm_client=ScriptedToolCallingLLM([{"content": "done"}]))

    messages = agent._BuildMessages(
        question="What is the average writing score of schools managed by Ricci Ulrich?",
        schema_prompt="Evidence: Average of average math = sum(average math scores) / count(schools)",
        session_context=None,
    )

    system_prompt = messages[0]["content"]
    assert system_prompt.startswith(BuildReActSystemPrompt())
    assert "If the schema evidence defines a formula" in system_prompt
    assert "writing score of schools managed by Ricci Ulrich" in system_prompt
    assert "Never use SELECT *" in system_prompt
    assert "SELECT column discipline" in system_prompt
    assert "Normalize date-like text with SQLite date functions" in system_prompt
    assert "prefer stable identifier columns such as id" in system_prompt
    assert "select only the rate expression" in system_prompt
    assert "do not include helper ranking/count columns" in system_prompt
    assert "do not concatenate address fields" in system_prompt


def test_react_sql_agent_keeps_count_sql_when_later_detail_query_is_exploratory(tmp_path):
    database_path = tmp_path / "demo.sqlite"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE items(id INTEGER, name TEXT)")
    connection.executemany("INSERT INTO items(id, name) VALUES (?, ?)", [(1, "a"), (2, "b"), (3, "c")])
    connection.commit()
    connection.close()

    llm = ScriptedToolCallingLLM([
        {"sql": "SELECT COUNT(id) AS count FROM items", "content": "Count first."},
        {"sql": "SELECT id, name FROM items", "content": "Double-check details."},
        {"content": "There are 3 items."},
    ])
    agent = ReActSqlAgent(llm_client=llm)

    result = agent.Run(
        question="How many items are there?",
        schema_prompt="Database: demo\nTable items(id integer, name text)",
        database_path=str(database_path),
    )

    assert result["sql"] == "SELECT COUNT(id) AS count FROM items"
    assert result["columns"] == ["count"]
    assert result["rows"] == [{"count": 3}]


def test_react_sql_agent_rejects_count_as_final_sql_for_list_question(tmp_path):
    database_path = tmp_path / "demo.sqlite"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE items(id INTEGER, name TEXT)")
    connection.executemany("INSERT INTO items(id, name) VALUES (?, ?)", [(1, "a"), (2, "b")])
    connection.commit()
    connection.close()

    llm = ScriptedToolCallingLLM([
        {"sql": "SELECT name FROM items ORDER BY name", "content": "List names."},
        {"sql": "SELECT COUNT(*) AS count FROM items", "content": "Check count."},
        {"content": "The names are a and b."},
    ])
    agent = ReActSqlAgent(llm_client=llm)

    result = agent.Run(
        question="List the names of all items.",
        schema_prompt="Database: demo\nTable items(id integer, name text)",
        database_path=str(database_path),
    )

    assert result["sql"] == "SELECT name FROM items ORDER BY name"
    assert result["columns"] == ["name"]
    assert result["rows"] == [{"name": "a"}, {"name": "b"}]


def test_react_sql_agent_prefers_average_sql_for_average_number_question(tmp_path):
    database_path = tmp_path / "demo.sqlite"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE items(id INTEGER, takers INTEGER)")
    connection.executemany("INSERT INTO items(id, takers) VALUES (?, ?)", [(1, 10), (2, 20)])
    connection.commit()
    connection.close()

    llm = ScriptedToolCallingLLM([
        {"sql": "SELECT COUNT(*) FROM items", "content": "Check count."},
        {"sql": "SELECT AVG(takers) AS avg_takers FROM items", "content": "Compute average."},
        {"content": "The average is 15."},
    ])
    agent = ReActSqlAgent(llm_client=llm)

    result = agent.Run(
        question="What is the average number of test takers?",
        schema_prompt="Database: demo\nTable items(id integer, takers integer)",
        database_path=str(database_path),
    )

    assert result["sql"] == "SELECT AVG(takers) AS avg_takers FROM items"
    assert result["columns"] == ["avg_takers"]
    assert result["rows"] == [{"avg_takers": 15.0}]


def test_react_sql_agent_allows_count_inside_most_query_when_target_is_listed(tmp_path):
    database_path = tmp_path / "demo.sqlite"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE wins(team TEXT)")
    connection.executemany("INSERT INTO wins(team) VALUES (?)", [("Rangers",), ("Rangers",), ("Celtic",)])
    connection.commit()
    connection.close()

    llm = ScriptedToolCallingLLM([
        {"sql": "SELECT 'Scotland Premier League' AS name", "content": "Find league."},
        {"sql": "SELECT team FROM wins GROUP BY team ORDER BY COUNT(*) DESC LIMIT 1", "content": "Find most wins."},
        {"content": "Rangers won the most."},
    ])
    agent = ReActSqlAgent(llm_client=llm)

    result = agent.Run(
        question="Which away team won the most?",
        schema_prompt="Database: demo\nTable wins(team text)",
        database_path=str(database_path),
    )

    assert result["sql"] == "SELECT team FROM wins GROUP BY team ORDER BY COUNT(*) DESC LIMIT 1"
    assert result["columns"] == ["team"]
    assert result["rows"] == [{"team": "Rangers"}]


def test_react_sql_agent_requests_second_candidate_after_shape_warning(tmp_path):
    database_path = tmp_path / "demo.sqlite"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE items(id INTEGER, name TEXT)")
    connection.executemany("INSERT INTO items(id, name) VALUES (?, ?)", [(1, "a"), (2, "b")])
    connection.commit()
    connection.close()
    llm = ScriptedToolCallingLLM([
        {"sql": "SELECT name FROM items", "content": "Inspect items."},
        {"content": "There are two."},
        {"sql": "SELECT COUNT(*) AS count FROM items", "content": "Correct the output shape."},
        {"content": "There are two."},
    ])

    result = ReActSqlAgent(llm_client=llm).Run(
        question="How many items are there?",
        schema_prompt="Database: demo\nTable items(id integer, name text)",
        database_path=str(database_path),
    )

    assert result["sql"] == "SELECT COUNT(*) AS count FROM items"
    assert result["rows"] == [{"count": 2}]
    assert any(step["step"] == "ReviewAnswerShape" for step in result["trace"])
    first_tool_message = next(
        message for message in llm.messages_seen[1]
        if message.get("role") == "tool"
    )
    payload = json.loads(first_tool_message["content"])
    assert payload["shapeWarnings"] == ["Question asks for a count, but SQL does not use COUNT."]


def test_react_sql_agent_retains_two_candidates_but_still_executes_later_final_sql(tmp_path):
    database_path = tmp_path / "demo.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE items(id INTEGER)")
        connection.executemany("INSERT INTO items(id) VALUES (?)", [(1,), (2,)])
    llm = ScriptedToolCallingLLM([
        {"sql": "SELECT id FROM items", "content": "First."},
        {"sql": "SELECT COUNT(*) AS count FROM items", "content": "Second."},
        {"sql": "SELECT COUNT(*) AS count FROM items WHERE id > 0", "content": "Third."},
        {"content": "Done."},
    ])

    result = ReActSqlAgent(llm_client=llm).Run(
        question="How many items?",
        schema_prompt="Database: demo\nTable items(id integer)",
        database_path=str(database_path),
    )

    assert result["sql"] == "SELECT COUNT(*) AS count FROM items WHERE id > 0"
    assert sum(step["step"] == "ExecuteSql" for step in result["trace"]) == 3
    assert not any(step["step"] == "CandidateLimit" for step in result["trace"])


def test_react_sql_agent_uses_valid_llm_judge_verdict_for_successful_candidates(tmp_path):
    database_path = tmp_path / "demo.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE items(id INTEGER, name TEXT)")
        connection.executemany("INSERT INTO items(id, name) VALUES (?, ?)", [(1, "a"), (2, "b")])

    class JudgeLlm(ScriptedToolCallingLLM):
        def Chat(self, messages, tools=None):
            if tools is None and "NL2SQL result judge" in messages[0]["content"]:
                return SimpleNamespace(content='{"best_index": 1, "score": 98, "reason": "The count answers directly."}', tool_calls=[])
            return super().Chat(messages, tools)

    llm = JudgeLlm([
        {"sql": "SELECT COUNT(*) AS count FROM items", "content": "Count items."},
        {"sql": "SELECT name FROM items ORDER BY name", "content": "Inspect names."},
        {"content": "There are two items."},
    ])
    result = ReActSqlAgent(llm_client=llm).Run(
        question="How many items are there?",
        schema_prompt="Database: demo\nTable items(id integer, name text)",
        database_path=str(database_path),
    )

    assert result["sql"] == "SELECT COUNT(*) AS count FROM items"
    assert len(result["candidates"]) == 2
    assert any(step["step"] == "SelectBestCandidate" and "count answers directly" in step["message"] for step in result["trace"])
