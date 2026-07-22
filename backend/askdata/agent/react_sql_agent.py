"""ReAct tool-calling SQL agent loop — LLM reasons, calls run_query, self-corrects on errors, and produces a final answer."""

import json
import re
from typing import Any

from askdata.agent.answer_shape import CheckAnswerShape
from askdata.agent.prompts import BuildReActSystemPrompt

RUN_QUERY_TOOL = {
    "type": "function",
    "function": {
        "name": "run_query",
        "description": "Execute a SQLite SELECT query against the selected database. Returns columns and a sample of rows, or an error message that should be used to repair the SQL. The sample may be truncated; do not use OFFSET pagination to collect full result sets. Keep the SQL that directly answers the question.",
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "The SQLite SELECT query to execute."},
            },
            "required": ["sql"],
        },
    },
}


class ReActSqlAgent:
    """Tool-calling SQL loop that can be used as one node inside AgentGraph."""

    def __init__(self, llm_client, max_iterations: int = 8, skill_loader=None):
        self.llm_client = llm_client
        self.max_iterations = max_iterations
        self.skill_loader = skill_loader

    def Run(self, question: str, schema_prompt: str, database_path: str, session_context: dict | None = None) -> dict:
        messages = self._BuildMessages(question, schema_prompt, session_context)
        trace = []
        last_sql = ""
        last_columns = []
        last_rows = []
        candidates = []
        candidate_sequence = 0
        answer = ""
        review_requested = False

        for iteration in range(self.max_iterations):
            message = self.llm_client.Chat(messages, tools=[RUN_QUERY_TOOL])
            content = getattr(message, "content", None)
            if content:
                trace.append(self._TraceStep(f"Reason-{iteration + 1}", "success", content[:300]))

            tool_calls = getattr(message, "tool_calls", None) or []
            if not tool_calls:
                latest_warnings = candidates[-1].get("shape_warnings", []) if candidates else []
                if latest_warnings and len(candidates) < 2 and not review_requested:
                    messages.append({"role": "assistant", "content": content or ""})
                    messages.append({
                        "role": "user",
                        "content": (
                            "Before finalizing, produce and run one corrected SQL candidate that resolves "
                            "these answer-shape warnings: " + "; ".join(latest_warnings)
                        ),
                    })
                    review_requested = True
                    trace.append(self._TraceStep("ReviewAnswerShape", "retry", "; ".join(latest_warnings)))
                    continue
                answer = self._CleanFinalAnswer(content or "")
                break

            messages.append(self._AssistantMessage(message))
            for tool_call in tool_calls:
                if tool_call.function.name != "run_query":
                    messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": f"Error: Unknown tool {tool_call.function.name}"})
                    continue

                sql = self._CleanSql(self._ParseSql(tool_call.function.arguments))
                trace.append(self._TraceStep("GenerateSql", "success", sql))
                result = self._ExecuteSql(sql, database_path)
                if result["success"]:
                    shape_warnings = CheckAnswerShape(question, sql)
                    last_sql = sql
                    last_columns = result["columns"]
                    last_rows = result["rows"]
                    candidates.append({
                        "sql": sql,
                        "columns": last_columns,
                        "rows": last_rows,
                        "shape_warnings": shape_warnings,
                        "sequence": candidate_sequence,
                    })
                    candidate_sequence += 1
                    # ReAct commonly uses successful queries to inspect categorical values
                    # before producing its final SQL. Keep the candidate set bounded without
                    # preventing that later, directly answering query from being executed.
                    candidates = candidates[-2:]
                    trace.append(self._TraceStep("ExecuteSql", "success", f"Returned {len(last_rows)} rows."))
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(
                            {
                                "columns": last_columns,
                                "rowCount": len(last_rows),
                                "rows": last_rows[:20],
                                "shapeWarnings": shape_warnings,
                                "reviewRequired": bool(shape_warnings),
                                "note": "Rows are a sample for inspection. Do not paginate with OFFSET to collect all rows; keep the SQL that directly answers the question.",
                            },
                            ensure_ascii=False,
                            default=str,
                        ),
                    })
                else:
                    trace.append(self._TraceStep("ExecuteSql", "retry", result["error"]))
                    messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": f"Error: {result['error']}"})
        else:
            answer = self._FallbackAnswer(last_columns, last_rows) if last_sql else "Unable to answer within the available steps."

        if not answer:
            answer = self._FallbackAnswer(last_columns, last_rows)

        selected = self._SelectBestCandidate(question, candidates)
        if selected:
            last_sql = selected["sql"]
            last_columns = selected["columns"]
            last_rows = selected["rows"]
            if len(candidates) > 1:
                reason = selected.get("selection_reason", "deterministic candidate ranking")
                trace.append(
                    self._TraceStep(
                        "SelectBestCandidate",
                        "success",
                        f"Selected candidate {selected.get('candidate_index', 0) + 1} of {len(candidates)}: {reason}",
                    )
                )

        return {
            "answer": answer,
            "sql": last_sql,
            "columns": last_columns,
            "rows": last_rows,
            "trace": trace,
            "candidates": candidates,
        }

    def _BuildMessages(self, question: str, schema_prompt: str, session_context: dict | None) -> list[dict]:
        previous = ""
        if session_context and session_context.get("last_sql"):
            previous = f"\nPrevious SQL: {session_context['last_sql']}"
        if session_context and session_context.get("understanding"):
            previous += "\nStructured intent: " + json.dumps(session_context["understanding"], ensure_ascii=False)
        system_prompt = BuildReActSystemPrompt()

        if self.skill_loader:
            skills = self.skill_loader.BuildPromptSection()
            if skills:
                system_prompt += "\n\n" + skills

        user_prompt = f"Question: {question}{previous}\n\nDatabase Schema:\n{schema_prompt}"
        return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

    def _ExecuteSql(self, sql: str, database_path: str) -> dict:
        from askdata.db.query_runner import Execute as RunQuery
        return RunQuery(sql, database_path)

    def _SelectBestCandidate(self, question: str, candidates: list[dict]) -> dict | None:
        if not candidates:
            return None
        if len(candidates) == 1:
            selected = candidates[0].copy()
            selected["candidate_index"] = 0
            selected["selection_reason"] = "only successful candidate"
            return selected

        judged = self._JudgeCandidates(question, candidates)
        if judged is not None:
            return judged

        selected_index, selected = max(
            enumerate(candidates),
            key=lambda item: (
                not item[1].get("shape_warnings"),
                self._IntentScore(question, item[1]),
                item[1].get("sequence", 0),
            ),
        )
        result = selected.copy()
        result["candidate_index"] = selected_index
        result["selection_reason"] = "LLM judge unavailable; selected by answer-shape and intent score"
        return result

    def _JudgeCandidates(self, question: str, candidates: list[dict[str, Any]]) -> dict | None:
        """Ask an LLM to compare executable candidates without exposing full results.

        Candidate execution is already the admission criterion.  The judge only
        ranks those candidates by semantic fit; a malformed or unavailable
        judge response never changes the deterministic fallback behaviour.
        """
        summaries = []
        for index, candidate in enumerate(candidates, start=1):
            preview = candidate.get("rows", [])[:5]
            summaries.append(
                "\n".join(
                    [
                        f"Candidate {index}",
                        f"SQL: {candidate.get('sql', '')}",
                        f"Columns: {json.dumps(candidate.get('columns', []), ensure_ascii=False)}",
                        f"Rows: {len(candidate.get('rows', []))}",
                        f"Preview: {json.dumps(preview, ensure_ascii=False, default=str)[:1200]}",
                        f"Answer-shape warnings: {json.dumps(candidate.get('shape_warnings', []), ensure_ascii=False)}",
                    ]
                )
            )
        prompt = "\n\n".join(
            [
                "You are an NL2SQL result judge. Select the candidate that directly and completely answers the user question.",
                "Prefer the exact requested result shape. Do not reward exploratory SQL, extra columns, or a result that merely looks plausible.",
                f"Question: {question}",
                "Candidates:",
                "\n\n".join(summaries),
                'Return JSON only: {"best_index": 1, "score": 0, "reason": "short reason"}.',
            ]
        )
        try:
            response = self.llm_client.Chat([{"role": "user", "content": prompt}])
            content = (getattr(response, "content", "") or "").strip()
            match = re.search(r"\{[\s\S]*\}", content)
            if not match:
                return None
            verdict = json.loads(match.group(0))
            selected_index = int(verdict.get("best_index", 0)) - 1
            if not 0 <= selected_index < len(candidates):
                return None
            selected = candidates[selected_index].copy()
            selected["candidate_index"] = selected_index
            selected["judge_score"] = verdict.get("score")
            selected["selection_reason"] = str(verdict.get("reason") or "LLM-as-a-Judge selection")
            return selected
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError, RuntimeError, IndexError):
            return None

    def CalcStateValue(
        self,
        state: dict[str, Any],
        immediate_reward: float,
        gamma: float = 0.95,
        next_state_value: float | None = None,
    ) -> float:
        """Extension point for future value-model based candidate selection.

        The default is deliberately deterministic: without a trained value
        model, the only defensible expectation is the observed reward.  A
        caller may supply an estimated next-state value when such a model is
        introduced, yielding the standard one-step Bellman estimate.
        """
        if not 0 <= gamma <= 1:
            raise ValueError("gamma must be between 0 and 1")
        del state
        return float(immediate_reward) + gamma * float(next_state_value or 0.0)

    def _IntentScore(self, question: str, candidate: dict) -> int:
        question_text = (question or "").lower()
        sql = (candidate.get("sql") or "").lower()
        columns = candidate.get("columns") or []
        score = 0

        asks_count = bool(re.search(r"\b(how many|number of|count|no\.)\b", question_text))
        asks_list = bool(re.search(r"\b(list|name|names|show|give|which|what are)\b", question_text))
        asks_average = bool(re.search(r"\b(avg|average|mean)\b", question_text))
        asks_rank = "rank" in question_text
        asks_extreme = bool(re.search(r"\b(top|bottom|highest|lowest|most|least|best|worst)\b", question_text))
        is_count_sql = bool(re.search(r"\bcount\s*\(", sql))
        # COUNT used in ORDER BY/GROUP BY is often the correct way to answer
        # an entity question such as "which team won the most".  It is a
        # count-only result only when COUNT appears in the SELECT expression.
        is_count_only = bool(
            re.search(r"^\s*select\s+(?:distinct\s+)?count\s*\(", sql)
        ) and len(columns) == 1
        is_avg_sql = bool(re.search(r"\b(avg|average)\s*\(", sql))

        if asks_average:
            score += 7 if is_avg_sql else -5
            if is_count_only:
                score -= 5
        elif asks_count:
            score += 6 if is_count_sql else -6
            if len(columns) == 1:
                score += 2
        if asks_list and not asks_count:
            score += -6 if is_count_only else 3
        if asks_rank:
            score += 5 if re.search(r"\b(rank|dense_rank|row_number)\s*\(", sql) else -4
        if asks_extreme:
            score += 3 if re.search(r"\border\s+by\b", sql) else -3
            score += 2 if re.search(r"\blimit\s+1\b", sql) else 0
            if is_count_sql and len(columns) > 1:
                score += 2
        if re.search(r"\boffset\b", sql):
            score -= 4
        if re.search(r"\blimit\s+([2-9]\d{2,}|\d{4,})\b", sql):
            score -= 3
        return score

    def _AssistantMessage(self, message) -> dict:
        return {
            "role": "assistant",
            "content": getattr(message, "content", None),
            "tool_calls": [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    },
                }
                for tool_call in (getattr(message, "tool_calls", None) or [])
            ],
        }

    def _ParseSql(self, arguments: str) -> str:
        try:
            return json.loads(arguments).get("sql", "")
        except json.JSONDecodeError:
            return ""

    def _CleanSql(self, text: str) -> str:
        cleaned = (text or "").strip().strip("`").strip()
        if cleaned.lower().startswith("sql"):
            cleaned = cleaned[3:].strip()
        return cleaned.rstrip(";")

    def _CleanFinalAnswer(self, answer: str) -> str:
        cleaned = (answer or "").strip()
        answer_match = re.search(r"(?:\*\*)?answer\s*:(?:\*\*)?\s*(.+)\Z", cleaned, re.I | re.S)
        return answer_match.group(1).strip() if answer_match else cleaned

    def _FallbackAnswer(self, columns: list[str], rows: list[dict]) -> str:
        if not rows:
            return "查询没有返回结果。"
        if len(rows) == 1 and len(columns) == 1:
            return f"查询结果是 {rows[0].get(columns[0])}。"
        return f"查询返回 {len(rows)} 行结果。"

    def _TraceStep(self, step: str, status: str, message: str) -> dict:
        return {"step": step, "status": status, "message": message}
