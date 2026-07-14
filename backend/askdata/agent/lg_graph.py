from typing import Dict, Any, Optional, List, Literal, Callable
from langgraph.graph import StateGraph, END

from .lg_state import WorkflowState
from .lg_nodes import (
    retrieve_schema_node,
    generate_sql_node,
    validate_sql_node,
    execute_sql_node,
    analyze_result_node,
    repair_sql_node,
    finalize_node,
)
from .lg_trace import traced
from .lg_protocols import ModelClient, Retriever, SqlExecutor, ResultAnalyzer

DEFAULT_MAX_RETRIES = 3
DEFAULT_RECURSION_LIMIT = 20
DEFAULT_DB_DIALECT = "mysql"


def _create_traced_node(func: Callable, *args, **kwargs) -> Callable:
    def wrapped_node(state: WorkflowState) -> Dict[str, Any]:
        return func(state, *args, **kwargs)
    
    wrapped_node.__name__ = func.__name__
    return traced(wrapped_node)


def create_workflow_graph(
    model_client: ModelClient,
    retriever: Retriever,
    sql_executor: SqlExecutor,
    result_analyzer: ResultAnalyzer,
    max_retries: int = DEFAULT_MAX_RETRIES,
    db_dialect: str = DEFAULT_DB_DIALECT,
) -> Any:
    wrapped_retrieve_schema = _create_traced_node(retrieve_schema_node, retriever)
    wrapped_generate_sql = _create_traced_node(generate_sql_node, model_client)
    wrapped_validate_sql = _create_traced_node(validate_sql_node, db_dialect)
    wrapped_execute_sql = _create_traced_node(execute_sql_node, sql_executor)
    wrapped_analyze_result = _create_traced_node(analyze_result_node, result_analyzer)
    wrapped_repair_sql = _create_traced_node(repair_sql_node, model_client)
    wrapped_finalize = _create_traced_node(finalize_node)

    def validate_conditional_edge(state: WorkflowState) -> Literal["execute_sql", "repair_sql", "finalize"]:
        if not state.validation_errors:
            return "execute_sql"
        if state.retry_count >= state.max_retries:
            return "finalize"
        return "repair_sql"

    def execute_conditional_edge(state: WorkflowState) -> Literal["analyze_result", "repair_sql", "finalize"]:
        if not state.execution_error:
            return "analyze_result"
        if state.retry_count >= state.max_retries:
            return "finalize"
        return "repair_sql"

    graph = StateGraph(WorkflowState)

    graph.add_node("retrieve_schema", wrapped_retrieve_schema)
    graph.add_node("generate_sql", wrapped_generate_sql)
    graph.add_node("validate_sql", wrapped_validate_sql)
    graph.add_node("execute_sql", wrapped_execute_sql)
    graph.add_node("analyze_result", wrapped_analyze_result)
    graph.add_node("repair_sql", wrapped_repair_sql)
    graph.add_node("finalize", wrapped_finalize)

    graph.set_entry_point("retrieve_schema")

    graph.add_edge("retrieve_schema", "generate_sql")
    graph.add_edge("generate_sql", "validate_sql")

    graph.add_conditional_edges(
        "validate_sql",
        validate_conditional_edge,
        {
            "execute_sql": "execute_sql",
            "repair_sql": "repair_sql",
            "finalize": "finalize",
        },
    )

    graph.add_conditional_edges(
        "execute_sql",
        execute_conditional_edge,
        {
            "analyze_result": "analyze_result",
            "repair_sql": "repair_sql",
            "finalize": "finalize",
        },
    )

    graph.add_edge("repair_sql", "validate_sql")
    graph.add_edge("analyze_result", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()


def run_workflow(
    compiled_graph: Any,
    user_query: str,
    messages: Optional[List[Dict]] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    database_id: Optional[str] = None,
) -> Dict[str, Any]:
    initial_state = WorkflowState(
        user_query=user_query,
        messages=messages or [],
        max_retries=max_retries,
        trace=[],
        database_id=database_id,
    )
    final_state = compiled_graph.invoke(initial_state)

    if isinstance(final_state, dict):
        state_dict = final_state
    elif hasattr(final_state, "model_dump"):
        state_dict = final_state.model_dump()
    elif hasattr(final_state, "__dict__"):
        state_dict = final_state.__dict__
    else:
        state_dict = {}

    return {
        "sql": state_dict.get("generated_sql", ""),
        "answer": state_dict.get("natural_response", ""),
        "columns": state_dict.get("execution_result", {}).get("columns", []),
        "rows": state_dict.get("execution_result", {}).get("rows", []),
        "status": state_dict.get("status", "failed"),
        "trace": state_dict.get("trace", []),
        "error": state_dict.get("execution_error") or (", ".join(state_dict.get("validation_errors", [])) if state_dict.get("validation_errors") else None),
        "retry_count": state_dict.get("retry_count", 0),
    }