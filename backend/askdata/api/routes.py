"""
API 路由定义 —— 所有 HTTP 接口的入口

本文件实现了以下接口:
  1. GET  /api/metadata/databases        — 获取数据库列表
  2. GET  /api/metadata/{database_id}/tables — 获取表结构
  3. POST /api/sessions                  — 创建会话
  4. DELETE /api/sessions/{session_id}    — 删除会话
  5. POST /api/query                     — 自然语言查询（核心接口）

设计原则:
  - 每个接口都记录 Trace 日志
  - 异常统一由全局异常处理器捕获，返回一致的错误格式
  - 核心 /api/query 接口通过 AgentGraph → ReActSqlAgent 完成 NL2SQL 全链路
"""

import os
import glob
import sqlite3
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from askdata.api.schemas import QueryRequest, QueryResponse
from askdata.api.trace import TraceLogger
from askdata.api.session_manager import session_manager
from askdata.agent.graph import AgentGraph
from askdata.core.config import settings
from askdata.core.paths import project_path

# 创建 APIRouter 实例
# 在 app.py 中通过 app.include_router(router, prefix="/api") 注册
router = APIRouter()


# ============================================================
# 辅助函数
# ============================================================

def _CountTables(db_path: str) -> int:
    try:
        connection = sqlite3.connect(db_path)
        try:
            cursor = connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            return cursor.fetchone()[0]
        finally:
            connection.close()
    except sqlite3.Error:
        return 0


def _get_bird_databases_dir() -> str:
    """获取 BIRD 数据库文件存放目录的绝对路径

    settings.BIRD_DATA_DIR 是相对于项目根的路径配置（如 "../data/bird"），
    这里需要解析为绝对路径。

    Returns:
        databases 目录的绝对路径
    """
    return str(project_path(settings.BIRD_DATA_DIR) / "databases")


def _scan_databases() -> List[dict]:
    """扫描 data/bird/databases/ 下的所有 SQLite 数据库文件

    遍历目录，找到所有 .db 或 .sqlite 文件，返回数据库元信息列表。

    Returns:
        数据库列表，每项包含 id, name, tables_count
    """
    databases_dir = _get_bird_databases_dir()
    databases = []

    # 如果目录不存在，返回空列表
    if not os.path.exists(databases_dir):
        return databases

    # 查找所有 .db 文件
    db_files = glob.glob(os.path.join(databases_dir, "**", "*.db"), recursive=True)
    db_files += glob.glob(os.path.join(databases_dir, "**", "*.sqlite"), recursive=True)

    seen = set()
    for db_path in db_files:
        # 用文件名（不含扩展名）作为数据库 ID
        db_id = os.path.splitext(os.path.basename(db_path))[0]
        if db_id not in seen:
            seen.add(db_id)
            databases.append({
                "id": db_id,
                "name": db_id.replace("_", " ").title(),  # 将下划线转换为可读名称
                "path": db_path,
                "tables_count": _CountTables(db_path),
            })

    return databases


def _read_sqlite_tables(db_path: str) -> List[dict]:
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
            {"id": "california_schools", "name": "California Schools", "tables_count": 0},
            {"id": "debit_card_specializing", "name": "Debit Card Specializing", "tables_count": 0}
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

    注意:
        通过 sqlite3 直接读取数据库表结构。
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


@router.post("/sessions")
async def create_session(database_id: Optional[str] = None):
    """
    创建新的对话会话

    为多轮对话创建一个会话。前端在用户首次提问或切换数据库时调用。
    返回的 session_id 需要在后续的 /api/query 请求中传递。

    用法:
        POST /api/sessions
        Body: {"database_id": "california_schools"}  (可选)

    返回:
        {
            "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "created_at": 1704067200.0
        }
    """
    trace = TraceLogger()
    trace.log("创建会话", f"database_id={database_id}")

    session_id = await session_manager.create_session(database_id)

    trace.log("会话创建成功", f"session_id={session_id}")
    return {
        "session_id": session_id,
        "created_at": (await session_manager.get_session(session_id))["created_at"],
    }


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """
    删除指定的对话会话

    前端在用户关闭对话或切换数据库时调用，用于清理服务端保存的会话历史。

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


@router.post("/query", response_model=QueryResponse)
async def execute_query(request: QueryRequest):
    """
    核心接口：自然语言查询转 SQL

    这是系统的核心功能。用户传入自然语言问题，系统经历以下流程:
    1. 创建 Trace 日志记录器
    2. 解析请求参数（question, database_id, session_id）
    3. 查找或创建会话（如果传了 session_id 则恢复历史）
    4. 调用 Agent 工作流（等待 AI 组实现 agent/graph.py）
    5. 将结果存入会话历史
    6. 返回 QueryResponse（包含 SQL、数据、图表配置和中文解释）

    用法:
        POST /api/query
        Body: {
            "question": "加州哪个学校的学生最多？",
            "database_id": "california_schools",
            "session_id": "a1b2c3d4-..."  (可选)
        }

    注意:
        调用 AgentGraph → ReActSqlAgent 完成 NL2SQL 全链路。
    """
    trace = TraceLogger()
    trace.log("收到查询请求", f"question='{request.question}', database_id='{request.database_id}'")

    # ---------- 会话管理 ----------
    session_id = request.session_id
    if session_id:
        # 如果前端传了 session_id，查找是否存在
        session = await session_manager.get_session(session_id)
        if session is None:
            trace.log("会话未找到，创建新会话", f"session_id={session_id}")
            session_id = await session_manager.create_session(request.database_id)
        else:
            # 如果会话关联的数据库不同，更新它
            if session["database_id"] != request.database_id:
                await session_manager.update_database(session_id, request.database_id)
                trace.log("更新会话数据库", f"new_database_id={request.database_id}")
    else:
        # 没有 session_id，创建一个新会话
        session_id = await session_manager.create_session(request.database_id)
        trace.log("创建新会话", f"session_id={session_id}")

    history = await session_manager.get_history(session_id) or []
    session_context = {}
    if history:
        last_item = history[-1]
        session_context = {
            "last_question": last_item.get("question"),
            "last_sql": last_item.get("sql"),
        }

    try:
        result = await AgentGraph().ARun(
            question=request.question,
            database_id=request.database_id,
            session_context=session_context,
        )
        response = QueryResponse(
            answer=result["answer"],
            sql=result.get("sql"),
            columns=result.get("columns"),
            rows=result.get("rows"),
            chart=result.get("chart"),
            trace=result.get("trace", []),
            error=result.get("error"),
        )
    except Exception as exc:
        trace.log("查询失败", str(exc))
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

    trace.log("查询完成")
    return response
