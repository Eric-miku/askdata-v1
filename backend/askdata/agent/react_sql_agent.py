"""ReAct tool-calling SQL agent loop — LLM reasons, calls run_query, self-corrects on errors, and produces a final answer."""

import json
import re
from typing import List, Dict, Any

from askdata.db.executor import SQLExecutor

RUN_QUERY_TOOL = {
    "type": "function",
    "function": {
        "name": "run_query",
        "description": "Execute a SQLite SELECT query against the selected database. Returns columns and rows, or an error message that should be used to repair the SQL.",
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "The SQLite SELECT query to execute.",
                },
            },
            "required": ["sql"],
        },
    },
}


class ReActSqlAgent:
    """Tool-calling SQL loop that can be used as one node inside AgentGraph."""

    def __init__(self, llm_client, max_iterations: int = 6):
        self.llm_client = llm_client
        self.max_iterations = max_iterations
        self._candidates: List[Dict[str, Any]] = []

    def Run(
        self,
        question: str,
        schema_prompt: str,
        database_path: str,
        session_context: dict | None = None,
    ) -> dict:
        messages = self._BuildMessages(question, schema_prompt, session_context)
        trace = []
        last_sql = ""
        last_columns = []
        last_rows = []
        answer = ""
        self._candidates = []

        for iteration in range(self.max_iterations):
            message = self.llm_client.Chat(messages, tools=[RUN_QUERY_TOOL])
            content = getattr(message, "content", None)
            if content:
                trace.append(
                    self._TraceStep(f"Reason-{iteration + 1}", "success", content[:300])
                )

            tool_calls = getattr(message, "tool_calls", None) or []
            if not tool_calls:
                answer = self._CleanFinalAnswer(content or "")
                break

            messages.append(self._AssistantMessage(message))
            for tool_call in tool_calls:
                if tool_call.function.name != "run_query":
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": f"Error: Unknown tool {tool_call.function.name}",
                        }
                    )
                    continue

                sql = self._CleanSql(self._ParseSql(tool_call.function.arguments))
                trace.append(self._TraceStep("GenerateSql", "success", sql))
                result = self._ExecuteSql(sql, database_path)
                if result["success"]:
                    last_sql = sql
                    last_columns = result["columns"]
                    last_rows = result["rows"]
                    self._candidates.append(
                        {
                            "sql": sql,
                            "columns": result["columns"],
                            "rows": result["rows"],
                            "iteration": iteration + 1,
                        }
                    )
                    trace.append(
                        self._TraceStep(
                            "ExecuteSql", "success", f"Returned {len(last_rows)} rows."
                        )
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps(
                                {
                                    "columns": last_columns,
                                    "rowCount": len(last_rows),
                                    "rows": last_rows[:20],
                                },
                                ensure_ascii=False,
                                default=str,
                            ),
                        }
                    )
                else:
                    trace.append(
                        self._TraceStep("ExecuteSql", "retry", result["error"])
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": f"Error: {result['error']}",
                        }
                    )
        else:
            answer = (
                self._FallbackAnswer(last_columns, last_rows)
                if last_sql
                else "Unable to answer within the available steps."
            )

        if len(self._candidates) > 1:
            best_candidate = self._SelectBestCandidate(question, self._candidates)
            last_sql = best_candidate["sql"]
            last_columns = best_candidate["columns"]
            last_rows = best_candidate["rows"]
            trace.append(
                self._TraceStep(
                    "SelectBestCandidate",
                    "success",
                    f"Selected candidate {best_candidate.get('index', 0) + 1} out of {len(self._candidates)}",
                )
            )

        if not answer:
            answer = self._FallbackAnswer(last_columns, last_rows)

        return {
            "answer": answer,
            "sql": last_sql,
            "columns": last_columns,
            "rows": last_rows,
            "trace": trace,
        }

    def _BuildMessages(
        self, question: str, schema_prompt: str, session_context: dict | None
    ) -> list[dict]:
        previous = ""
        if session_context and session_context.get("last_sql"):
            previous = f"\nPrevious SQL: {session_context['last_sql']}"
        system_prompt = """You are a SQLite data analyst. Given a question and a database schema, write and execute SQL queries to answer the question.

HOW TO WORK (follow this sequence for every question):
1. Read the question. Identify exactly what columns the answer requires.
2. Check the schema: which table has each required column? If filter columns and target columns are in DIFFERENT tables, you MUST JOIN those tables.
3. Before writing SQL, verify: does my SELECT list match exactly what the question asks for? No extra columns.
4. Write and execute the SQL via the run_query tool.
5. If the query fails or returns wrong results, read the error, fix the SQL, and retry.
6. When satisfied, state ONLY the final answer based on the SQL results.

COLUMN SELECTION (strict — every SELECT is checked):
- "What is the phone number of X?" -> SELECT phone ONLY, not phone + school + score.
- "List the schools and their writing scores" -> SELECT school, score ONLY.
- "How many schools in each county?" -> SELECT county, COUNT(*) ONLY.
- NEVER add extra columns "for context". If the question asks for Phone, SELECT Phone.
- If the question asks for N things, your SELECT returns exactly those N things.

PRE-AGGREGATED COLUMNS (do NOT double-aggregate):
- If a column name contains Avg, Average, Rate, Percent, Pct, Total, Sum, Ratio, or Score: it is already a computed metric per row. Do NOT wrap it in AVG(), SUM(), or other aggregate functions.
- Example: column "AvgScrWrite" means "average writing score per school". Use it directly: SELECT AvgScrWrite — never AVG(AvgScrWrite).
- Only use aggregate functions (AVG, SUM, COUNT, MIN, MAX) on raw atomic columns, not on pre-computed metrics.

JOIN (mandatory when data spans tables):
- Before you skip a JOIN, ask yourself: does my WHERE column come from a different table than my SELECT column? If yes -> JOIN them.

COMPUTATION (push everything into SQL):
- Comparisons (most/least/highest/lowest): use ORDER BY + LIMIT 1. Never fetch multiple rows and pick yourself.
- Ratios, percentages, averages, differences: compute in the SELECT expressions. Never fetch two numbers and divide in your head.
- Conditional counts: use SUM(CASE WHEN ... THEN 1 ELSE 0 END). Never count rows manually.

ANSWER (final output rules):
- Your answer MUST contain ONLY information present in the SQL results. Never invent numbers, names, or facts.
- Do not include your reasoning, doubts, or chain-of-thought in the final answer.
- Keep answers concise — one or two sentences.
- If data is insufficient, say so."""

        user_prompt = (
            f"Question: {question}{previous}\n\nDatabase Schema:\n{schema_prompt}"
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def _ExecuteSql(self, sql: str, database_path: str) -> dict:
        try:
            result = SQLExecutor(
                f"sqlite:///{database_path}", dialect="sqlite"
            ).execute(sql)
            if not result.success:
                error = (
                    result.error.to_dict()
                    if result.error
                    else {"message": "SQL execution failed"}
                )
                return {
                    "success": False,
                    "error": error.get("detail") or error.get("message"),
                }
            return {
                "success": True,
                "columns": [column.key for column in result.columns],
                "rows": result.rows,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

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
        answer_match = re.search(
            r"(?:\*\*)?answer\s*:(?:\*\*)?\s*(.+)\Z", cleaned, re.I | re.S
        )
        return answer_match.group(1).strip() if answer_match else cleaned

    def _FallbackAnswer(self, columns: list[str], rows: list[dict]) -> str:
        if not rows:
            return "查询没有返回结果。"
        if len(rows) == 1 and len(columns) == 1:
            return f"查询结果是 {rows[0].get(columns[0])}。"
        return f"查询返回 {len(rows)} 行结果。"

    def _SelectBestCandidate(
        self, question: str, candidates: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        if len(candidates) == 1:
            return candidates[0]

        candidates_text = ""
        for idx, candidate in enumerate(candidates):
            rows_preview = (
                candidate["rows"][:5]
                if len(candidate["rows"]) > 5
                else candidate["rows"]
            )
            candidates_text += f"""候选 {idx + 1}:
SQL: {candidate["sql"]}
列: {candidate["columns"]}
行数: {len(candidate["rows"])}
部分结果: {json.dumps(rows_preview, ensure_ascii=False, default=str)[:200]}

"""

        judge_prompt = f"""你是一个 SQL 查询结果评估专家。请根据用户问题，对比以下多个成功执行的 SQL 查询，选出最能准确回答用户问题的最优候选。

评估标准：
1. 查询结果是否直接回答了用户的问题（正确性）
2. 返回的列是否完全匹配用户需求（完整性）
3. 返回行数是否合理（过多或过少可能表示逻辑问题）
4. SQL 逻辑是否简洁高效（优雅性）

用户问题：{question}

候选列表：
{candidates_text}

请直接返回 JSON 格式的评估结果，不要包含其他解释：
{{
    "best_index": 最优候选的序号（从 1 开始）,
    "score": 最优候选的评分（0-100）,
    "reason": 选择该候选的理由
}}
"""

        try:
            response = self.llm_client.Chat([{"role": "user", "content": judge_prompt}])
            content = getattr(response, "content", "").strip()
            match = re.search(r"\{[\s\S]*\}", content)
            if match:
                result = json.loads(match.group())
                best_idx = result.get("best_index", 1) - 1
                if 0 <= best_idx < len(candidates):
                    best_candidate = candidates[best_idx].copy()
                    best_candidate["index"] = best_idx
                    best_candidate["score"] = result.get("score", 0)
                    best_candidate["reason"] = result.get("reason", "")
                    return best_candidate
        except (json.JSONDecodeError, Exception):
            pass

        return candidates[-1]

    def CalcStateValue(
        self, state: Dict[str, Any], immediate_reward: float, gamma: float = 0.95
    ) -> float:
        """计算状态价值函数 V(s) = E[R_{t+1} + gamma * V(s_{t+1}) | S_t = s]

        预留接口：后续强化学习接入时，可重写此方法实现基于贝尔曼方程的价值计算。

        参数:
            state: 当前状态，包含 sql, columns, rows, iteration 等信息
            immediate_reward: 即时回报，如执行成功率、结果质量评分等
            gamma: 折扣因子，范围 [0, 1]，默认为 0.95

        返回:
            状态价值估计值，当前为占位实现，返回即时回报
        """
        return immediate_reward

    def _TraceStep(self, step: str, status: str, message: str) -> dict:
        return {"step": step, "status": status, "message": message}
