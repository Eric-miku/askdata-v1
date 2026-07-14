"""BIRD evaluation runner — runs AgentGraph (ReAct loop) against BIRD questions and compares results with gold SQL."""

from datetime import UTC, datetime
import json
import random
import re
import sqlite3
import time
from pathlib import Path

from tqdm import tqdm

from askdata.agent.graph import AgentGraph
from askdata.core.config import settings
from askdata.core.paths import project_path
from askdata.eval.metrics import BirdResultComparer, ExactMatch
from askdata.tools.retriever import BirdSchemaIndex


class EvalRunner:
    """BIRD evaluator that uses the full ReAct agent pipeline."""

    def __init__(self, processed_dir=None, agent_graph=None, comparer=None):
        base_dir = project_path(processed_dir or settings.BIRD_DATA_DIR)
        self.processed_dir = base_dir if (base_dir / "databases.json").exists() else base_dir / "processed"
        self.agent_graph = agent_graph
        self.comparer = comparer or BirdResultComparer()

    def Run(self, database_id=None, limit=None, out=None, seed=None) -> dict:
        started_at = datetime.now(UTC)
        databases = self._LoadJson("databases.json")
        questions = [item for item in self._LoadJson("questions.json") if item.get("goldSql") or item.get("SQL")]
        if database_id:
            questions = [item for item in questions if item.get("databaseId") == database_id or item.get("db_id") == database_id]
        if seed is not None:
            random.Random(seed).shuffle(questions)
        if limit:
            questions = questions[:limit]

        index = BirdSchemaIndex().Build(databases, questions=questions)
        cases = [self._EvaluateQuestion(question, index) for question in tqdm(questions, desc="eval", unit="q")]
        finished_at = datetime.now(UTC)
        report = {
            "summary": self._BuildSummary(cases, started_at, finished_at),
            "byDatabase": self._BuildBreakdown(cases, "databaseId"),
            "byDifficulty": self._BuildBreakdown(cases, "difficulty"),
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
        database_path = context.get("database_path", "")
        generated_sql = ""
        error = None
        execution_succeeded = False
        gold_columns = []
        gold_rows = []
        comparison = self.comparer.BuildVerdict(False, False, None, "empty_prediction")
        result = {}

        try:
            agent = self.agent_graph or AgentGraph(processed_dir=self.processed_dir)
            result = agent.Run(question=question_text, database_id=database_id)
            generated_sql = result.get("sql") or ""
            error = result.get("error")

            if not generated_sql:
                comparison = self.comparer.BuildVerdict(False, False, None, "empty_prediction")
            elif not database_path:
                comparison = self.comparer.BuildVerdict(False, False, None, "execution_error")
                error = f"No database path for {database_id}"
            else:
                gold = self._ExecuteSql(gold_sql, database_path)
                execution_succeeded = True
                gold_columns = gold["columns"]
                gold_rows = gold["rows"]
                comparison = self.comparer.Compare(
                    result.get("columns", []),
                    result.get("rows", []),
                    generated_sql,
                    gold_columns,
                    gold_rows,
                    gold_sql,
                )
        except Exception as exc:
            error = error or str(exc)
            if generated_sql:
                comparison = self.comparer.BuildVerdict(False, False, None, "execution_error")

        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        trace_steps = result.get("trace", [])
        return {
            "questionId": question_id,
            "databaseId": database_id,
            "difficulty": question.get("difficulty") or "unknown",
            "question": question_text,
            "goldSql": gold_sql,
            "goldColumns": gold_columns,
            "goldRows": gold_rows,
            "generatedSql": generated_sql,
            "generatedColumns": result.get("columns", []),
            "generatedRows": result.get("rows", []),
            "answer": result.get("answer", ""),
            "passed": comparison["passed"],
            "metrics": {
                "validSql": bool(generated_sql),
                "executionSucceeded": execution_succeeded,
                "strictPass": comparison["strict_passed"],
                "relaxedPass": comparison["relaxed_passed"],
                "exactMatch": ExactMatch(generated_sql, gold_sql),
                "answerProduced": bool(result.get("answer")),
                "matchMode": comparison["match_mode"],
                "mismatchType": comparison["mismatch_type"],
                "retryOrRepair": any(s.get("status") == "retry" for s in trace_steps),
            },
            "error": error,
            "latencyMs": latency_ms,
            "trace": trace_steps,
        }

    def _ExecuteSql(self, sql: str, database_path: str) -> dict:
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

    def _BuildBreakdown(self, cases: list[dict], key: str) -> dict:
        groups: dict[str, list] = {}
        for case in cases:
            value = case.get(key) or "unknown"
            groups.setdefault(value, []).append(case)
        return {value: self._BuildGroupSummary(items) for value, items in sorted(groups.items())}

    def _BuildGroupSummary(self, cases: list[dict]) -> dict:
        latencies = [case["latencyMs"] for case in cases]
        return {
            "total": len(cases),
            "executionAccuracy": self._Rate(cases, lambda c: c["passed"]),
            "validSqlRate": self._Rate(cases, lambda c: c["metrics"]["validSql"]),
            "avgLatencyMs": round(sum(latencies) / len(latencies), 2) if latencies else 0,
        }
