from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

class QueryRequest(BaseModel):
    question: str = Field(..., description="用户输入的自然语言问题")
    database_id: str = Field(..., description="选中的 BIRD 数据库 ID，如 'california_schools'")
    session_id: Optional[str] = Field(None, description="多轮对话的会话 ID")

class QueryResponse(BaseModel):
    answer: str = Field(..., description="LLM 生成的最终中文解释")
    sql: Optional[str] = Field(None, description="系统生成并执行的 SQL 语句")
    columns: Optional[List[str]] = Field(None, description="表格列名，如 ['School', 'Total_Students']")
    rows: Optional[List[Dict[str, Any]]] = Field(None, description="表格数据行，每行是 column -> value 的字典")
    chart: Optional[Dict[str, Any]] = Field(None, description="ECharts 图表配置，包含 type, xAxis, yAxis 等")
    trace: List[Any] = Field(default_factory=list, description="Agent 执行轨迹日志")
    error: Optional[str] = Field(None, description="执行过程中的错误信息")
