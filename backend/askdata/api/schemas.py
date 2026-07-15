from typing import Any, Dict, List, Optional, Self

from pydantic import BaseModel, Field, model_validator


def _strip_optional(value: Optional[str]) -> Optional[str]:
    stripped = value.strip() if value else ""
    return stripped or None


class ClarificationResolution(BaseModel):
    clarification_id: str
    option_id: Optional[str] = None
    text: Optional[str] = None

    @model_validator(mode="after")
    def require_exactly_one_resolution(self) -> Self:
        self.option_id = _strip_optional(self.option_id)
        self.text = _strip_optional(self.text)

        if bool(self.option_id) == bool(self.text):
            raise ValueError("Provide exactly one of option_id or text")
        return self


class QueryRequest(BaseModel):
    database_id: str = Field(..., description="选中的 BIRD 数据库 ID，如 'california_schools'")
    session_id: Optional[str] = Field(None, description="多轮对话的会话 ID")
    question: Optional[str] = Field(None, description="用户输入的自然语言问题")
    clarification: Optional[ClarificationResolution] = None

    @model_validator(mode="after")
    def require_exactly_one_input(self) -> Self:
        self.question = _strip_optional(self.question)

        if bool(self.question) == (self.clarification is not None):
            raise ValueError("Provide exactly one of question or clarification")
        return self


class QueryResponse(BaseModel):
    answer: str = Field(..., description="LLM 生成的最终中文解释")
    sql: Optional[str] = Field(None, description="系统生成并执行的 SQL 语句")
    columns: Optional[List[str]] = Field(None, description="表格列名，如 ['School', 'Total_Students']")
    rows: Optional[List[Dict[str, Any]]] = Field(None, description="表格数据行，每行是 column -> value 的字典")
    chart: Optional[Dict[str, Any]] = Field(None, description="ECharts 图表配置，包含 type, xAxis, yAxis 等")
    trace: List[Any] = Field(default_factory=list, description="Agent 执行轨迹日志")
    error: Optional[str] = Field(None, description="执行过程中的错误信息")
