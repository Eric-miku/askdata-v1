"""Deterministic staged orchestration around focused ReAct SQL generation."""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping

from askdata.agent.ambiguity import AmbiguityGate, StructuredInterpreter
from askdata.agent.intent import IntentContract
from askdata.agent.react_sql_agent import SqlCandidateDraft
from askdata.agent.sql_quality import (
    CandidateLedger,
    EvaluateResult,
    EvaluateStaticSql,
    QualityReport,
    SqlCandidate,
)
from askdata.tools.analyzer import ResultAnalyzer
from askdata.tools.chart_builder import ChartBuilder


_STAGES = (
    "initial",
    "targeted_repair_1",
    "targeted_repair_2",
    "retrieval_expansion",
    "alternate_plan",
    "final_candidate",
)
_GROUNDING_FAILURES = {
    "unknown_table",
    "unknown_column",
    "invalid_join_using",
    "unconnected_join",
}
_ANSWER_SHAPE_FAILURES = {
    "missing_count_aggregation",
    "missing_output_attribute",
    "missing_metric",
    "missing_grouping",
    "missing_ratio_computation",
    "missing_order",
    "wrong_order_direction",
    "wrong_order_target",
    "missing_limit",
    "excessive_limit",
    "missing_filter",
    "missing_time_condition",
    "listing_returns_only_aggregates",
    "unrequested_helper_columns",
}
_SAFE_MESSAGES = {
    "RetrieveSchema": "Schema context expanded.",
    "GenerateSql": "SQL candidate generated.",
    "ValidateSql": "SQL candidate checked.",
    "ExecuteSql": "SQL candidate executed.",
    "RepairSql": "A targeted repair was requested.",
    "AnalyzeResult": "Final result analyzed.",
}


class StagedSqlPipeline:
    """Own the six-execution budget, candidate ledger, and final answer binding."""

    def __init__(
        self,
        react,
        analyzer=None,
        runner: Callable[[str, str], dict[str, Any]] | None = None,
        retrieval_expander: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
        max_executions: int = 6,
        ambiguity_gate=None,
        chart_builder=None,
    ) -> None:
        self.react = react
        self.analyzer = analyzer or ResultAnalyzer()
        self.runner = runner or self._ExecuteSql
        self.retrieval_expander = retrieval_expander
        self.max_executions = min(max(1, max_executions), 6)
        self.ambiguity_gate = ambiguity_gate
        self.chart_builder = chart_builder or ChartBuilder()

    def Run(
        self,
        context: Mapping[str, Any] | None = None,
        *,
        question: str | None = None,
        retrieval: Mapping[str, Any] | None = None,
        session_context: dict | None = None,
        emit=None,
    ) -> dict[str, Any]:
        if context is not None:
            question = question or str(context.get("question") or "")
            retrieval = retrieval or context.get("retrieval") or context
            session_context = session_context or context.get("session_context")
        question = question or ""
        current_retrieval = dict(retrieval or {})
        schema_prompt = str(current_retrieval.get("schema_prompt") or "")
        schema = current_retrieval.get("schema") or self._SchemaFromPrompt(schema_prompt)
        ambiguity_gate = self.ambiguity_gate
        if ambiguity_gate is None:
            llm_client = getattr(self.react, "llm_client", None)
            if llm_client is not None:
                ambiguity_gate = AmbiguityGate(StructuredInterpreter(llm_client))
        if ambiguity_gate is not None:
            ambiguity = ambiguity_gate.Check(
                question,
                schema,
                evidence=str(current_retrieval.get("evidence") or schema_prompt),
                session_context=session_context,
            )
            if ambiguity.state == "unanswerable":
                return {
                    "kind": "error",
                    "answer": "",
                    "sql": "",
                    "columns": [],
                    "rows": [],
                    "chart": None,
                    "trace": [],
                    "error": "unanswerable_from_schema",
                    "missing_concepts": ambiguity.missing_concepts,
                }
            if ambiguity.state == "materially_ambiguous":
                interpretations = {item.id: item for item in ambiguity.interpretations}
                options = []
                for option in ambiguity.options:
                    payload = option.model_dump(mode="json")
                    candidate = interpretations.get(option.id)
                    if candidate is not None:
                        payload["interpretation"] = candidate.model_dump(mode="json")
                    options.append(payload)
                return {
                    "kind": "clarification",
                    "question": ambiguity.question,
                    "options": options,
                    "interpretations": [
                        item.model_dump(mode="json") for item in ambiguity.interpretations
                    ],
                    "trace": [],
                    "error": None,
                }
            question = ambiguity.resolved_question or question
        analysis = current_retrieval.get("analysis")
        analysis_intent = None
        if analysis is not None:
            if hasattr(analysis, "intent"):
                analysis_intent = analysis.intent
            elif isinstance(analysis, Mapping):
                analysis_intent = analysis.get("intent")
        intent = current_retrieval.get("intent") or analysis_intent or self._InferIntent(question, schema)
        if not isinstance(intent, IntentContract):
            intent = IntentContract.model_validate(intent)
        database_path = str(current_retrieval.get("database_path") or "")

        trace: list[dict[str, str]] = []
        ledger = CandidateLedger()
        attempts: list[dict[str, Any]] = []
        seen_sql: set[str] = set()
        executions = 0
        last_failure: str | None = None
        last_candidate_sql = ""
        grounding_failure_seen = False
        expanded = False
        previous_failure: str | None = None
        previous_progress = -1.0
        new_state = getattr(self.react, "NewCandidateState", None)
        generation_state = (
            new_state(question, schema_prompt, session_context)
            if callable(new_state)
            else None
        )

        for stage_index, stage in enumerate(_STAGES):
            if executions >= self.max_executions:
                break
            if stage == "retrieval_expansion":
                if not grounding_failure_seen:
                    continue
                if self.retrieval_expander:
                    expanded_retrieval = self.retrieval_expander(question, current_retrieval)
                    if expanded_retrieval:
                        current_retrieval = dict(expanded_retrieval)
                        schema_prompt = str(
                            current_retrieval.get("schema_prompt") or schema_prompt
                        )
                        schema = current_retrieval.get("schema") or self._SchemaFromPrompt(
                            schema_prompt
                        )
                    expanded = True
                    self._Emit(trace, emit, "RetrieveSchema", "retry")
                    record_context = getattr(self.react, "RecordRetrievalContext", None)
                    if generation_state is not None and callable(record_context):
                        record_context(generation_state, schema_prompt)
            if stage == "alternate_plan" and last_failure not in {
                "empty_or_suspicious",
                "answer_shape",
            }:
                continue

            generation_context = dict(session_context or {})
            generation_context.update(
                {
                    "pipeline_stage": stage,
                    "pipeline_feedback": self._Feedback(last_failure),
                    "pipeline_previous_sql": last_candidate_sql,
                }
            )
            if generation_state is not None:
                drafts = self.react.GenerateCandidates(
                    question,
                    schema_prompt,
                    generation_context,
                    state=generation_state,
                )
            else:
                drafts = self.react.GenerateCandidates(
                    question, schema_prompt, generation_context
                )
            drafts = list(drafts or [])
            for ignored in drafts[1:]:
                ignored_draft = (
                    ignored
                    if isinstance(ignored, SqlCandidateDraft)
                    else SqlCandidateDraft.model_validate(ignored)
                )
                self._RecordFeedback(
                    generation_state,
                    ignored_draft,
                    success=False,
                    failure_class="not_selected",
                    error="candidate_not_selected_for_this_stage",
                )
            for raw_draft in drafts[:1]:
                if executions >= self.max_executions:
                    break
                draft = (
                    raw_draft
                    if isinstance(raw_draft, SqlCandidateDraft)
                    else SqlCandidateDraft.model_validate(raw_draft)
                )
                sql = draft.sql.strip().rstrip(";")
                last_candidate_sql = sql
                self._Emit(trace, emit, "GenerateSql", "success")
                normalized_key = re.sub(r"\s+", " ", sql).casefold()
                if normalized_key in seen_sql:
                    last_failure = "repeated_no_progress"
                    self._Emit(
                        trace,
                        emit,
                        "RepairSql",
                        "warning",
                        failure_class="repeated_no_progress",
                    )
                    return self._Finish(
                        question,
                        ledger,
                        attempts,
                        trace,
                        last_failure,
                        executions,
                        expanded,
                        emit,
                        intent,
                    )
                seen_sql.add(normalized_key)

                static_report = EvaluateStaticSql(intent, sql, schema, question=question)
                self._Emit(trace, emit, "ValidateSql", "success" if static_report.passed else "retry")
                static_class = self._ClassifyStatic(static_report)
                if static_class == "syntax_or_safety":
                    ledger.Add(
                        SqlCandidate(
                            sql=sql,
                            referenced_context=draft.referenced_context,
                            static_report=static_report,
                            execution_error="static_validation_failed",
                            directness=static_report.directness,
                            sequence=len(attempts),
                        )
                    )
                    attempts.append(self._Attempt(sql, static_class, static_report, None, False))
                    last_failure = static_class
                    self._RecordFeedback(
                        generation_state,
                        draft,
                        success=False,
                        failure_class=static_class,
                        error=",".join(static_report.failures),
                    )
                    self._Emit(trace, emit, "RepairSql", "retry")
                    progress = self._Progress(static_report, None)
                    if self._RepeatedWithoutProgress(
                        previous_failure, previous_progress, static_class, progress
                    ):
                        last_failure = "repeated_no_progress"
                        self._Emit(
                            trace,
                            emit,
                            "RepairSql",
                            "warning",
                            failure_class="repeated_no_progress",
                        )
                        return self._Finish(
                            question,
                            ledger,
                            attempts,
                            trace,
                            last_failure,
                            executions,
                            expanded,
                            emit,
                            intent,
                        )
                    previous_failure, previous_progress = static_class, progress
                    continue

                execution = self.runner(sql, database_path)
                executions += 1
                if not execution.get("success"):
                    failure_class = self._ClassifyExecution(execution.get("error"), static_class)
                    grounding_failure_seen = grounding_failure_seen or failure_class == "schema_grounding"
                    ledger.Add(
                        SqlCandidate(
                            sql=sql,
                            referenced_context=draft.referenced_context,
                            static_report=static_report,
                            execution_error=str(execution.get("error") or "execution_failed"),
                            directness=static_report.directness,
                            sequence=len(attempts),
                        )
                    )
                    attempts.append(self._Attempt(sql, failure_class, static_report, None, True))
                    last_failure = failure_class
                    self._RecordFeedback(
                        generation_state,
                        draft,
                        success=False,
                        failure_class=failure_class,
                        error=str(execution.get("error") or "execution_failed"),
                    )
                    self._Emit(trace, emit, "ExecuteSql", "retry")
                    self._Emit(trace, emit, "RepairSql", "retry")
                    progress = self._Progress(static_report, None)
                    if self._RepeatedWithoutProgress(
                        previous_failure, previous_progress, failure_class, progress
                    ):
                        last_failure = "repeated_no_progress"
                        self._Emit(
                            trace,
                            emit,
                            "RepairSql",
                            "warning",
                            failure_class="repeated_no_progress",
                        )
                        return self._Finish(
                            question,
                            ledger,
                            attempts,
                            trace,
                            last_failure,
                            executions,
                            expanded,
                            emit,
                            intent,
                        )
                    previous_failure, previous_progress = failure_class, progress
                    continue

                columns = list(execution.get("columns") or [])
                rows = list(execution.get("rows") or [])
                result_report = EvaluateResult(intent, columns, rows, static_report=static_report)
                failure_class = static_class or self._ClassifyResult(result_report)
                candidate = SqlCandidate(
                    sql=sql,
                    columns=columns,
                    rows=rows,
                    referenced_context=draft.referenced_context,
                    static_report=static_report,
                    result_report=result_report,
                    directness=static_report.directness,
                    sequence=len(attempts),
                )
                ledger.Add(candidate)
                attempts.append(self._Attempt(sql, failure_class, static_report, result_report, True))
                last_failure = failure_class
                grounding_failure_seen = grounding_failure_seen or failure_class == "schema_grounding"
                self._RecordFeedback(
                    generation_state,
                    draft,
                    success=True,
                    failure_class=failure_class,
                    error=",".join(result_report.failures),
                    columns=columns,
                    rows=rows,
                )
                self._Emit(trace, emit, "ExecuteSql", "success" if failure_class is None else "retry")
                if failure_class is None:
                    return self._Finish(
                        question,
                        ledger,
                        attempts,
                        trace,
                        None,
                        executions,
                        expanded,
                        emit,
                        intent,
                    )
                self._Emit(trace, emit, "RepairSql", "retry")
                progress = self._Progress(static_report, result_report)
                if self._RepeatedWithoutProgress(
                    previous_failure, previous_progress, failure_class, progress
                ):
                    last_failure = "repeated_no_progress"
                    self._Emit(
                        trace,
                        emit,
                        "RepairSql",
                        "warning",
                        failure_class="repeated_no_progress",
                    )
                    return self._Finish(
                        question,
                        ledger,
                        attempts,
                        trace,
                        last_failure,
                        executions,
                        expanded,
                        emit,
                        intent,
                    )
                previous_failure, previous_progress = failure_class, progress

            if not drafts and stage_index < len(_STAGES) - 1:
                self._Emit(trace, emit, "RepairSql", "retry")

        return self._Finish(
            question,
            ledger,
            attempts,
            trace,
            last_failure,
            executions,
            expanded,
            emit,
            intent,
        )

    def _Finish(
        self,
        question,
        ledger,
        attempts,
        trace,
        failure_class,
        executions,
        expanded,
        emit,
        intent,
    ):
        verified_ledger = CandidateLedger()
        for candidate in ledger.candidates:
            if (
                candidate.execution_error is None
                and candidate.static_report.passed
                and candidate.result_report is not None
                and candidate.result_report.passed
            ):
                verified_ledger.Add(candidate)
        selected = verified_ledger.SelectBest()
        ledger_summary = [
            {
                "sequence": index,
                "executed": attempt["executed"],
                "failure_class": attempt["failure_class"],
            }
            for index, attempt in enumerate(attempts)
        ]
        if selected is None:
            return {
                "kind": "error",
                "answer": "",
                "sql": "",
                "columns": [],
                "rows": [],
                "chart": None,
                "trace": trace,
                "error": "no_trustworthy_candidate",
                "failure_class": failure_class,
                "executions": executions,
                "retrieval_expanded": expanded,
                "ledger": ledger_summary,
            }

        answer = self.analyzer.Analyze(
            question, selected.sql, selected.columns, selected.rows
        )
        chart = self.chart_builder.Build(
            question, intent, selected.columns, selected.rows
        )
        self._Emit(trace, emit, "AnalyzeResult", "success")
        verified = bool(
            selected.static_report.passed
            and selected.result_report is not None
            and selected.result_report.passed
        )
        return {
            "kind": "answer",
            "answer": answer,
            "sql": selected.sql,
            "columns": selected.columns,
            "rows": selected.rows,
            "chart": chart,
            "trace": trace,
            "error": None,
            "confidence": "high" if verified else "low",
            "executions": executions,
            "retrieval_expanded": expanded,
            "ledger": ledger_summary,
        }

    @staticmethod
    def _Attempt(sql, failure_class, static_report, result_report, executed):
        return {
            "sql": sql,
            "failure_class": failure_class,
            "static_report": static_report,
            "result_report": result_report,
            "executed": executed,
        }

    @staticmethod
    def _ClassifyStatic(report: QualityReport) -> str | None:
        failures = set(report.failures)
        if failures & {"invalid_sql", "unsafe_sql"}:
            return "syntax_or_safety"
        if failures & _GROUNDING_FAILURES:
            return "schema_grounding"
        if failures & _ANSWER_SHAPE_FAILURES or failures:
            return "answer_shape"
        return None

    @staticmethod
    def _ClassifyExecution(error: Any, static_class: str | None) -> str:
        if static_class == "schema_grounding":
            return static_class
        lowered = str(error or "").casefold()
        if any(marker in lowered for marker in ("no such table", "no such column", "ambiguous column")):
            return "schema_grounding"
        return "syntax_or_safety"

    @staticmethod
    def _ClassifyResult(report: QualityReport) -> str | None:
        return "empty_or_suspicious" if report.failures else None

    @staticmethod
    def _Progress(
        static_report: QualityReport,
        result_report: QualityReport | None,
    ) -> float:
        if result_report is None:
            return static_report.coverage
        return (static_report.coverage + result_report.coverage) / 2

    @staticmethod
    def _RepeatedWithoutProgress(
        previous_failure: str | None,
        previous_progress: float,
        failure_class: str | None,
        progress: float,
    ) -> bool:
        if failure_class == "answer_shape":
            return False
        return bool(
            failure_class
            and failure_class == previous_failure
            and progress <= previous_progress
        )

    def _RecordFeedback(
        self,
        state,
        draft: SqlCandidateDraft,
        *,
        success: bool,
        failure_class: str | None,
        error: str = "",
        columns: list[str] | None = None,
        rows: list[dict] | None = None,
    ) -> None:
        record = getattr(self.react, "RecordExecutionFeedback", None)
        if state is None or not callable(record):
            return
        record(
            state,
            draft,
            {
                "success": success,
                "failure_class": failure_class,
                "error": error[:300],
                "columns": columns or [],
                "rows": rows or [],
            },
        )

    @staticmethod
    def _Feedback(failure_class: str | None) -> str:
        return {
            "syntax_or_safety": "Correct SQL syntax or safety validation.",
            "schema_grounding": "Use only tables and columns present in the schema context.",
            "answer_shape": (
                "The previous SQL did not directly answer the question. Do not run "
                "inspection queries. Produce one final SQL whose SELECT columns, filters, "
                "aggregation, order, and limit match the question. For rate, ratio, "
                "percentage, average, or decrease/increase questions, SELECT the final "
                "computed expression itself, not intermediate components."
            ),
            "empty_or_suspicious": "Try a direct alternate plan and verify the expected result shape.",
            "repeated_no_progress": "Do not repeat the previous SQL.",
        }.get(failure_class, "")

    @staticmethod
    def _SchemaFromPrompt(schema_prompt: str) -> dict[str, list[str]]:
        schema: dict[str, list[str]] = {}
        for table, columns_text in re.findall(
            r"^Table\s+([^\s(]+)\((.*)\)$", schema_prompt, re.M
        ):
            columns = []
            for item in columns_text.split(","):
                name = item.strip().split(" ", 1)[0]
                if name:
                    columns.append(name)
            schema[table] = columns
        return schema

    @staticmethod
    def _InferIntent(question: str, schema: Mapping[str, list[str]]) -> IntentContract:
        lowered = question.casefold()
        all_columns = [column for columns in schema.values() for column in columns]
        question_concepts = {
            StagedSqlPipeline._NormalizeConcept(token)
            for token in re.findall(r"[a-z0-9]+", lowered)
        }
        outputs = [
            column
            for column in all_columns
            if (
                re.search(rf"\b{re.escape(column.casefold())}\b", lowered)
                or StagedSqlPipeline._NormalizeConcept(column) in question_concepts
            )
        ]
        if re.search(r"\b(percentage|percent|ratio|rate|share)\b", lowered):
            return IntentContract(shape="ratio", metrics=["ratio"], expected_max_rows=1)
        if re.search(r"\b(how many|number of|count)\b", lowered):
            return IntentContract(shape="scalar", metrics=["count"], expected_max_rows=1)
        if re.search(r"\b(average|mean|avg)\b", lowered):
            return IntentContract(
                shape="scalar",
                metrics=outputs[-1:] or ["average"],
                expected_max_rows=1,
            )
        if re.search(r"\b(top|bottom|highest|lowest|most|least|rank)\b", lowered):
            order = "ascending" if re.search(r"\b(bottom|lowest|least)\b", lowered) else "descending"
            return IntentContract(shape="ranking", order=order, output_attributes=outputs)
        return IntentContract(shape="listing", output_attributes=outputs)

    @classmethod
    def _NormalizeConcept(cls, value: str) -> str:
        tokens = re.findall(r"[a-z0-9]+", value.casefold())
        word = "".join(tokens)
        if len(word) <= 3:
            return word
        stemmed = re.sub(
            r"(ies|sses|ses|ches|shes|xes|zes|ves|uses|s|ing|ed|er|est|ness|ly)$",
            "",
            word,
        )
        return stemmed if len(stemmed) >= 3 else word

    @staticmethod
    def _Emit(
        trace,
        emit,
        step: str,
        status: str,
        *,
        failure_class: str | None = None,
    ) -> None:
        event = {"step": step, "status": status, "message": _SAFE_MESSAGES[step]}
        if failure_class:
            event["failure_class"] = failure_class
        trace.append(event)
        if emit:
            emit(dict(event))

    @staticmethod
    def _ExecuteSql(sql: str, database_path: str) -> dict[str, Any]:
        from askdata.db.query_runner import Execute

        return Execute(sql, database_path)
