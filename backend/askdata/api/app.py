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
from askdata.api.routes import router


@asynccontextmanager
async def lifespan(application: FastAPI):
    """
    应用生命周期管理

    startup（yield 之前）:
      应用启动时执行，适合做：
      - 初始化数据库连接池
      - 加载元数据到缓存
      - 预热 LLM 模型

    shutdown（yield 之后）:
      应用关闭时执行，适合做：
      - 关闭所有数据库连接
      - 清理临时文件
      - 释放资源

    目前 V1 阶段还未接入真实模块，先预留空实现。
    """
    # ---------- 启动逻辑 ----------
    print("[AskData] 服务启动中...")
    # TODO: 初始化数据库连接池（等待 db/executor.py 实现）
    # TODO: 加载 BIRD 元数据缓存（等待 tools/retriever.py 实现）

    yield  # 应用在此运行，直到收到关闭信号

    # ---------- 关闭逻辑 ----------
    print("[AskData] 服务关闭中...")
    # TODO: 关闭数据库连接池


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
        "http://7.59.11.153:5173",
        "http://7.59.11.153:5174",
    ],
    allow_credentials=True,   # 允许携带 Cookie
    allow_methods=["*"],      # 允许所有 HTTP 方法（GET, POST, DELETE 等）
    allow_headers=["*"],      # 允许所有请求头
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
