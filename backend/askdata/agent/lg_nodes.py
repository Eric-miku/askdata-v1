import re
from typing import Dict, Any
from sqlglot import parse_one, exp
from sqlglot import errors as sqlglot_errors

from .lg_state import WorkflowState
from .lg_protocols import ModelClient, Retriever, SqlExecutor, ResultAnalyzer
from .lg_prompts import BuildSqlPrompt, BuildRepairPrompt

MAX_SQL_LENGTH = 10000

DANGEROUS_OPERATIONS = {
    "Drop": "DROP",
    "Delete": "DELETE",
    "TruncateTable": "TRUNCATE",
    "Alter": "ALTER",
    "Update": "UPDATE",
    "Insert": "INSERT",
    "Create": "CREATE",
    "Grant": "GRANT",
    "Revoke": "REVOKE",
    "Rename": "RENAME",
}

DANGEROUS_FUNCTIONS = {"EXEC", "EXECUTE", "CALL", "SET", "SHOW"}


def _extract_sql_from_response(response: str) -> str:
    response = response.strip()
    
    patterns = [
        r"```sql\s*(.*?)\s*```",
        r"```\s*(.*?)\s*```",
        r"sql\s*=\s*(.+)",
        r"(SELECT\s+.+)",
        r"(WITH\s+.+)",
    ]
    
    for pattern in patterns:
        match = re.search(pattern, response, re.DOTALL | re.IGNORECASE)
        if match:
            sql = match.group(1).strip()
            if sql:
                return sql
    
    return response


def retrieve_schema_node(state: WorkflowState, retriever: Retriever) -> Dict[str, Any]:
    if not retriever:
        return {"schema_context": ""}
    
    schema_context = retriever.retrieve(state.user_query, database_id=state.database_id)
    return {"schema_context": schema_context}


def generate_sql_node(state: WorkflowState, model_client: ModelClient) -> Dict[str, Any]:
    if not model_client:
        return {"generated_sql": "", "validation_errors": ["模型客户端未配置"]}
    
    history_str = ""
    if state.messages:
        history_str = "\n对话历史:\n" + "\n".join(
            f"{msg['role']}: {msg['content']}" for msg in state.messages
        )
    
    prompt = BuildSqlPrompt(state.schema_context, state.user_query, history_str)
    response = model_client.generate(prompt)
    generated_sql = _extract_sql_from_response(response)
    return {"generated_sql": generated_sql}


SQL_KEYWORDS = {"SELECT", "WITH", "INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE", "MERGE", "EXPLAIN", "CALL"}


def validate_sql_node(state: WorkflowState, db_dialect: str = "mysql") -> Dict[str, Any]:
    errors = []
    sql = state.generated_sql

    if not sql:
        errors.append("生成的 SQL 为空")
        return {"validation_errors": errors}
    
    if len(sql) > MAX_SQL_LENGTH:
        errors.append(f"SQL 长度超过限制 ({len(sql)}/{MAX_SQL_LENGTH})")
        return {"validation_errors": errors}
    
    if not any(sql.strip().upper().startswith(keyword) for keyword in SQL_KEYWORDS):
        errors.append("生成的内容不是有效的 SQL 语句")
        return {"validation_errors": errors}

    try:
        parsed = parse_one(sql, dialect=db_dialect)
    except sqlglot_errors.ParseError as e:
        errors.append(f"SQL 语法错误：{e.errors}")
        return {"validation_errors": errors}
    except Exception as e:
        errors.append(f"解析失败：{str(e)}")
        return {"validation_errors": errors}

    for class_name, op_name in DANGEROUS_OPERATIONS.items():
        exp_class = getattr(exp, class_name, None)
        if exp_class is not None and list(parsed.find_all(exp_class)):
            errors.append(f"检测到危险操作 {op_name}，禁止执行")

    for func_name in DANGEROUS_FUNCTIONS:
        if func_name in sql.upper():
            errors.append(f"检测到危险函数调用 {func_name}，禁止执行")

    return {"validation_errors": errors}


def execute_sql_node(state: WorkflowState, sql_executor: SqlExecutor) -> Dict[str, Any]:
    if not sql_executor:
        return {"execution_result": {}, "execution_error": "SQL 执行器未配置"}
    
    try:
        result = sql_executor.execute(state.generated_sql)
        return {"execution_result": result, "execution_error": None}
    except Exception as e:
        return {"execution_result": {}, "execution_error": str(e)}


def analyze_result_node(state: WorkflowState, result_analyzer: ResultAnalyzer) -> Dict[str, Any]:
    if not result_analyzer:
        return {"natural_response": "无法生成自然语言回答，分析器未配置"}
    
    natural_response = result_analyzer.analyze(
        question=state.user_query,
        sql=state.generated_sql,
        result=state.execution_result,
    )
    return {"natural_response": natural_response}


def repair_sql_node(state: WorkflowState, model_client: ModelClient) -> Dict[str, Any]:
    if not model_client:
        return {"generated_sql": "", "validation_errors": ["模型客户端未配置"]}
    
    error_info = "\n".join(state.validation_errors) if state.validation_errors else state.execution_error
    
    history_str = ""
    if state.messages:
        history_str = "\n对话历史:\n" + "\n".join(
            f"{msg['role']}: {msg['content']}" for msg in state.messages
        )

    prompt = BuildRepairPrompt(
        schema_context=state.schema_context,
        user_query=state.user_query,
        generated_sql=state.generated_sql,
        error_info=error_info,
        history_str=history_str,
    )
    response = model_client.generate(prompt)
    repaired_sql = _extract_sql_from_response(response)
    return {
        "generated_sql": repaired_sql,
        "retry_count": state.retry_count + 1,
        "validation_errors": [],
        "execution_error": None,
    }


def finalize_node(state: WorkflowState) -> Dict[str, Any]:
    if state.validation_errors or state.execution_error:
        status = "failed"
    else:
        status = "success"
    return {"status": status}