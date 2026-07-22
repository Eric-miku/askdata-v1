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


# ============================================================
# 会话管理相关模型
# ============================================================

class SessionCreateRequest(BaseModel):
    """创建会话请求体"""
    database_id: Optional[str] = Field(None, description="关联的数据库 ID")


class SessionCreateResponse(BaseModel):
    """创建会话响应体"""
    session_id: str = Field(..., description="会话唯一标识")
    thread_id: str = Field(..., description="LangGraph thread_id（与 session_id 一致）")
    created_at: float = Field(..., description="创建时间戳")
    database_id: Optional[str] = Field(None, description="关联的数据库 ID")


class SessionItem(BaseModel):
    """会话列表中的单个会话条目"""
    session_id: str = Field(..., description="会话唯一标识")
    thread_id: str = Field(..., description="LangGraph thread_id")
    created_at: float = Field(..., description="创建时间戳")
    updated_at: float = Field(..., description="最后更新时间戳")
    database_id: Optional[str] = Field(None, description="关联的数据库 ID")
    question_count: int = Field(0, description="对话轮次数")


class SessionListResponse(BaseModel):
    """会话列表响应体"""
    sessions: List[SessionItem] = Field(default_factory=list, description="会话列表")
    total: int = Field(0, description="会话总数")


class SessionDetailResponse(BaseModel):
    """会话详情响应体（含历史记录）"""
    session_id: str = Field(..., description="会话唯一标识")
    thread_id: str = Field(..., description="LangGraph thread_id")
    created_at: float = Field(..., description="创建时间戳")
    updated_at: float = Field(..., description="最后更新时间戳")
    database_id: Optional[str] = Field(None, description="关联的数据库 ID")
    history: List[Dict[str, Any]] = Field(default_factory=list, description="对话历史记录")


class SessionUpdateRequest(BaseModel):
    """更新会话请求体"""
    database_id: Optional[str] = Field(None, description="更新关联的数据库 ID")
