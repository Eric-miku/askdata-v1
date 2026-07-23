"""
FastAPI 应用入口 —— 整个后端服务的心脏

这里创建了 FastAPI 实例，配置了:
1. CORS 中间件 —— 允许前端 Vite 开发服务器跨域请求
2. 生命周期管理 —— 应用启动/关闭时的资源初始化和清理
3. 路由挂载 —— 将 routes.py 中的路由注册到应用
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from askdata.api.query_service import QueryService
from askdata.api.routes import router
from askdata.api.session_store import SessionStore
from askdata.core.config import settings
from askdata.core.paths import project_path


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Initialize and close application-scoped resources."""
    print("[AskData] 服务启动中...")
    store = SessionStore(project_path(settings.APP_DATABASE_PATH))
    await store.Initialize()
    application.state.session_store = store
    application.state.query_service = QueryService(store)
    try:
        yield
    finally:
        print("[AskData] 服务关闭中...")
        await store.Close()


# 创建 FastAPI 应用实例
# - lifespan: 注册生命周期回调
# - title: API 文档标题（在 /docs 中显示）
# - version: 接口版本号
app = FastAPI(
    title="AskData API",
    description="智能问数平台 (Text-to-SQL) 后端服务 API",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS 中间件配置
# 前端 Vite 开发服务器默认运行在 localhost:5173
# 生产环境部署时请替换为实际域名
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:3000",
        "http://7.59.11.153:5173",
        "http://7.59.11.153:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 将路由注册到应用
# prefix="/api" 表示所有路由都以 /api 开头
# 例如: /api/query, /api/metadata/databases
app.include_router(router, prefix="/api")


# 仅在直接运行此文件时执行的调试入口
# 正常启动请使用: uvicorn askdata.api.app:app --reload
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("askdata.api.app:app", host="0.0.0.0", port=8000, reload=True)
