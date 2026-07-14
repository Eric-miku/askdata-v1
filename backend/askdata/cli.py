"""Typer command-line entry points for AskData development workflows."""

from pathlib import Path

import typer

from askdata.agent.graph import AgentGraph
from askdata.core.config import settings
from askdata.core.paths import project_path
from askdata.data.bird_io import LoadProcessedDatabases, ResolveProcessedDir
from askdata.eval import EvalRunner
from askdata.tools.retriever import GetValue


app = typer.Typer(help="AskData — NL2SQL development CLI")


def _ResolveProcessedDir(processed_dir: Path | None = None) -> Path:
    return ResolveProcessedDir(processed_dir or settings.BIRD_DATA_DIR)


def _LoadDatabases(processed_dir: Path | None = None) -> list[dict]:
    try:
        return LoadProcessedDatabases(processed_dir or settings.BIRD_DATA_DIR)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc


class ChatSession:
    """Small interactive shell around AgentGraph."""

    def __init__(self, agent_graph=None, database_id: str | None = None, processed_dir: Path | None = None):
        self.agent_graph = agent_graph or AgentGraph(processed_dir=processed_dir)
        self.database_id = database_id
        self.processed_dir = processed_dir
        self.last_question = None
        self.last_sql = None

    def Start(self):
        typer.echo("AskData chat. Type /help for commands, /quit to exit.")
        if self.database_id:
            typer.echo(f"Database: {self.database_id}")
        while True:
            try:
                question = input("askdata> ").strip()
            except (EOFError, KeyboardInterrupt):
                typer.echo("\nBye.")
                break
            if not question:
                continue
            if question.startswith("/"):
                if self.HandleCommand(question):
                    break
                continue
            typer.echo(self.Ask(question))

    def Ask(self, question: str) -> str:
        database_id = self.database_id or self._DefaultDatabaseId()
        session_context = {"last_question": self.last_question, "last_sql": self.last_sql}
        result = self.agent_graph.Run(question=question, database_id=database_id, session_context=session_context)
        self.database_id = database_id
        self.last_question = question
        self.last_sql = result.get("sql")
        lines = [result.get("answer") or ""]
        if result.get("sql"):
            lines.append(f"SQL: {result['sql']}")
        if result.get("error"):
            lines.append(f"Error: {result['error']}")
        return "\n".join(line for line in lines if line)

    def HandleCommand(self, command: str) -> bool:
        parts = command.split()
        name = parts[0].lower()
        if name in {"/quit", "/exit", "/q"}:
            typer.echo("Bye.")
            return True
        if name == "/help":
            typer.echo("/help                 Show commands")
            typer.echo("/databases            List available databases")
            typer.echo("/use <database_id>    Switch database")
            typer.echo("/quit                 Exit chat")
            return False
        if name == "/databases":
            self.PrintDatabases()
            return False
        if name == "/use" and len(parts) >= 2:
            self.database_id = parts[1]
            typer.echo(f"Database: {self.database_id}")
            return False
        typer.echo(f"Unknown command: {command}")
        return False

    def PrintDatabases(self):
        for database in _LoadDatabases(self.processed_dir):
            database_id = GetValue(database, "databaseId", "database_id")
            table_count = len(GetValue(database, "tables", default=[]))
            typer.echo(f"{database_id}\t{table_count} tables")

    def _DefaultDatabaseId(self) -> str:
        databases = _LoadDatabases(self.processed_dir)
        if not databases:
            raise typer.BadParameter("No databases found in processed schema")
        return GetValue(databases[0], "databaseId", "database_id")


@app.command("serve")
def Serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    reload: bool = typer.Option(False, "--reload"),
):
    """Run the FastAPI backend server."""
    import uvicorn
    uvicorn.run("askdata.api.app:app", host=host, port=port, reload=reload)


@app.command("eval-bird")
def EvalBird(
    processed_dir: Path | None = typer.Option(None, "--processed-dir", help="BIRD processed directory"),
    database_id: str | None = typer.Option(None, "--database-id", "-d", help="Evaluate only this database"),
    limit: int | None = typer.Option(None, "--limit", "-n", help="Limit evaluation count"),
    seed: int | None = typer.Option(None, "--seed", help="Shuffle questions with this random seed before applying --limit"),
    question_manifest: Path | None = typer.Option(None, "--question-manifest", help="JSON list of exact BIRD question IDs; overrides --seed and --limit"),
    model_name: str | None = typer.Option(None, "--model-name", help="Override LLM_MODEL_NAME for this evaluation run"),
    out: Path = typer.Option(Path("reports/bird-eval.json"), "--out", "-o", help="JSON report output path"),
):
    """Run BIRD evaluation using the full ReAct agent pipeline."""
    if model_name:
        settings.LLM_MODEL_NAME = model_name
    report = EvalRunner(processed_dir=processed_dir).Run(
        database_id=database_id,
        limit=limit,
        out=str(out),
        seed=seed,
        question_manifest=question_manifest,
    )
    summary = report["summary"]
    typer.echo(f"Total: {summary['total']}")
    typer.echo(f"Execution Accuracy: {summary['executionAccuracy']:.2%}")
    typer.echo(f"Strict Execution Accuracy: {summary['executionAccuracyStrict']:.2%}")
    typer.echo(f"Valid SQL Rate: {summary['validSqlRate']:.2%}")
    typer.echo(f"Exact Match Rate: {summary['exactMatchRate']:.2%}")
    typer.echo(f"Report: {out}")


@app.command("databases")
def Databases(
    processed_dir: Path | None = typer.Option(None, "--processed-dir", help="BIRD processed directory"),
):
    """List processed databases."""
    for database in _LoadDatabases(processed_dir):
        database_id = GetValue(database, "databaseId", "database_id")
        tables = GetValue(database, "tables", default=[])
        table_names = ", ".join(GetValue(table, "tableName", "table_name", default="") for table in tables[:5])
        typer.echo(f"{database_id}\t{len(tables)} tables\t{table_names}")


@app.command("gen-instructions")
def GenInstructions(
    processed_dir: Path | None = typer.Option(None, "--processed-dir", help="BIRD processed directory"),
    out_dir: Path = typer.Option(Path("data/bird/instructions"), "--out-dir", help="Instruction template output directory"),
):
    """Generate per-database business-context instruction templates."""
    databases = _LoadDatabases(processed_dir)
    out_path = project_path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    for database in databases:
        database_id = GetValue(database, "databaseId", "database_id")
        table_lines = []
        for table in GetValue(database, "tables", default=[]):
            table_name = GetValue(table, "tableName", "table_name", default="")
            columns = ", ".join(GetValue(column, "columnName", "column_name", default="") for column in GetValue(table, "columns", default=[]))
            table_lines.append(f"- {table_name}: {columns}")
        content = "\n".join([
            f"# {database_id}",
            "",
            "## Business Term Mappings",
            "- Add domain phrases here, for example: active customer -> customers.status = 'active'",
            "",
            "## JOIN Patterns",
            "- Add important joins here, for example: orders.customer_id = customers.id",
            "",
            "## Schema Notes",
            *table_lines,
            "",
        ])
        (out_path / f"{database_id}.md").write_text(content, encoding="utf-8")
    typer.echo(f"Generated {len(databases)} instruction files in {out_path}")


@app.command("chat")
def Chat(
    database_id: str | None = typer.Option(None, "--database-id", "-d", help="Force a database"),
    processed_dir: Path | None = typer.Option(None, "--processed-dir", help="BIRD processed directory"),
):
    """Start an interactive NL2SQL chat session."""
    ChatSession(database_id=database_id, processed_dir=processed_dir).Start()


def main():
    app()


if __name__ == "__main__":
    main()
