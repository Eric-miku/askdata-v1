"""AgentGraph — minimal NL2SQL orchestration chain. Delegates to ReActSqlAgent or falls back to a one-shot SQL pipeline with repair."""

import asyncio
from collections.abc import Mapping

from askdata.agent.prompts import BuildRepairPrompt, BuildSqlPrompt
from askdata.agent.pipeline import StagedSqlPipeline
from askdata.agent.question_analyzer import QuestionAnalysis, QuestionAnalyzer
from askdata.agent.react_sql_agent import ReActSqlAgent
from askdata.core.llm import LLMClient
from askdata.tools.analyzer import ResultAnalyzer
from askdata.tools.retriever import SemanticRetriever
from askdata.tools.skill_loader import SkillLoader
from askdata.tools.value_linker import ValueLinker


_VECTOR_FALLBACK_MESSAGE = "Semantic retrieval unavailable; lexical schema matched."


class AgentGraph:
    """Minimal real NL2SQL chain used until the LangGraph workflow is expanded."""

    def __init__(
        self,
        processed_dir=None,
        llm_client=None,
        analyzer=None,
        retriever=None,
        react_agent=None,
        skill_loader=None,
        pipeline=None,
        question_analyzer=None,
        value_linker=None,
        max_repairs: int = 1,
    ):
        self.processed_dir = processed_dir
        self.llm_client = llm_client or LLMClient()
        self.analyzer = analyzer or ResultAnalyzer()
        self.retriever = retriever
        self.react_agent = react_agent
        self.skill_loader = skill_loader or SkillLoader()
        self.pipeline = pipeline
        self.question_analyzer = question_analyzer or QuestionAnalyzer()
        self.value_linker = value_linker or ValueLinker()
        self.max_repairs = max_repairs

    def Run(self, question: str, database_id: str, session_context: dict | None = None, emit=None) -> dict:
        trace = []
        retriever = self.retriever or SemanticRetriever(processed_dir=self.processed_dir).Build()
        context = retriever.index.Retrieve(database_id, question)
        raw_analysis = self.question_analyzer.Analyze(
            question,
            context.get("schema") or {},
            str(context.get("evidence") or ""),
        )
        analysis = self._NormalizeQuestionAnalysis(raw_analysis)
        value_links = self.value_linker.Link(question, context, analysis)
        context = dict(context)
        context["analysis"] = analysis
        context["intent"] = analysis.intent
        context["value_links"] = value_links
        pipeline_session_context = self._PipelineSessionContext(
            session_context,
            analysis,
            value_links,
        )
        schema_prompt = context["schema_prompt"]
        retrieval_trace = context.get("retrieval_trace")
        has_retrieval_warning = isinstance(retrieval_trace, list) and any(
            isinstance(event, Mapping) and event.get("status") == "warning"
            for event in retrieval_trace
        )
        retrieval_event = self._TraceStep(
            "RetrieveSchema",
            "warning" if has_retrieval_warning else "success",
            _VECTOR_FALLBACK_MESSAGE if has_retrieval_warning else "Schema matched.",
        )
        trace.append(retrieval_event)
        if emit:
            emit(dict(retrieval_event))

        if self.react_agent or hasattr(self.llm_client, "Chat"):
            react_agent = self.react_agent or ReActSqlAgent(self.llm_client, skill_loader=self.skill_loader)
            pipeline = self.pipeline or StagedSqlPipeline(
                react=react_agent,
                analyzer=self.analyzer,
                retrieval_expander=lambda expanded_question, current: self._ExpandRetrieval(
                    retriever,
                    database_id,
                    expanded_question,
                    current,
                ),
            )
            result = pipeline.Run(
                question=question,
                retrieval=context,
                session_context=pipeline_session_context,
                emit=emit,
            )
            result["trace"] = trace + result.get("trace", [])
            return result

        skills_section = self.skill_loader.BuildPromptSection()
        sql = self._CleanSql(
            self.llm_client.Complete(BuildSqlPrompt(question, schema_prompt, pipeline_session_context, skills_section))
        )
        trace.append(self._TraceStep("GenerateSql", "success", sql))

        execution = self._ExecuteWithRepair(question, sql, schema_prompt, context["database_path"], trace)
        columns = execution["columns"]
        rows = execution["rows"]
        final_sql = execution["sql"]
        answer = self.analyzer.Analyze(question, final_sql, columns, rows)
        trace.append(self._TraceStep("AnalyzeResult", "success", answer[:300]))

        return {
            "answer": answer,
            "sql": final_sql,
            "columns": columns,
            "rows": rows,
            "chart": None,
            "trace": trace,
            "error": None,
        }

    async def ARun(self, question: str, database_id: str, session_context: dict | None = None, emit=None) -> dict:
        return await asyncio.to_thread(
            self.Run,
            question=question,
            database_id=database_id,
            session_context=session_context,
            emit=emit,
        )

    def _ExecuteWithRepair(self, question: str, sql: str, schema_prompt: str, database_path: str, trace: list[dict]) -> dict:
        current_sql = sql
        last_error = None
        for attempt in range(self.max_repairs + 1):
            result = self._ExecuteSql(current_sql, database_path)
            if result["success"]:
                trace.append(self._TraceStep("ValidateSql", "success", "SQL validated."))
                trace.append(self._TraceStep("ExecuteSql", "success", f"Returned {len(result['rows'])} rows."))
                return {
                    "sql": current_sql,
                    "columns": result["columns"],
                    "rows": result["rows"],
                }

            last_error = result["error"]
            step_status = "retry" if attempt < self.max_repairs else "error"
            trace.append(self._TraceStep("ValidateSql", step_status, last_error))
            if attempt >= self.max_repairs:
                break
            current_sql = self._CleanSql(
                self.llm_client.Complete(BuildRepairPrompt(question, current_sql, last_error, schema_prompt))
            )
            trace.append(self._TraceStep("RepairSql", "success", current_sql))

        raise RuntimeError(last_error or "SQL execution failed")

    def _ExecuteSql(self, sql: str, database_path: str) -> dict:
        from askdata.db.query_runner import Execute as RunQuery
        return RunQuery(sql, database_path)

    def _PipelineSessionContext(
        self,
        session_context: dict | None,
        analysis,
        value_links,
    ) -> dict:
        pipeline_session_context = dict(session_context or {})
        pipeline_session_context["analysis"] = analysis
        pipeline_session_context["value_links"] = value_links
        return pipeline_session_context

    def _NormalizeQuestionAnalysis(self, raw_analysis) -> QuestionAnalysis:
        if isinstance(raw_analysis, QuestionAnalysis):
            return raw_analysis
        if isinstance(raw_analysis, Mapping):
            return QuestionAnalysis.model_validate(raw_analysis)
        return QuestionAnalysis.model_validate({
            "intent": getattr(raw_analysis, "intent"),
            "requested_outputs": getattr(raw_analysis, "requested_outputs", []),
            "filters": getattr(raw_analysis, "filters", []),
            "formula_hints": getattr(raw_analysis, "formula_hints", []),
            "notes": getattr(raw_analysis, "notes", []),
        })

    def _ExpandRetrieval(self, retriever, database_id: str, question: str, current: dict) -> dict:
        expand = getattr(retriever.index, "Expand", None)
        if callable(expand):
            return expand(database_id, question, current)
        return retriever.index.Retrieve(database_id, question)

    def _CleanSql(self, text: str) -> str:
        cleaned = (text or "").strip().strip("`").strip()
        if cleaned.lower().startswith("sql"):
            cleaned = cleaned[3:].strip()
        return cleaned.rstrip(";")

    def _TraceStep(self, step: str, status: str, message: str) -> dict:
        return {"step": step, "status": status, "message": message}


async def RunAgent(question: str, database_id: str, session_context: dict | None = None, **kwargs) -> dict:
    return await AgentGraph(**kwargs).ARun(question=question, database_id=database_id, session_context=session_context)
