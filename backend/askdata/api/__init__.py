# API 模块
# 提供 FastAPI 路由定义、请求响应模型、会话管理和 Trace 日志工具
"""
API 模块 — AskData 后端服务 HTTP 层

提供 FastAPI 路由定义、请求响应模型、会话管理和 Trace 日志工具。

组件说明:
    app              — FastAPI 应用实例 (from .app import app)
    router           — APIRouter 实例，注册了所有 API 路由
    QueryRequest     — 自然语言查询请求体模型
    QueryResponse    — 统一查询响应模型 (含 SQL、数据、图表、Trace)
    session_manager  — 全局会话管理器单例 (内存存储)
    TraceLogger      — 请求级 Trace 日志记录器
    get_engine       — 获取/创建 SQLAlchemy Engine（带连接池缓存）

用法:
    from askdata.api import app                    # FastAPI 实例，用于 uvicorn 启动
    from askdata.api import router                 # APIRouter，用于挂载到其他应用
    from askdata.api import QueryRequest, QueryResponse  # Pydantic 模型
    from askdata.api import session_manager        # 会话管理
    from askdata.api import TraceLogger            # Trace 日志
    from askdata.api import get_engine             # 获取数据库引擎
"""

# FastAPI 应用实例 — 整个后端的 WSGI/ASGI 入口
from askdata.api.app import app

# APIRouter 实例 — 所有 HTTP 路由定义 (/api/query, /api/sessions, /api/metadata/...)
from askdata.api.routes import router

# Pydantic 请求/响应模型
from askdata.api.schemas import QueryRequest, QueryResponse

# 全局会话管理器单例
from askdata.api.session_manager import session_manager

# Trace 日志工具
from askdata.api.trace import TraceLogger

# 数据库引擎工厂（带连接池缓存）
from askdata.api.app import get_engine

# 显式定义 __all__ 控制 from askdata.api import * 的导出内容
__all__ = [
    "app",
    "router",
    "QueryRequest",
    "QueryResponse",
    "session_manager",
    "TraceLogger",
    "get_engine",
]
