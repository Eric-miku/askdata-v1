"""BIRD evaluation runner using the normalized data-processing contract."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
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
from askdata.data.bird_io import (
    LoadProcessedDatabases,
    LoadProcessedQuestions,
    LoadQuestionManifest,
    ResolveProcessedDir,
)
from askdata.eval.metrics import BirdResultComparer, ExactMatch
from askdata.tools.retriever import BirdSchemaIndex


class EvalRunner:
    """BIRD evaluator that uses the full ReAct agent pipeline."""

    def __init__(self, processed_dir=None, agent_graph=None, comparer=None):
        self.processed_dir = ResolveProcessedDir(processed_dir or settings.BIRD_DATA_DIR)
        self.agent_graph = agent_graph
        self.comparer = comparer or BirdResultComparer()

    def Run(self, database_id=None, limit=None, out=None, seed=None, question_manifest=None) -> dict:
        started_at = datetime.now(timezone.utc)
        databases = LoadProcessedDatabases(self.processed_dir)
        database_ids = {item["database_id"] for item in databases}
        questions = [
            item for item in LoadProcessedQuestions(self.processed_dir, database_ids=database_ids)
            if item["gold_sql"]
        ]
        if database_id:
            questions = [item for item in questions if item["database_id"] == database_id]

        manifest_path = None
        manifest_hash = None
        if question_manifest:
            manifest_path = project_path(question_manifest).resolve()
            manifest_ids = LoadQuestionManifest(manifest_path)
            by_id = {item["question_id"]: item for item in questions}
            unknown_ids = [question_id for question_id in manifest_ids if question_id not in by_id]
            if unknown_ids:
                raise ValueError(f"Question manifest contains unknown IDs: {', '.join(unknown_ids[:10])}")
            questions = [by_id[question_id] for question_id in manifest_ids]
            manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        else:
            if seed is not None:
                random.Random(seed).shuffle(questions)
            if limit:
                questions = questions[:limit]

        index = BirdSchemaIndex().Build(databases, questions=questions)
        cases = [self._EvaluateQuestion(question, index) for question in tqdm(questions, desc="eval", unit="q")]
        finished_at = datetime.now(timezone.utc)
        report = {
            "summary": self._BuildSummary(cases, started_at, finished_at),
            "byDatabase": self._BuildBreakdown(cases, "databaseId"),
            "byDifficulty": self._BuildBreakdown(cases, "difficulty"),
            "cases": cases,
            "metadata": {
                "modelName": settings.LLM_MODEL_NAME,
                "questionManifest": str(manifest_path) if manifest_path else None,
                "questionManifestSha256": manifest_hash,
                "processedDataSha256": self._ProcessedDataFingerprint(),
                "seed": seed,
                "limit": limit,
            },
        }
        if out:
            path = Path(out)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    def _EvaluateQuestion(self, question: dict, index: BirdSchemaIndex) -> dict:
        started = time.perf_counter()
        question_id = question["question_id"]
        database_id = question["database_id"]
        question_text = question["question"]
        gold_sql = question["gold_sql"]
        context = index.Retrieve(database_id, question_text)
        database_path = context["database_path"]
        generated_sql = ""
        error = None
        execution_succeeded = False
        gold_columns = []
        gold_rows = []
        comparison = self.comparer.BuildVerdict(False, False, None, "empty_prediction")
        result = {}
        candidate_sqls: list[str] = []
        candidate_outcomes: list[dict] = []

        try:
            agent = self.agent_graph or AgentGraph(processed_dir=self.processed_dir)
            result = agent.Run(question=question_text, database_id=database_id)
            generated_sql = result.get("sql") or ""
            error = result.get("error")

            candidates = result.get("candidates", [])
            candidate_sqls = [c.get("sql", "") for c in candidates if c.get("sql")]

            gold = self._ExecuteSql(gold_sql, database_path)
            execution_succeeded = True
            gold_columns = gold["columns"]
            gold_rows = gold["rows"]
            if generated_sql:
                comparison = self.comparer.Compare(
                    result.get("columns", []),
                    result.get("rows", []),
                    generated_sql,
                    gold_columns,
                    gold_rows,
                    gold_sql,
                )
            else:
                comparison = self.comparer.BuildVerdict(False, False, None, "empty_prediction")

            for candidate in candidates:
                candidate_sql = candidate.get("sql") or ""
                if not candidate_sql:
                    continue
                # Use the in-agent result when available so the evaluator does
                # not execute an LLM-produced query a second time unnecessarily.
                candidate_columns = candidate.get("columns") or []
                candidate_rows = candidate.get("rows") or []
                candidate_verdict = self.comparer.Compare(
                    candidate_columns,
                    candidate_rows,
                    candidate_sql,
                    gold_columns,
                    gold_rows,
                    gold_sql,
                )
                candidate_outcomes.append({
                    "sql": candidate_sql,
                    "strictPass": candidate_verdict["strict_passed"],
                    "relaxedPass": candidate_verdict["relaxed_passed"],
                    "matchMode": candidate_verdict["match_mode"],
                })

        except Exception as exc:
            error = error or str(exc)
            if generated_sql:
                comparison = self.comparer.BuildVerdict(False, False, None, "execution_error")

        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        trace_steps = result.get("trace", [])

        return {
            "questionId": question_id,
            "databaseId": database_id,
            "difficulty": question["difficulty"],
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
                "retryOrRepair": any(step.get("status") == "retry" for step in trace_steps),
            },
            "error": error,
            "latencyMs": latency_ms,
            "trace": trace_steps,
            "candidateSqls": candidate_sqls,
            "candidateOutcomes": candidate_outcomes,
            "candidateHit": any(item["relaxedPass"] for item in candidate_outcomes),
            "candidateStrictHit": any(item["strictPass"] for item in candidate_outcomes),
            "candidateCount": len(candidate_sqls),
        }

    def _ExecuteSql(self, sql: str, database_path: str) -> dict:
        path = Path(database_path)
        if not path.is_file():
            raise FileNotFoundError(f"SQLite database does not exist: {path}")
        cleaned = sql.strip().rstrip(";")
        if not re.search(r"\blimit\b", cleaned, re.I):
            cleaned += " LIMIT 1000"
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            connection.row_factory = sqlite3.Row
            cursor = connection.execute(cleaned)
            columns = [item[0] for item in cursor.description or []]
            rows = [dict(row) for row in cursor.fetchall()]
            return {"columns": columns, "rows": rows}
        finally:
            connection.close()

    def _BuildSummary(self, cases: list[dict], started_at: datetime, finished_at: datetime) -> dict:
        latencies = [case["latencyMs"] for case in cases]
        return {
            "total": len(cases),
            "executionAccuracy": self._Rate(cases, lambda case: case["passed"]),
            "executionAccuracyStrict": self._Rate(cases, lambda case: case["metrics"]["strictPass"]),
            "executionAccuracyRelaxed": self._Rate(cases, lambda case: case["metrics"]["relaxedPass"]),
            "validSqlRate": self._Rate(cases, lambda case: case["metrics"]["validSql"]),
            "executionSuccessRate": self._Rate(cases, lambda case: case["metrics"]["executionSucceeded"]),
            "exactMatchRate": self._Rate(cases, lambda case: case["metrics"]["exactMatch"]),
            "answerProducedRate": self._Rate(cases, lambda case: case["metrics"]["answerProduced"]),
            "retryRepairRate": self._Rate(cases, lambda case: case["metrics"]["retryOrRepair"]),
            "candidateHitRate": self._Rate(cases, lambda case: case.get("candidateHit", False)),
            "candidateStrictHitRate": self._Rate(cases, lambda case: case.get("candidateStrictHit", False)),
            "candidateSelectionLossRate": self._Rate(
                cases,
                lambda case: case.get("candidateHit", False) and not case["metrics"]["relaxedPass"],
            ),
            "avgLatencyMs": round(sum(latencies) / len(latencies), 2) if latencies else 0,
            "p95LatencyMs": self._Percentile(latencies, 95) if latencies else 0,
            "startedAt": started_at.isoformat(),
            "finishedAt": finished_at.isoformat(),
            "durationSeconds": round((finished_at - started_at).total_seconds(), 2),
        }

    def _BuildBreakdown(self, cases: list[dict], key: str) -> dict:
        groups = {}
        for case in cases:
            group_key = case.get(key, "unknown")
            if group_key not in groups:
                groups[group_key] = []
            groups[group_key].append(case)
        return {
            group_key: {
                "total": len(group_cases),
                "executionAccuracy": self._Rate(group_cases, lambda case: case["passed"]),
                "executionAccuracyStrict": self._Rate(group_cases, lambda case: case["metrics"]["strictPass"]),
                "executionAccuracyRelaxed": self._Rate(group_cases, lambda case: case["metrics"]["relaxedPass"]),
                "candidateHitRate": self._Rate(group_cases, lambda case: case.get("candidateHit", False)),
                "candidateStrictHitRate": self._Rate(group_cases, lambda case: case.get("candidateStrictHit", False)),
                "candidateSelectionLossRate": self._Rate(
                    group_cases,
                    lambda case: case.get("candidateHit", False) and not case["metrics"]["relaxedPass"],
                ),
            }
            for group_key, group_cases in groups.items()
        }

    def _Rate(self, cases: list[dict], predicate) -> float:
        if not cases:
            return 0.0
        return round(sum(1 for case in cases if predicate(case)) / len(cases), 4)

    def _Percentile(self, values: list[float], percentile: int) -> float:
        if not values:
            return 0.0
        sorted_values = sorted(values)
        index = (len(sorted_values) - 1) * percentile / 100
        if index.is_integer():
            return sorted_values[int(index)]
        lower = sorted_values[int(index)]
        upper = sorted_values[int(index) + 1]
        return lower + (upper - lower) * (index - int(index))

    def _ProcessedDataFingerprint(self) -> str:
        """生成 processed 目录内容的 SHA256 指纹"""
        import hashlib
        import os
        processed_dir = Path(self.processed_dir)
        if not processed_dir.exists():
            return ""
        hasher = hashlib.sha256()
        for path in sorted(processed_dir.rglob("*")):
            if path.is_file():
                hasher.update(path.read_bytes())
        return hasher.hexdigest()
