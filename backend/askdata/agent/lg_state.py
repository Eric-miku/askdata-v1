from typing import List, Dict, Optional, Literal, Annotated
from pydantic import BaseModel, Field, ConfigDict, field_validator
import operator

StatusType = Literal["running", "success", "failed"]


class WorkflowState(BaseModel):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_default=True,
        extra="ignore"
    )
    
    user_query: str = Field(..., description="用户自然语言查询")
    messages: Annotated[List[Dict], operator.add] = Field(default_factory=list, description="多轮对话历史")
    schema_context: str = Field(default="", description="检索库返回表结构上下文")
    generated_sql: str = Field(default="", description="LLM生成/修复后的SQL")
    validation_errors: List[str] = Field(default_factory=list, description="SQL语法/风险校验错误")
    execution_result: Dict = Field(default_factory=dict, description="SQL执行结果 {columns:[], rows:[]}")
    natural_response: str = Field(default="", description="结果自然语言总结")
    retry_count: int = Field(default=0, ge=0, description="当前重试次数")
    max_retries: int = Field(default=3, gt=0, description="最大允许重试次数")
    trace: Annotated[List[Dict], operator.add] = Field(default_factory=list, description="节点执行追踪日志")
    execution_error: Optional[str] = Field(default=None, description="SQL执行异常信息")
    status: StatusType = Field(default="running", description="运行状态")
    database_id: Optional[str] = Field(default=None, description="数据库标识")

    @field_validator("retry_count")
    def no_negative_retry(cls, v):
        return max(v, 0)