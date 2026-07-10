"""ResultAnalyzer — turns SQL execution results into a concise Chinese explanation. Uses LLM by default, falls back to deterministic summary on failure."""

import json

from askdata.core.llm import LLMClient


class ResultAnalyzer:
    """Turns SQL execution results into a concise Chinese explanation."""

    def __init__(self, llm_client=None):
        self.llm_client = llm_client or LLMClient()

    def Analyze(self, question: str, sql: str, columns: list[str], rows: list[dict]) -> str:
        prompt = self.BuildPrompt(question, sql, columns, rows)
        try:
            answer = self.llm_client.Complete(prompt).strip()
            if answer:
                return answer
        except Exception:
            pass
        return self.Fallback(columns, rows)

    def BuildPrompt(self, question: str, sql: str, columns: list[str], rows: list[dict]) -> str:
        preview = rows[:20]
        return "\n".join([
            "你是数据分析助手。请用中文根据 SQL 查询结果回答用户问题。",
            "要求：简洁、准确，不要编造结果中不存在的信息。",
            f"用户问题：{question}",
            f"SQL：{sql}",
            f"列名：{json.dumps(columns, ensure_ascii=False)}",
            f"结果预览：{json.dumps(preview, ensure_ascii=False, default=str)}",
        ])

    def Fallback(self, columns: list[str], rows: list[dict]) -> str:
        if not rows:
            return "查询没有返回结果。"
        if len(rows) == 1 and len(columns) == 1:
            return f"查询结果是 {rows[0].get(columns[0])}。"
        return f"查询返回 {len(rows)} 行结果。"
