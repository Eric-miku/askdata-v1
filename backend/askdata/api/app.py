"""
FastAPI 应用入口 —— 整个后端服务的心脏

这里创建了 FastAPI 实例，配置了:
1. CORS 中间件 —— 允许前端 Vite 开发服务器跨域请求
2. 生命周期管理 —— 应用启动/关闭时的资源初始化和清理
3. 路由挂载 —— 将 routes.py 中的路由注册到应用
"""

from contextlib import asynccontextmanager
import logging
import json
import time
import uuid

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Engine
from askdata.api.routes import router
from askdata.api.request_context import GetRequestId, ResetRequestId, SetRequestId
from askdata.core.config import settings
from askdata.core.paths import project_path


logger = logging.getLogger("askdata.api")


class RequestContextMiddleware:
    """Pure ASGI request logging without BaseHTTPMiddleware streaming issues."""

    def __init__(self, app):
        self.application = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.application(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        request_id = headers.get(b"x-request-id", b"").decode() or str(uuid.uuid4())
        request_token = SetRequestId(request_id)
        started = time.perf_counter()
        status_code = 500

        async def send_with_context(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.application(scope, receive, send_with_context)
        finally:
            logger.info(json.dumps({
                "event": "http_request", "request_id": request_id,
                "method": scope.get("method"), "path": scope.get("path"),
                "status_code": status_code,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            }, ensure_ascii=False))
            ResetRequestId(request_token)


# 全局引擎缓存 —— 按 database_path 缓存 SQLAlchemy Engine，避免重复创建连接池
# 在 lifespan startup 中初始化，shutdown 中清理
_engine_pool: dict[str, Engine] = {}


def get_engine(database_path: str) -> Engine:
    """获取或创建数据库引擎

    优先从 _engine_pool 返回缓存的 Engine，避免重复建立连接池。
    如果不存在则创建新 Engine 并缓存。

    Args:
        database_path: SQLite 数据库文件的绝对路径

    Returns:
        SQLAlchemy Engine 实例
    """
    if database_path not in _engine_pool:
        engine = create_engine(f"sqlite:///{database_path}", pool_pre_ping=True)
        _engine_pool[database_path] = engine
    return _engine_pool[database_path]


@asynccontextmanager
async def lifespan(application: FastAPI):
    """
    应用生命周期管理

    startup（yield 之前）:
      - 扫描 BIRD 数据库文件目录，确认数据就绪
      - 预热 SemanticRetriever（加载 databases.json schema 索引到内存）
      - 初始化数据库连接池缓存

    shutdown（yield 之后）:
      - 关闭所有缓存的数据库连接池
    """
    # ---------- 启动逻辑 ----------
    logger.info("service_starting")

    # 1. 确认 BIRD 数据目录存在
    bird_data_dir = project_path(settings.BIRD_DATA_DIR)
    databases_dir = bird_data_dir / "databases"
    if not databases_dir.exists():
        logger.warning("bird_database_directory_missing", extra={"database_path": str(databases_dir)})
    else:
        db_count = len(list(databases_dir.rglob("*.db"))) + len(list(databases_dir.rglob("*.sqlite")))
        logger.info("bird_databases_ready", extra={"database_path": str(databases_dir), "database_count": db_count})

    # Schema 索引在首个查询时懒加载，避免就绪探针被大数据目录预热阻塞。
    logger.info("schema_index_lazy_load_enabled")

    yield  # 应用在此运行，直到收到关闭信号

    # ---------- 关闭逻辑 ----------
    logger.info("service_stopping")

    # 3. 关闭所有缓存的数据库引擎连接池
    closed_count = 0
    for path, engine in _engine_pool.items():
        try:
            engine.dispose()
            closed_count += 1
        except Exception as exc:
            logger.warning("engine_close_failed", extra={"database_path": path, "error": str(exc)})
    _engine_pool.clear()
    logger.info("service_stopped", extra={"closed_engine_count": closed_count})


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
app.add_middleware(RequestContextMiddleware)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    request_id = GetRequestId() or request.headers.get("X-Request-ID", "")
    logger.exception(json.dumps({
        "event": "unhandled_exception", "request_id": request_id,
        "method": request.method, "path": request.url.path,
        "error_type": type(exc).__name__,
    }, ensure_ascii=False))
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "服务暂时无法处理请求，请稍后重试",
                "request_id": request_id,
            }
        },
        headers={"X-Request-ID": request_id} if request_id else None,
    )


@app.get("/health", tags=["operations"])
async def health():
    return {"status": "ok", "service": "askdata"}


@app.get("/ready", tags=["operations"])
async def readiness(response: Response):
    databases_dir = project_path(settings.BIRD_DATA_DIR) / "databases"
    ready = databases_dir.is_dir()
    if not ready:
        response.status_code = 503
    return {"status": "ready" if ready else "not_ready", "databases_dir": str(databases_dir)}


@app.get("/metrics", tags=["operations"], response_class=Response)
async def metrics():
    databases_dir = project_path(settings.BIRD_DATA_DIR) / "databases"
    database_count = sum(1 for pattern in ("*.db", "*.sqlite") for _ in databases_dir.rglob(pattern)) if databases_dir.is_dir() else 0
    body = "\n".join([
        "# HELP askdata_database_count Number of discovered SQLite databases.",
        "# TYPE askdata_database_count gauge",
        f"askdata_database_count {database_count}",
        "# HELP askdata_engine_pool_size Number of cached database engines.",
        "# TYPE askdata_engine_pool_size gauge",
        f"askdata_engine_pool_size {len(_engine_pool)}",
        "",
    ])
    return Response(content=body, media_type="text/plain; version=0.0.4")

# CORS 中间件配置
# 前端 Vite 开发服务器默认运行在 localhost:5173
# 生产环境部署时请替换为实际域名
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.CORS_ORIGINS.split(",") if origin.strip()],
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
