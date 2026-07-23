from pydantic import BaseModel, Field, model_validator
from typing import List, Dict, Any, Optional


class QueryRequest(BaseModel):
    question: str = Field(..., description="用户输入的自然语言问题")
    database_id: str = Field(..., description="选中的 BIRD 数据库 ID，如 'california_schools'")
    session_id: Optional[str] = Field(None, description="多轮对话的会话 ID")


class ExecuteSqlRequest(BaseModel):
    """Read-only replay request used to restore a historical result view."""

    database_id: str = Field(..., description="历史 SQL 所属的 BIRD 数据库")
    sql: str = Field(..., min_length=1, description="Previously generated read-only SQL")


class ExportRequest(ExecuteSqlRequest):
    question: str = Field("", description="原始自然语言问题")
    format: str = Field(..., pattern="^(csv|xlsx)$", description="导出格式")


class KnowledgeEntryRequest(BaseModel):
    kind: str = Field(..., pattern="^(term|metric)$")
    standard_name: str = Field(..., min_length=1, max_length=200)
    definition: str = ""
    category: str = ""
    scope: str = ""
    status: str = Field("draft", pattern="^(draft|published|disabled)$")
    aliases: List[str] = Field(default_factory=list)
    mappings: List[Dict[str, Any]] = Field(default_factory=list)
    formula: str = ""
    aggregation: str = ""
    unit: str = ""
    time_field: str = ""
    examples: List[str] = Field(default_factory=list)
    changelog: str = ""


class KnowledgeBulkImportRequest(BaseModel):
    entries: List[Dict[str, Any]] = Field(..., min_length=1, max_length=1000)
    mode: str = Field("upsert", pattern="^(append|upsert)$")


class DataSourceRequest(BaseModel):
    id: str = Field(..., pattern=r"^[A-Za-z0-9_-]+$", min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=200)
    kind: str = Field("sqlite", pattern="^(sqlite|mysql|postgres|postgresql)$")
    path: str = Field(..., min_length=1, description="SQLite 相对路径，或外部数据库 SQLAlchemy 连接串")
    enabled: bool = True


class DataSourceStatusRequest(BaseModel):
    enabled: bool


class PermissionPolicyRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=200)
    database_id: str = Field(..., min_length=1, max_length=100)
    table_name: Optional[str] = Field(None, max_length=200)
    field_name: Optional[str] = Field(None, max_length=200)
    can_query: bool = True
    can_export: bool = True
    row_filter: Optional[str] = Field(None, max_length=1000)

    @model_validator(mode="after")
    def validate_scope(self):
        if self.field_name and not self.table_name:
            raise ValueError("字段级权限必须指定表名")
        if self.row_filter:
            from askdata.security.permissions import NormalizeRowFilter

            self.row_filter = NormalizeRowFilter(self.row_filter, self.table_name)
            if self.field_name:
                raise ValueError("行过滤条件应配置在表级策略，不应指定字段")
        return self


class QueryResponse(BaseModel):
    answer: str = Field(..., description="LLM 生成的最终中文解释")
    sql: Optional[str] = Field(None, description="系统生成并执行的 SQL 语句")
    columns: Optional[List[str]] = Field(None, description="表格列名，如 ['School', 'Total_Students']")
    rows: Optional[List[Dict[str, Any]]] = Field(None, description="表格数据行，每行是 column -> value 的字典")
    chart: Optional[Dict[str, Any]] = Field(None, description="ECharts 图表配置，包含 type, xAxis, yAxis 等")
    analysis: Optional[Dict[str, Any]] = Field(None, description="可追溯的结构化分析结果")
    suggestions: List[str] = Field(default_factory=list, description="基于当前结果的关联分析问题")
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
