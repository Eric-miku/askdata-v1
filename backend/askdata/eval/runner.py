"""BIRD evaluation runner — self-contained: loads processed schema, calls LLM for SQL generation, executes both generated and gold SQL, compares results, and writes JSON reports."""

from datetime import UTC, datetime
import json
import re
import sqlite3
import time
from pathlib import Path

from askdata.core.config import settings
from askdata.core.llm import LLMClient
from askdata.core.paths import project_path
from askdata.eval.metrics import BirdResultComparer, ExactMatch
from askdata.tools.retriever import BirdSchemaIndex


class EvalRunner:
    """Self-contained BIRD SQL-generation evaluator."""

    def __init__(self, processed_dir=None, llm_client=None, comparer=None):
        base_dir = project_path(processed_dir or settings.BIRD_DATA_DIR)
        self.processed_dir = base_dir if (base_dir / "databases.json").exists() else base_dir / "processed"
        self.llm_client = llm_client or LLMClient()
        self.comparer = comparer or BirdResultComparer()

    def Run(self, database_id=None, limit=None, out=None) -> dict:
        started_at = datetime.now(UTC)
        databases = self._LoadJson("databases.json")
        questions = [item for item in self._LoadJson("questions.json") if item.get("goldSql") or item.get("SQL")]
        if database_id:
            questions = [item for item in questions if item.get("databaseId") == database_id or item.get("db_id") == database_id]
        if limit:
            questions = questions[:limit]

        index = BirdSchemaIndex().Build(databases)
        cases = [self._EvaluateQuestion(question, index) for question in questions]
        finished_at = datetime.now(UTC)
        report = {
            "summary": self._BuildSummary(cases, started_at, finished_at),
            "cases": cases,
        }
        if out:
            path = Path(out)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    def _LoadJson(self, filename: str):
        path = self.processed_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Missing BIRD processed file: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _EvaluateQuestion(self, question: dict, index: BirdSchemaIndex) -> dict:
        started = time.perf_counter()
        question_id = str(question.get("questionId") or question.get("question_id"))
        database_id = question.get("databaseId") or question.get("db_id")
        question_text = question.get("question") or ""
        gold_sql = question.get("goldSql") or question.get("SQL") or ""
        context = index.Retrieve(database_id, question_text)
        generated_sql = ""
        error = None
        valid_sql = False
        execution_succeeded = False
        generated_columns = []
        generated_rows = []
        gold_columns = []
        gold_rows = []
        comparison = self.comparer.BuildVerdict(False, False, None, "empty_prediction")

        try:
            generated_sql = self._CleanSql(self.llm_client.Complete(self._BuildSqlPrompt(question_text, context["schema_prompt"])))
            valid_sql = self._IsSelectSql(generated_sql)
            if not generated_sql:
                comparison = self.comparer.BuildVerdict(False, False, None, "empty_prediction")
            elif not valid_sql:
                comparison = self.comparer.BuildVerdict(False, False, None, "validation_error")
                error = "Only SELECT SQL is allowed"
            else:
                generated = self._ExecuteSql(generated_sql, context["database_path"])
                gold = self._ExecuteSql(gold_sql, context["database_path"])
                execution_succeeded = True
                generated_columns = generated["columns"]
                generated_rows = generated["rows"]
                gold_columns = gold["columns"]
                gold_rows = gold["rows"]
                comparison = self.comparer.Compare(
                    generated_columns,
                    generated_rows,
                    generated_sql,
                    gold_columns,
                    gold_rows,
                    gold_sql,
                )
        except Exception as exc:
            error = str(exc)
            if generated_sql and valid_sql:
                comparison = self.comparer.BuildVerdict(False, False, None, "execution_error")

        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "questionId": question_id,
            "databaseId": database_id,
            "difficulty": question.get("difficulty") or "unknown",
            "question": question_text,
            "goldSql": gold_sql,
            "goldColumns": gold_columns,
            "goldRows": gold_rows,
            "generatedSql": generated_sql,
            "generatedColumns": generated_columns,
            "generatedRows": generated_rows,
            "answer": "SQL generated." if generated_sql else "",
            "passed": comparison["passed"],
            "metrics": {
                "validSql": valid_sql,
                "executionSucceeded": execution_succeeded,
                "strictPass": comparison["strict_passed"],
                "relaxedPass": comparison["relaxed_passed"],
                "exactMatch": ExactMatch(generated_sql, gold_sql),
                "answerProduced": bool(generated_sql),
                "matchMode": comparison["match_mode"],
                "mismatchType": comparison["mismatch_type"],
            },
            "error": error,
            "latencyMs": latency_ms,
        }

    def _BuildSqlPrompt(self, question: str, schema_prompt: str) -> str:
        return "\n".join([
            "You are a Text-to-SQL assistant. Generate one SQLite SELECT statement only.",
            "Do not include markdown or explanation.",
            schema_prompt,
            f"Question: {question}",
            "SQL:",
        ])

    def _CleanSql(self, text: str) -> str:
        cleaned = (text or "").strip().strip("`").strip()
        if cleaned.lower().startswith("sql"):
            cleaned = cleaned[3:].strip()
        return cleaned.rstrip(";")

    def _IsSelectSql(self, sql: str) -> bool:
        cleaned = (sql or "").strip().rstrip(";")
        return bool(cleaned) and cleaned.lower().startswith("select") and ";" not in cleaned

    def _ExecuteSql(self, sql: str, database_path: str) -> dict:
        if not database_path:
            raise ValueError("Missing database_path for evaluation")
        cleaned = sql.strip().rstrip(";")
        if not re.search(r"\blimit\b", cleaned, re.I):
            cleaned += " LIMIT 1000"
        connection = sqlite3.connect(database_path)
        try:
            connection.row_factory = sqlite3.Row
            cursor = connection.execute(cleaned)
            columns = [item[0] for item in cursor.description or []]
            rows = [dict(row) for row in cursor.fetchall()]
            return {"columns": columns, "rows": rows}
        finally:
            connection.close()

    def _BuildSummary(self, cases: list[dict], started_at: datetime, finished_at: datetime) -> dict:
        return {
            "total": len(cases),
            "executionAccuracy": self._Rate(cases, lambda case: case["passed"]),
            "validSqlRate": self._Rate(cases, lambda case: case["metrics"]["validSql"]),
            "executionSuccessRate": self._Rate(cases, lambda case: case["metrics"]["executionSucceeded"]),
            "exactMatchRate": self._Rate(cases, lambda case: case["metrics"]["exactMatch"]),
            "answerProducedRate": self._Rate(cases, lambda case: case["metrics"]["answerProduced"]),
            "startedAt": started_at.isoformat(),
            "finishedAt": finished_at.isoformat(),
            "durationSeconds": round((finished_at - started_at).total_seconds(), 2),
        }

    def _Rate(self, cases: list[dict], predicate) -> float:
        if not cases:
            return 0.0
        return round(sum(1 for case in cases if predicate(case)) / len(cases), 4)
