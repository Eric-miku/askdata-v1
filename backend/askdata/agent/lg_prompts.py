def BuildSqlPrompt(schema_context: str, user_query: str, history_str: str = "") -> str:
    return f"""你是一个专业的 SQL 生成助手。请根据用户问题和提供的数据库 Schema 生成正确的 SQL 语句。

数据库 Schema:
{schema_context}

{history_str}

用户问题: {user_query}

请直接返回 SQL 语句，不要包含任何额外的解释或说明。
"""


def BuildRepairPrompt(
    schema_context: str,
    user_query: str,
    generated_sql: str,
    error_info: str,
    history_str: str = "",
) -> str:
    return f"""你之前生成的 SQL 存在错误，请根据错误信息进行修复。

数据库 Schema:
{schema_context}

{history_str}

用户问题: {user_query}

原始 SQL:
{generated_sql}

错误信息:
{error_info}

请分析错误原因并生成修正后的 SQL 语句。直接返回 SQL，不要包含任何解释。
"""