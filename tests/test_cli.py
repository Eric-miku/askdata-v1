from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from typer.testing import CliRunner

from askdata import cli


runner = CliRunner()


def write_processed_dataset(root):
    processed = root / "processed"
    processed.mkdir()
    (processed / "databases.json").write_text(json.dumps([
        {
            "databaseId": "demo",
            "databasePath": "/tmp/demo.sqlite",
            "tables": [{"tableName": "items", "columns": []}],
            "foreignKeys": [],
        }
    ]), encoding="utf-8")
    return processed


def test_cli_help_lists_development_commands():
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "serve" in result.output
    assert "eval-bird" in result.output
    assert "chat" in result.output
    assert "databases" in result.output
    assert "gen-instructions" in result.output


def test_eval_bird_help_is_available():
    result = runner.invoke(cli.app, ["eval-bird", "--help"])

    assert result.exit_code == 0
    assert "--limit" in result.output
    assert "--out" in result.output
    assert "--processed-dir" in result.output


def test_databases_lists_processed_database_ids(tmp_path):
    processed = write_processed_dataset(tmp_path)

    result = runner.invoke(cli.app, ["databases", "--processed-dir", str(processed)])

    assert result.exit_code == 0
    assert "demo" in result.output
    assert "items" in result.output


def test_gen_instructions_writes_one_template_per_database(tmp_path):
    processed = write_processed_dataset(tmp_path)
    out_dir = tmp_path / "instructions"

    result = runner.invoke(cli.app, ["gen-instructions", "--processed-dir", str(processed), "--out-dir", str(out_dir)])

    assert result.exit_code == 0
    template = out_dir / "demo.md"
    assert template.exists()
    content = template.read_text(encoding="utf-8")
    assert "Business Term Mappings" in content
    assert "JOIN Patterns" in content


class FakeAgentGraph:
    def __init__(self):
        self.calls = []

    def Run(self, question, database_id, session_context=None):
        self.calls.append((question, database_id, session_context))
        return {
            "answer": "共有 3 条。",
            "sql": "SELECT COUNT(id) AS count FROM items",
            "columns": ["count"],
            "rows": [{"count": 3}],
            "trace": [],
            "error": None,
        }


def test_chat_session_runs_query_and_stores_last_sql():
    agent = FakeAgentGraph()
    session = cli.ChatSession(agent_graph=agent, database_id="demo")

    output = session.Ask("How many items?")

    assert "共有 3 条。" in output
    assert "SELECT COUNT(id) AS count FROM items" in output
    assert session.last_sql == "SELECT COUNT(id) AS count FROM items"
    assert agent.calls[0][1] == "demo"
