"""
API 路由定义 —— 所有 HTTP 接口的入口

本文件实现了以下接口:
  1. GET    /api/metadata/databases              — 获取数据库列表
  2. GET    /api/metadata/{database_id}/tables   — 获取表结构
  3. GET    /api/sessions                        — 获取会话列表
  4. POST   /api/sessions                        — 创建会话
  5. GET    /api/sessions/{session_id}           — 获取会话详情（含历史）
  6. PATCH  /api/sessions/{session_id}           — 更新会话（如切换数据库）
  7. DELETE /api/sessions/{session_id}           — 删除会话
  8. POST   /api/sessions/{session_id}/reset     — 重置会话（清空历史）
  9. POST   /api/query                           — 自然语言查询（核心接口）

设计原则:
  - 每个接口都记录 Trace 日志
  - 异常统一由全局异常处理器捕获，返回一致的错误格式
  - /api/query 调用 AgentGraph 工作流，执行 NL2SQL 全链路：
    SemanticRetriever → LLM(ReAct/One-Shot) → SQLExecutor → ResultAnalyzer
  - /api/sessions/* 接口为前端提供完整的会话管理和历史记录功能，
    与 LangGraph SqliteSaver 检查点机制配合，实现多轮对话持久化
  - 核心 /api/query 接口通过 AgentGraph → ReActSqlAgent 完成 NL2SQL 全链路
"""

import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from askdata.api.schemas import (
    QueryRequest,
    QueryResponse,
    ExecuteSqlRequest,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionItem,
    SessionListResponse,
    SessionDetailResponse,
    SessionUpdateRequest,
)
from askdata.api.trace import TraceLogger
from askdata.api.session_manager import session_manager
from askdata.agent.graph import AgentGraph
from askdata.core.config import settings
from askdata.core.paths import project_path
from askdata.db.query_runner import Execute as ExecuteSql

# 创建 APIRouter 实例
# 在 app.py 中通过 app.include_router(router, prefix="/api") 注册
router = APIRouter()


# ============================================================
# 辅助函数
# ============================================================


def _CountTables(db_path: str) -> int:
    """统计 SQLite 数据库中的表数量（仅查询 COUNT，更高效）"""
    try:
        connection = sqlite3.connect(db_path)
        try:
            cursor = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            return cursor.fetchone()[0]
        finally:
            connection.close()
    except sqlite3.Error:
        return 0


def _get_bird_databases_dir() -> Path:
    """获取 BIRD 数据库文件存放目录的绝对路径

    settings.BIRD_DATA_DIR 是相对于项目根的路径配置（如 "../data/bird"），
    这里需要解析为绝对路径。

    Returns:
        databases 目录的 Path 对象
    """
    return project_path(settings.BIRD_DATA_DIR) / "databases"


def _scan_databases() -> list[dict]:
    """扫描 data/bird/databases/ 下的所有 SQLite 数据库文件

    遍历目录，找到所有 .db 或 .sqlite 文件，返回数据库元信息列表。

    Returns:
        数据库列表，每项包含 id, name, tables_count
    """
    databases_dir = _get_bird_databases_dir()
    databases = []

    # 如果目录不存在，返回空列表
    if not databases_dir.exists():
        return databases

    # 查找所有 .db 和 .sqlite 文件
    db_files = list(databases_dir.rglob("*.db")) + list(databases_dir.rglob("*.sqlite"))

    seen = set()
    for db_path in db_files:
        # 用文件名（不含扩展名）作为数据库 ID
        db_id = db_path.stem
        if db_id not in seen:
            seen.add(db_id)
            databases.append({
                "id": db_id,
                "name": db_id.replace("_", " ").title(),
                "path": str(db_path),           # 转为 str，保持类型一致
                "tables_count": _CountTables(str(db_path)),  # 使用高效 COUNT 查询
            })

    return databases


def _read_sqlite_tables(db_path: str) -> list[dict]:
    """Read table and column metadata from a SQLite database."""
    connection = sqlite3.connect(db_path)
    try:
        cursor = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        tables = []
        for (table_name,) in cursor.fetchall():
            quoted_table_name = '"' + table_name.replace('"', '""') + '"'
            columns = []
            for column in connection.execute(f"PRAGMA table_info({quoted_table_name})").fetchall():
                columns.append({
                    "name": column[1],
                    "type": column[2] or "TEXT",
                    "primary_key": bool(column[5]),
                    "nullable": not bool(column[3]),
                })
            tables.append({"table_name": table_name, "columns": columns})
        return tables
    finally:
        connection.close()


# ============================================================
# 路由定义
# ============================================================

@router.get("/metadata/databases")
async def list_databases():
    """
    获取所有可用数据库列表

    供前端数据库选择器下拉框使用。扫描 data/bird/databases/
    目录下的所有 SQLite 文件，返回数据库 ID 和可读名称。

    用法:
        GET /api/metadata/databases

    返回示例:
        [
            {"id": "california_schools", "name": "California Schools", "tables_count": 8},
            {"id": "debit_card_specializing", "name": "Debit Card Specializing", "tables_count": 5}
        ]
    """
    trace = TraceLogger()
    trace.log("扫描数据库列表")

    databases = _scan_databases()

    trace.log("扫描完成", f"找到 {len(databases)} 个数据库")
    return databases


@router.get("/metadata/{database_id}/tables")
async def get_tables(database_id: str):
    """
    获取指定数据库的表结构信息

    查询数据库中的所有表名及其字段信息。
    V1 阶段使用 SQLite 的 sqlite_master 表和 PRAGMA table_info 来获取结构。

    用法:
        GET /api/metadata/california_schools/tables

    返回示例:
        {
            "database_id": "california_schools",
            "tables": [
                {
                    "table_name": "schools",
                    "columns": [
                        {"name": "School", "type": "TEXT"},
                        {"name": "Total_Students", "type": "INTEGER"}
                    ]
                }
            ]
        }
    """
    trace = TraceLogger()
    trace.log("查询表结构", f"database_id={database_id}")

    # 查找数据库文件
    databases = _scan_databases()
    db_info = next((db for db in databases if db["id"] == database_id), None)

    if db_info is None:
        trace.log("数据库未找到", f"database_id={database_id}")
        raise HTTPException(
            status_code=404,
            detail=f"数据库 '{database_id}' 未找到。可用的数据库: {[db['id'] for db in databases]}"
        )

    try:
        tables = _read_sqlite_tables(db_info["path"])
    except sqlite3.Error as exc:
        trace.log("读取表结构失败", str(exc))
        raise HTTPException(status_code=500, detail=f"读取数据库结构失败: {exc}") from exc

    trace.log("返回表结构", f"database_id={database_id}, tables={len(tables)}")

    return {
        "database_id": database_id,
        "tables": tables,
    }


# ============================================================
# 会话管理路由
# ============================================================

@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    limit: int = Query(50, ge=1, le=200, description="返回条数上限"),
    offset: int = Query(0, ge=0, description="分页偏移量"),
):
    """
    获取会话列表

    返回所有会话的摘要信息，按最后更新时间降序排列（最新在最前）。
    供前端会话侧边栏/下拉框使用，展示历史会话列表。

    用法:
        GET /api/sessions
        GET /api/sessions?limit=10&offset=0

    返回:
        {
            "sessions": [
                {
                    "session_id": "a1b2c3d4-...",
                    "thread_id": "a1b2c3d4-...",
                    "created_at": 1704067200.0,
                    "updated_at": 1704067300.0,
                    "database_id": "california_schools",
                    "question_count": 3
                }
            ],
            "total": 1
        }
    """
    trace = TraceLogger()
    trace.log("获取会话列表", f"limit={limit}, offset={offset}")

    sessions_list, total = await session_manager.list_sessions(
        limit=limit, offset=offset
    )

    # 将原始 dict 转换为 SessionItem 模型
    items = []
    for s in sessions_list:
        items.append(SessionItem(
            session_id=s["session_id"],
            thread_id=s.get("thread_id", s["session_id"]),
            created_at=s["created_at"],
            updated_at=s.get("updated_at", s["created_at"]),
            database_id=s.get("database_id"),
            question_count=s.get("question_count", len(s.get("history", []))),
        ))

    trace.log("返回会话列表", f"共 {total} 个会话，返回 {len(items)} 条")
    return SessionListResponse(sessions=items, total=total)


@router.post("/sessions", response_model=SessionCreateResponse)
async def create_session(
    body: SessionCreateRequest | None = None,
    database_id: str | None = Query(None),
):
    """
    创建新的对话会话

    为多轮对话创建一个新会话。每次创建同时生成一个 LangGraph thread_id
    （与 session_id 相同），SqliteSaver 检查点机制据此持久化 Agent 状态。
    返回的 session_id 需在后续 /api/query 请求中传递。

    用法:
        POST /api/sessions
        Body: {"database_id": "california_schools"}  (可选)

    返回:
        {
            "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "thread_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "created_at": 1704067200.0,
            "database_id": "california_schools"
        }
    """
    trace = TraceLogger()
    requested_database_id = body.database_id if body is not None else database_id
    trace.log("创建会话", f"database_id={requested_database_id}")

    session_id = await session_manager.create_session(requested_database_id)
    session_data = await session_manager.get_session(session_id)

    trace.log("会话创建成功",
              f"session_id={session_id}, thread_id={session_data['thread_id']}")

    return SessionCreateResponse(
        session_id=session_id,
        thread_id=session_data["thread_id"],
        created_at=session_data["created_at"],
        database_id=session_data.get("database_id"),
    )


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session_detail(session_id: str):
    """
    获取会话详情（含历史记录）

    返回指定会话的完整信息，包括对话历史记录。
    前端在切换到某个历史会话时调用此接口，恢复对话上下文。

    用法:
        GET /api/sessions/a1b2c3d4-e5f6-7890-abcd-ef1234567890

    返回:
        {
            "session_id": "a1b2c3d4-...",
            "thread_id": "a1b2c3d4-...",
            "created_at": 1704067200.0,
            "updated_at": 1704067300.0,
            "database_id": "california_schools",
            "history": [
                {
                    "question": "加州哪个学校学生最多？",
                    "sql": "SELECT ...",
                    "answer": "..., 共有 1000 名学生",
                    "timestamp": 1704067250.0
                }
            ]
        }
    """
    trace = TraceLogger()
    trace.log("获取会话详情", f"session_id={session_id}")

    session_data = await session_manager.get_session(session_id)
    if session_data is None:
        trace.log("会话未找到", f"session_id={session_id}")
        raise HTTPException(
            status_code=404,
            detail=f"会话 '{session_id}' 未找到",
        )

    trace.log("返回会话详情",
              f"session_id={session_id}, history={len(session_data.get('history', []))}条")

    return SessionDetailResponse(
        session_id=session_data["session_id"],
        thread_id=session_data.get("thread_id", session_data["session_id"]),
        created_at=session_data["created_at"],
        updated_at=session_data.get("updated_at", session_data["created_at"]),
        database_id=session_data.get("database_id"),
        history=session_data.get("history", []),
    )


@router.patch("/sessions/{session_id}")
async def update_session(session_id: str, body: SessionUpdateRequest):
    """
    更新会话属性（如切换关联数据库）

    前端在用户切换数据库时调用，更新会话绑定的数据库 ID。

    用法:
        PATCH /api/sessions/a1b2c3d4-...
        Body: {"database_id": "debit_card_specializing"}

    返回:
        {"success": true, "message": "会话已更新"}
    """
    trace = TraceLogger()
    trace.log("更新会话", f"session_id={session_id}, database_id={body.database_id}")

    success = await session_manager.update_session(
        session_id=session_id,
        database_id=body.database_id,
    )
    if not success:
        trace.log("会话未找到", f"session_id={session_id}")
        raise HTTPException(
            status_code=404,
            detail=f"会话 '{session_id}' 未找到",
        )

    trace.log("会话更新成功", f"session_id={session_id}")
    return {"success": True, "message": "会话已更新"}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """
    删除指定的对话会话

    前端在用户关闭对话或清理历史时调用。
    删除会话会同时移除其对应的 LangGraph 检查点数据。

    用法:
        DELETE /api/sessions/a1b2c3d4-e5f6-7890-abcd-ef1234567890

    返回:
        {"success": true, "message": "会话已删除"}
    """
    trace = TraceLogger()
    trace.log("删除会话", f"session_id={session_id}")

    success = await session_manager.delete_session(session_id)
    if not success:
        trace.log("会话未找到", f"session_id={session_id}")
        raise HTTPException(
            status_code=404,
            detail=f"会话 '{session_id}' 未找到",
        )

    trace.log("会话删除成功", f"session_id={session_id}")
    return {
        "success": True,
        "message": "会话已删除",
    }


@router.post("/sessions/{session_id}/reset")
async def reset_session(session_id: str):
    """
    重置会话（清空历史记录，保留会话和 thread_id）

    前端在用户点击"新建对话"（但保持同一会话）时调用。
    清空历史后，后续 /api/query 请求会使用同一个 session_id，
    但不再携带之前的对话上下文。

    用法:
        POST /api/sessions/a1b2c3d4-e5f6-7890-abcd-ef1234567890/reset

    返回:
        {"success": true, "message": "会话已重置", "session_id": "..."}
    """
    trace = TraceLogger()
    trace.log("重置会话", f"session_id={session_id}")

    success = await session_manager.clear_history(session_id)
    if not success:
        trace.log("会话未找到", f"session_id={session_id}")
        raise HTTPException(
            status_code=404,
            detail=f"会话 '{session_id}' 未找到",
        )

    trace.log("会话重置成功", f"session_id={session_id}")
    return {
        "success": True,
        "message": "会话已重置",
        "session_id": session_id,
    }


@router.post("/query", response_model=QueryResponse)
async def execute_query(request: QueryRequest):
    """
    核心接口：自然语言查询转 SQL

    用户传入自然语言问题，系统经历以下流程:
    1. 创建 Trace 日志记录器
    2. 解析请求参数（question, database_id, session_id）
    3. 查找或创建会话（如果传了 session_id 则恢复历史）
    4. 调用 AgentGraph 工作流（graph.py）：
       - SemanticRetriever 获取数据库 schema 上下文
       - LLM 生成 SQL / ReAct 工具调用循环
       - SQLExecutor 执行并验证 SQL
       - ResultAnalyzer 生成中文解释
    5. 将结果存入会话历史
    6. 返回 QueryResponse（包含 SQL、数据、图表配置和中文解释）

    注意:
        调用 AgentGraph → ReActSqlAgent 完成 NL2SQL 全链路。

    用法:
        POST /api/query
        Body: {
            "question": "加州哪个学校的学生最多？",
            "database_id": "california_schools",
            "session_id": "a1b2c3d4-..."  (可选)
        }
    """
    trace = TraceLogger()
    trace.log("收到查询请求", f"question='{request.question}', database_id='{request.database_id}'")

    # ---------- 会话管理（与 LangGraph SqliteSaver 检查点协同） ----------
    session_id = request.session_id
    if session_id:
        session = await session_manager.get_session(session_id)
        if session is None:
            trace.log("会话未找到，创建新会话", f"session_id={session_id}")
            session_id = await session_manager.create_session(request.database_id)
        else:
            if session["database_id"] != request.database_id:
                await session_manager.update_session(
                    session_id, database_id=request.database_id
                )
                trace.log("更新会话数据库", f"new_database_id={request.database_id}")
    else:
        session_id = await session_manager.create_session(request.database_id)
        trace.log("创建新会话", f"session_id={session_id}")

    checkpoint_state = session_manager.load_agent_state(session_id)
    history = await session_manager.get_history(session_id) or []
    session_context = {"thread_id": session_manager.get_thread_id(session_id)}
    if checkpoint_state.get("database_id") == request.database_id:
        session_context.update({
            "last_question": checkpoint_state.get("question"),
            "last_sql": checkpoint_state.get("sql"),
        })
    elif history:
        last_item = history[-1]
        session_context.update({
            "last_question": last_item.get("question"),
            "last_sql": last_item.get("sql"),
        })

    # ---------- 调用 Agent 工作流 ----------
    try:
        # 使用 AgentGraph 执行完整的 NL2SQL 工作流
        result = await AgentGraph().ARun(
            question=request.question,
            database_id=request.database_id,
            session_context=session_context,
        )

        # 合并 API 层 Trace + Agent 层 Trace
        combined_trace = result.get("trace", []) + trace.get_logs()

        response = QueryResponse(
            answer=result["answer"],
            sql=result.get("sql"),
            columns=result.get("columns"),
            rows=result.get("rows"),
            chart=result.get("chart"),
            trace=combined_trace,
            error=result.get("error"),
        )
    except Exception as exc:
        trace.log("查询失败", str(exc), status="error")
        response = QueryResponse(
            answer="查询失败，请稍后重试或换一种问法。",
            sql=None,
            columns=[],
            rows=[],
            chart=None,
            trace=trace.get_logs(),
            error=str(exc),
        )

    # ---------- 保存会话历史 ----------
    await session_manager.append_history(
        session_id=session_id,
        question=request.question,
        sql=response.sql,
        answer=response.answer,
    )
    session_manager.save_agent_state(
        session_id,
        {
            "database_id": request.database_id,
            "question": request.question,
            "sql": response.sql,
            "answer": response.answer,
        },
    )

    trace.log("查询完成")
    return response


@router.post("/query/execute-sql", response_model=QueryResponse)
async def replay_sql(request: ExecuteSqlRequest):
    """Re-execute a saved read-only SQL statement for history restoration.

    The common query runner validates the SQL AST before execution, therefore
    this endpoint cannot be used to mutate the selected SQLite database.
    """
    databases = _scan_databases()
    database = next((item for item in databases if item["id"] == request.database_id), None)
    if database is None:
        raise HTTPException(status_code=404, detail=f"数据库 '{request.database_id}' 未找到")
    result = ExecuteSql(request.sql, database["path"])
    if not result["success"]:
        return QueryResponse(
            answer="",
            sql=request.sql,
            columns=[],
            rows=[],
            chart=None,
            trace=[],
            error=result["error"],
        )
    return QueryResponse(
        answer="",
        sql=request.sql,
        columns=result["columns"],
        rows=result["rows"],
        chart=None,
        trace=[],
        error=None,
    )
