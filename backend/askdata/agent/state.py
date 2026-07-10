from typing import TypedDict, List, Dict, Any, Optional

class AgentState(TypedDict):
    question: str                   # 原始问题
    database_id: str                # 数据库标识
    schema_context: str             # 检索到的表结构 Prompt
    generated_sql: Optional[str]    # 当前生成的 SQL
    execution_result: Optional[List[Dict[str, Any]]] # 数据库返回的原始字典列表
    final_answer: str               # 最终中文解释
    error_log: Optional[str]        # SQL 报错信息（用于传给 LLM 进行修复）
    iterations: int                 # 记录重试次数，防止无限循环