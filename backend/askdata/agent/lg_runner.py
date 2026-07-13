from typing import Dict, Any, Optional, List
from pydantic import BaseModel

from .lg_protocols import ModelClient, Retriever, SqlExecutor, ResultAnalyzer
from .lg_graph import create_workflow_graph, run_workflow, DEFAULT_MAX_RETRIES


class QueryResponse(BaseModel):
    answer: str = ""
    sql: str = ""
    columns: List[Any] = []
    rows: List[Any] = []
    status: str = "failed"
    trace: List[Dict[str, Any]] = []
    error: Optional[str] = None
    retry_count: int = 0


class AgentRunner:
    def __init__(
        self,
        model_client: ModelClient,
        retriever: Retriever,
        sql_executor: SqlExecutor,
        result_analyzer: ResultAnalyzer,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        self.model_client = model_client
        self.retriever = retriever
        self.sql_executor = sql_executor
        self.result_analyzer = result_analyzer
        self.max_retries = max_retries
        self.compiled_graph = create_workflow_graph(
            model_client=model_client,
            retriever=retriever,
            sql_executor=sql_executor,
            result_analyzer=result_analyzer,
            max_retries=max_retries,
        )

    def query(
        self,
        user_query: str,
        session_messages: Optional[List[Dict]] = None,
        database_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            result = run_workflow(
                compiled_graph=self.compiled_graph,
                user_query=user_query,
                messages=session_messages,
                max_retries=self.max_retries,
                database_id=database_id,
            )
            return {
                "answer": result.get("answer", ""),
                "sql": result.get("sql", ""),
                "columns": result.get("columns", []),
                "rows": result.get("rows", []),
                "status": result.get("status", "failed"),
                "trace": result.get("trace", []),
                "error": result.get("error"),
                "retry_count": result.get("retry_count", 0),
            }
        except Exception as e:
            return {
                "answer": "",
                "sql": "",
                "columns": [],
                "rows": [],
                "status": "failed",
                "trace": [],
                "error": f"工作流执行异常：{str(e)}",
                "retry_count": 0,
            }