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
from sqlalchemy import create_engine, Engine
from askdata.api.routes import router
from askdata.core.config import settings
from askdata.core.paths import project_path
from askdata.tools.retriever import SemanticRetriever


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
    print("[AskData] 服务启动中...")

    # 1. 确认 BIRD 数据目录存在
    bird_data_dir = project_path(settings.BIRD_DATA_DIR)
    databases_dir = bird_data_dir / "databases"
    if not databases_dir.exists():
        print(f"[AskData] 警告: BIRD 数据库目录不存在: {databases_dir}")
    else:
        db_count = len(list(databases_dir.rglob("*.db"))) + len(list(databases_dir.rglob("*.sqlite")))
        print(f"[AskData] BIRD 数据目录: {databases_dir} (发现 {db_count} 个数据库文件)")

    # 2. 预热 SemanticRetriever —— 将 databases.json 中的 schema 元数据加载到内存
    #    这样第一个 /api/query 请求不用再等待磁盘 I/O
    try:
        retriever = SemanticRetriever().Build()
        database_count = len(retriever.index.databases) if retriever.index else 0
        print(f"[AskData] Schema 索引已加载: {database_count} 个数据库")
    except FileNotFoundError as exc:
        print(f"[AskData] 警告: Schema 索引文件未找到: {exc}")
        print("[AskData] 部分接口（如 /api/query）在首次调用时可能略有延迟")
    except Exception as exc:
        print(f"[AskData] 警告: Schema 索引加载失败: {exc}")

    yield  # 应用在此运行，直到收到关闭信号

    # ---------- 关闭逻辑 ----------
    print("[AskData] 服务关闭中...")

    # 3. 关闭所有缓存的数据库引擎连接池
    closed_count = 0
    for path, engine in _engine_pool.items():
        try:
            engine.dispose()
            closed_count += 1
        except Exception as exc:
            print(f"[AskData] 关闭引擎失败: {path} — {exc}")
    _engine_pool.clear()
    print(f"[AskData] 已关闭 {closed_count} 个数据库连接池")


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
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
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
