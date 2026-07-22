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
import json
import secrets
import hashlib
import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from askdata.api.schemas import (
    QueryRequest,
    QueryResponse,
    ExecuteSqlRequest,
    ExportRequest,
    KnowledgeEntryRequest,
    KnowledgeBulkImportRequest,
    DataSourceRequest,
    DataSourceStatusRequest,
    PermissionPolicyRequest,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionItem,
    SessionListResponse,
    SessionDetailResponse,
    SessionUpdateRequest,
)
from askdata.api.trace import TraceLogger
from askdata.api.request_context import GetRequestId
from askdata.api.session_manager import session_manager
from askdata.agent.graph import AgentGraph
from askdata.agent.understanding import QuestionUnderstanding
from askdata.core.config import settings
from askdata.core.paths import project_path
from askdata.db.query_runner import Execute as ExecuteSql
from askdata.db.optimizer import ExplainSqliteQuery
from askdata.tools.analysis import StructuredAnalyzer
from askdata.tools.exporter import BuildCsv, BuildXlsx
from askdata.tools.visualization import ChartRecommender
from askdata.knowledge.store import knowledge_store
from askdata.data.source_store import data_source_store
from askdata.security.permissions import ResetSqlAuthorizer, SetSqlAuthorizer, permission_store

# 创建 APIRouter 实例
# 在 app.py 中通过 app.include_router(router, prefix="/api") 注册
router = APIRouter()
audit_logger = logging.getLogger("askdata.audit")


def _audit(event: str, **fields) -> None:
    audit_logger.info(json.dumps({
        "event": event, "request_id": GetRequestId(), **fields,
    }, ensure_ascii=False, default=str))


def _require_admin(x_admin_token: str | None = Header(None)) -> None:
    """Protect management mutations when an admin token is configured."""
    expected = settings.ADMIN_API_TOKEN
    if expected and (not x_admin_token or not secrets.compare_digest(x_admin_token, expected)):
        raise HTTPException(status_code=403, detail="管理员凭据无效")


def _enrich_result(question: str, columns: list | None, rows: list | None) -> dict:
    analyzer = StructuredAnalyzer()
    return {
        "chart": ChartRecommender().Recommend(question, columns, rows),
        "analysis": analyzer.Analyze(columns, rows),
        "suggestions": analyzer.Suggest(question, columns, rows),
    }


def _resolve_knowledge(question: str) -> tuple[str, str | None]:
    """Resolve published aliases; refuse conflicting metric definitions."""
    matches = []
    lowered = question.casefold()
    for entry in knowledge_store.list(status="published"):
        names = [entry["standard_name"], *entry.get("aliases", [])]
        if any(str(name).casefold() in lowered for name in names if name):
            matches.append(entry)
    by_name: dict[str, list[dict]] = {}
    for entry in matches:
        by_name.setdefault(entry["standard_name"], []).append(entry)
    conflicts = [name for name, entries in by_name.items() if len({(item.get("formula"), item.get("aggregation")) for item in entries}) > 1]
    if conflicts:
        return question, f"“{conflicts[0]}”存在多个已发布口径，请确认要使用的指标定义。"
    if not matches:
        return question, None
    context = "；".join(
        f"{entry['standard_name']}={entry.get('definition') or entry.get('formula') or '按已发布口径'}"
        for entry in matches
    )
    return f"{question}\n业务术语口径（仅作 Schema/SQL 参考）：{context}", None


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

    managed = {item["id"]: item for item in data_source_store.list()}
    managed_by_path = {str(Path(item["path"]).resolve()): item for item in managed.values()}
    disabled_paths = {str(Path(item["path"]).resolve()) for item in managed.values() if not item["enabled"]}
    seen = set()
    for db_path in db_files:
        if str(db_path.resolve()) in disabled_paths:
            continue
        managed_item = managed_by_path.get(str(db_path.resolve()))
        db_id = managed_item["id"] if managed_item else db_path.stem
        if db_id not in seen:
            seen.add(db_id)
            databases.append({
                "id": db_id,
                "name": managed_item["name"] if managed_item else db_id.replace("_", " ").title(),
                "path": str(db_path),           # 转为 str，保持类型一致
                "tables_count": _CountTables(str(db_path)),  # 使用高效 COUNT 查询
            })

    for item in managed.values():
        resolved = Path(item["path"])
        if item["enabled"] and item["id"] not in seen and resolved.is_file():
            databases.append({
                "id": item["id"], "name": item["name"], "path": str(resolved),
                "tables_count": item["table_count"] or _CountTables(str(resolved)),
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
async def list_databases(x_user_id: str = Header("local-user")):
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

    databases = [
        database for database in _scan_databases()
        if permission_store.database_allowed(x_user_id, database["id"])
    ]

    trace.log("扫描完成", f"找到 {len(databases)} 个数据库")
    return [{key: value for key, value in database.items() if key != "path"} for database in databases]


@router.get("/metadata/{database_id}/tables")
async def get_tables(database_id: str, x_user_id: str = Header("local-user")):
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

    if not permission_store.database_allowed(x_user_id, database_id):
        raise HTTPException(status_code=403, detail="用户无权访问该数据源")

    try:
        tables = []
        for table in _read_sqlite_tables(db_info["path"]):
            table_name = table["table_name"]
            if not permission_store.table_allowed(x_user_id, database_id, table_name):
                continue
            table["columns"] = [
                column for column in table["columns"]
                if permission_store.field_allowed(x_user_id, database_id, table_name, column["name"])
            ]
            tables.append(table)
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
    x_user_id: str = Header("local-user"),
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
        limit=limit, offset=offset, user_id=x_user_id
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
    x_user_id: str = Header("local-user"),
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

    session_id = await session_manager.create_session(requested_database_id, x_user_id)
    session_data = await session_manager.get_session(session_id, x_user_id)

    trace.log("会话创建成功",
              f"session_id={session_id}, thread_id={session_data['thread_id']}")

    return SessionCreateResponse(
        session_id=session_id,
        thread_id=session_data["thread_id"],
        created_at=session_data["created_at"],
        database_id=session_data.get("database_id"),
    )


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session_detail(session_id: str, x_user_id: str = Header("local-user")):
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

    session_data = await session_manager.get_session(session_id, x_user_id)
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
async def update_session(session_id: str, body: SessionUpdateRequest, x_user_id: str = Header("local-user")):
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
        user_id=x_user_id,
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
async def delete_session(session_id: str, x_user_id: str = Header("local-user")):
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

    success = await session_manager.delete_session(session_id, x_user_id)
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
async def reset_session(session_id: str, x_user_id: str = Header("local-user")):
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

    success = await session_manager.clear_history(session_id, x_user_id)
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
async def execute_query(request: QueryRequest, x_user_id: str = Header("local-user")):
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
    started = time.perf_counter()
    trace = TraceLogger()
    trace.log("收到查询请求", f"question='{request.question}', database_id='{request.database_id}'")

    if not permission_store.database_allowed(x_user_id, request.database_id):
        raise HTTPException(status_code=403, detail="用户无权访问该数据源")

    # ---------- 会话管理（与 LangGraph SqliteSaver 检查点协同） ----------
    session_id = request.session_id
    database_changed = False
    if session_id:
        session = await session_manager.get_session(session_id, x_user_id)
        if session is None:
            trace.log("会话未找到，创建新会话", f"session_id={session_id}")
            session_id = await session_manager.create_session(request.database_id, x_user_id)
        else:
            if session["database_id"] != request.database_id:
                database_changed = True
                await session_manager.update_session(
                    session_id, database_id=request.database_id, user_id=x_user_id
                )
                trace.log("更新会话数据库", f"new_database_id={request.database_id}")
    else:
        session_id = await session_manager.create_session(request.database_id, x_user_id)
        trace.log("创建新会话", f"session_id={session_id}")

    checkpoint_state = session_manager.load_agent_state(session_id)
    history = await session_manager.get_history(session_id, x_user_id) or []
    session_context = {"thread_id": session_manager.get_thread_id(session_id)}
    if checkpoint_state.get("database_id") == request.database_id:
        session_context.update({
            "last_question": checkpoint_state.get("question"),
            "last_sql": checkpoint_state.get("sql"),
        })
    elif history and not database_changed and not checkpoint_state.get("database_id"):
        last_item = history[-1]
        session_context.update({
            "last_question": last_item.get("question"),
            "last_sql": last_item.get("sql"),
        })

    previous_understanding = (
        checkpoint_state.get("understanding")
        if checkpoint_state.get("database_id") == request.database_id
        else None
    )
    understanding = QuestionUnderstanding().Resolve(request.question, previous_understanding)
    session_context["understanding"] = understanding

    # ---------- 调用 Agent 工作流 ----------
    try:
        resolved_question, clarification = _resolve_knowledge(request.question)
        if clarification:
            result = {
                "answer": clarification,
                "sql": None,
                "columns": [],
                "rows": [],
                "trace": [{"step": "ResolveBusinessTerms", "status": "clarification", "message": clarification}],
                "error": None,
            }
        else:
            authorizer_token = SetSqlAuthorizer(
                lambda sql, mode: permission_store.prepare_sql(
                    x_user_id, request.database_id, sql, mode
                )
            )
            try:
                result = await AgentGraph().ARun(
                    question=resolved_question,
                    database_id=request.database_id,
                    session_context=session_context,
                )
            finally:
                ResetSqlAuthorizer(authorizer_token)

        # 合并 API 层 Trace + Agent 层 Trace
        combined_trace = [{
            "step": "UnderstandQuestion",
            "status": "success",
            "message": json.dumps(understanding, ensure_ascii=False),
        }] + result.get("trace", []) + trace.get_logs()

        enrichment = _enrich_result(request.question, result.get("columns"), result.get("rows"))
        response = QueryResponse(
            answer=result["answer"],
            sql=result.get("sql"),
            columns=result.get("columns"),
            rows=result.get("rows"),
            chart=enrichment["chart"],
            analysis=enrichment["analysis"],
            suggestions=enrichment["suggestions"],
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
        user_id=x_user_id,
    )
    session_manager.save_agent_state(
        session_id,
        {
            "database_id": request.database_id,
            "question": request.question,
            "sql": response.sql,
            "answer": response.answer,
            "understanding": understanding,
        },
    )

    trace.log("查询完成")
    sql_hash = hashlib.sha256(response.sql.encode()).hexdigest() if response.sql else None
    retry_count = sum(
        1 for item in response.trace
        if isinstance(item, dict) and item.get("status") == "retry"
    )
    _audit(
        "query_completed", user_id=x_user_id, session_id=session_id,
        database_id=request.database_id, trace_id=trace.get_trace_id(),
        sql_hash=sql_hash, row_count=len(response.rows or []), retry_count=retry_count,
        elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
        error_code="QUERY_FAILED" if response.error else None,
    )
    return response


@router.post("/query/execute-sql", response_model=QueryResponse)
async def replay_sql(request: ExecuteSqlRequest, x_user_id: str = Header("local-user")):
    """Re-execute a saved read-only SQL statement for history restoration.

    The common query runner validates the SQL AST before execution, therefore
    this endpoint cannot be used to mutate the selected SQLite database.
    """
    databases = [
        database for database in _scan_databases()
        if permission_store.database_allowed(x_user_id, database["id"])
    ]
    database = next((item for item in databases if item["id"] == request.database_id), None)
    if database is None:
        raise HTTPException(status_code=404, detail=f"数据库 '{request.database_id}' 未找到")
    authorizer_token = SetSqlAuthorizer(
        lambda sql, mode: permission_store.prepare_sql(x_user_id, request.database_id, sql, mode)
    )
    try:
        result = ExecuteSql(request.sql, database["path"])
    finally:
        ResetSqlAuthorizer(authorizer_token)
    if not result["success"]:
        _audit(
            "query_replay_failed", user_id=x_user_id, database_id=request.database_id,
            sql_hash=hashlib.sha256(request.sql.encode()).hexdigest(),
            error_code=result.get("error_code"),
        )
        return QueryResponse(
            answer="",
            sql=request.sql,
            columns=[],
            rows=[],
            chart=None,
            trace=[],
            error=result["error"],
        )
    enrichment = _enrich_result("", result["columns"], result["rows"])
    response = QueryResponse(
        answer="",
        sql=request.sql,
        columns=result["columns"],
        rows=result["rows"],
        chart=enrichment["chart"],
        analysis=enrichment["analysis"],
        suggestions=enrichment["suggestions"],
        trace=[],
        error=None,
    )
    _audit(
        "query_replayed", user_id=x_user_id, database_id=request.database_id,
        sql_hash=hashlib.sha256(request.sql.encode()).hexdigest(),
        row_count=len(result["rows"]), elapsed_ms=result.get("elapsed_ms"),
    )
    return response


@router.post("/query/explain")
async def explain_query(request: ExecuteSqlRequest, x_user_id: str = Header("local-user")):
    """Inspect a validated, authorized query without executing its result rows."""
    database = next((item for item in _scan_databases() if item["id"] == request.database_id), None)
    if database is None or not permission_store.database_allowed(x_user_id, request.database_id):
        raise HTTPException(status_code=404, detail=f"数据库 '{request.database_id}' 未找到")
    allowed, reason, executable_sql = permission_store.prepare_sql(
        x_user_id, request.database_id, request.sql, "query"
    )
    if not allowed:
        raise HTTPException(status_code=403, detail=reason or "用户无权分析该 SQL")
    result = ExplainSqliteQuery(executable_sql, database["path"])
    if result.get("success"):
        result["normalized_sql"] = request.sql
    _audit(
        "query_explained",
        user_id=x_user_id,
        database_id=request.database_id,
        sql_hash=hashlib.sha256(request.sql.encode()).hexdigest(),
        success=result["success"],
        error_code=result.get("error_code"),
        suggestion_count=len(result.get("suggestions", [])),
    )
    if not result["success"]:
        status_code = 400 if result.get("error_code") == "SQL_BLOCKED" else 422
        raise HTTPException(status_code=status_code, detail=result["error"])
    return result


@router.post("/query/export")
async def export_query(request: ExportRequest, x_user_id: str = Header("local-user")):
    """Re-run validated read-only SQL and export the trusted result snapshot."""
    database = next((item for item in _scan_databases() if item["id"] == request.database_id), None)
    if database is None:
        raise HTTPException(status_code=404, detail=f"数据库 '{request.database_id}' 未找到")
    authorizer_token = SetSqlAuthorizer(
        lambda sql, mode: permission_store.prepare_sql(x_user_id, request.database_id, sql, mode)
    )
    try:
        result = ExecuteSql(request.sql, database["path"], page_size=10_000, access_mode="export")
    finally:
        ResetSqlAuthorizer(authorizer_token)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    if request.format == "csv":
        content = BuildCsv(result["columns"], result["rows"])
        media_type, filename = "text/csv; charset=utf-8", "askdata-result.csv"
    else:
        content = BuildXlsx(request.question, request.sql, request.database_id, result["columns"], result["rows"])
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = "askdata-result.xlsx"
    _audit(
        "query_export", user_id=x_user_id, database_id=request.database_id, format=request.format,
        row_count=len(result["rows"]), sql_hash=hashlib.sha256(request.sql.encode()).hexdigest(),
    )
    return Response(content=content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/permissions")
async def list_permission_policies(user_id: str | None = None, _admin: None = Depends(_require_admin)):
    return {"policies": permission_store.list(user_id)}


@router.post("/permissions")
async def save_permission_policy(body: PermissionPolicyRequest, _admin: None = Depends(_require_admin)):
    policy = permission_store.save(body.model_dump())
    _audit(
        "permission_granted", policy_id=policy["id"], user_id=policy["user_id"],
        database_id=policy["database_id"], table_name=policy["table_name"],
        field_name=policy["field_name"], can_query=policy["can_query"],
        can_export=policy["can_export"], has_row_filter=bool(policy.get("row_filter")),
    )
    return policy


@router.delete("/permissions/{policy_id}")
async def delete_permission_policy(policy_id: str, _admin: None = Depends(_require_admin)):
    policy = next((item for item in permission_store.list() if item["id"] == policy_id), None)
    if policy is None or not permission_store.delete(policy_id):
        raise HTTPException(status_code=404, detail="权限策略不存在")
    _audit(
        "permission_revoked", policy_id=policy_id, user_id=policy["user_id"],
        database_id=policy["database_id"], table_name=policy["table_name"],
        field_name=policy["field_name"],
    )
    return {"success": True}


@router.get("/knowledge/entries")
async def list_knowledge_entries(kind: str | None = None, search: str | None = None, status: str | None = None):
    return {"entries": knowledge_store.list(kind=kind, search=search, status=status)}


@router.get("/knowledge/export")
async def export_knowledge_entries(format: str = Query("json", pattern="^(json|csv)$"), _admin: None = Depends(_require_admin)):
    entries = knowledge_store.list()
    if format == "csv":
        columns = [
            "id", "kind", "standard_name", "definition", "category", "scope", "status",
            "aliases", "mappings", "formula", "aggregation", "unit", "time_field",
            "examples", "version", "changelog", "updated_by", "updated_at",
        ]
        rows = [
            {
                **entry,
                "aliases": json.dumps(entry.get("aliases", []), ensure_ascii=False),
                "mappings": json.dumps(entry.get("mappings", []), ensure_ascii=False),
                "examples": json.dumps(entry.get("examples", []), ensure_ascii=False),
            }
            for entry in entries
        ]
        content = BuildCsv(columns, rows)
        media_type, filename = "text/csv; charset=utf-8", "askdata-knowledge.csv"
    else:
        content = json.dumps({"entries": entries}, ensure_ascii=False, indent=2).encode("utf-8")
        media_type, filename = "application/json; charset=utf-8", "askdata-knowledge.json"
    _audit("knowledge_exported", format=format, entry_count=len(entries))
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/knowledge/import")
async def import_knowledge_entries(body: KnowledgeBulkImportRequest, _admin: None = Depends(_require_admin)):
    existing = {
        (entry["kind"].casefold(), entry["standard_name"].casefold()): entry
        for entry in knowledge_store.list()
    }
    imported, errors = [], []
    for index, raw_entry in enumerate(body.entries):
        try:
            validated = KnowledgeEntryRequest.model_validate(raw_entry).model_dump()
            validated["status"] = "draft"
            validated["changelog"] = validated.get("changelog") or "批量导入"
            key = (validated["kind"].casefold(), validated["standard_name"].casefold())
            entry_id = existing.get(key, {}).get("id") if body.mode == "upsert" else None
            saved = knowledge_store.save(validated, entry_id=entry_id, updated_by="bulk-import")
            existing[key] = saved
            imported.append(saved)
        except Exception as exc:
            errors.append({"index": index, "standard_name": raw_entry.get("standard_name"), "error": str(exc)})
    _audit(
        "knowledge_imported", mode=body.mode, requested=len(body.entries),
        imported=len(imported), failed=len(errors),
    )
    return {
        "requested": len(body.entries), "imported": len(imported),
        "failed": len(errors), "entries": imported, "errors": errors,
    }


@router.post("/knowledge/entries")
async def create_knowledge_entry(body: KnowledgeEntryRequest, _admin: None = Depends(_require_admin)):
    entry = knowledge_store.save(body.model_dump(), updated_by="api-user")
    _audit("knowledge_created", entry_id=entry["id"], kind=entry["kind"])
    return entry


@router.get("/knowledge/entries/{entry_id}")
async def get_knowledge_entry(entry_id: str):
    entry = knowledge_store.get(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="术语或指标不存在")
    return entry


@router.get("/knowledge/entries/{entry_id}/versions")
async def list_knowledge_versions(entry_id: str):
    versions = knowledge_store.list_versions(entry_id)
    if not versions:
        raise HTTPException(status_code=404, detail="术语或指标不存在")
    return {"versions": versions}


@router.put("/knowledge/entries/{entry_id}")
async def update_knowledge_entry(entry_id: str, body: KnowledgeEntryRequest, _admin: None = Depends(_require_admin)):
    if knowledge_store.get(entry_id) is None:
        raise HTTPException(status_code=404, detail="术语或指标不存在")
    entry = knowledge_store.save(body.model_dump(), entry_id=entry_id, updated_by="api-user")
    _audit("knowledge_updated", entry_id=entry_id, version=entry["version"])
    return entry


@router.delete("/knowledge/entries/{entry_id}")
async def delete_knowledge_entry(entry_id: str, _admin: None = Depends(_require_admin)):
    if not knowledge_store.delete(entry_id):
        raise HTTPException(status_code=404, detail="术语或指标不存在")
    _audit("knowledge_deleted", entry_id=entry_id)
    return {"success": True}


@router.post("/knowledge/entries/{entry_id}/publish")
async def publish_knowledge_entry(entry_id: str, _admin: None = Depends(_require_admin)):
    entry = knowledge_store.get(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="术语或指标不存在")
    for mapping in entry.get("mappings", []):
        database_id, table_name, field_name = mapping.get("database_id"), mapping.get("table"), mapping.get("field")
        if not database_id or not table_name or not field_name:
            continue
        database = next((item for item in _scan_databases() if item["id"] == database_id), None)
        if database is None or not any(table["table_name"] == table_name and any(column["name"] == field_name for column in table["columns"]) for table in _read_sqlite_tables(database["path"])):
            raise HTTPException(status_code=400, detail=f"映射字段不存在: {database_id}.{table_name}.{field_name}")
    payload = {**entry, "status": "published", "changelog": "发布"}
    published = knowledge_store.save(payload, entry_id=entry_id, updated_by="api-user")
    _audit("knowledge_published", entry_id=entry_id, version=published["version"])
    return published


@router.post("/knowledge/entries/{entry_id}/rollback/{version}")
async def rollback_knowledge_entry(entry_id: str, version: int, _admin: None = Depends(_require_admin)):
    entry = knowledge_store.rollback(entry_id, version)
    if entry is None:
        raise HTTPException(status_code=404, detail="指定版本不存在")
    _audit("knowledge_rolled_back", entry_id=entry_id, target_version=version, new_version=entry["version"])
    return entry


def _resolve_managed_sqlite_path(value: str) -> Path:
    base = _get_bird_databases_dir().resolve()
    candidate = Path(value)
    resolved = candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()
    if base not in resolved.parents and resolved != base:
        raise HTTPException(status_code=400, detail="数据源路径必须位于受控 BIRD databases 目录中")
    if not resolved.is_file() or resolved.suffix.lower() not in {".db", ".sqlite"}:
        raise HTTPException(status_code=400, detail="SQLite 数据库文件不存在或格式不受支持")
    return resolved


@router.get("/data-sources")
async def list_managed_data_sources(_admin: None = Depends(_require_admin)):
    return {"data_sources": data_source_store.list()}


@router.post("/data-sources")
async def create_managed_data_source(body: DataSourceRequest, _admin: None = Depends(_require_admin)):
    path = _resolve_managed_sqlite_path(body.path)
    if data_source_store.get(body.id):
        raise HTTPException(status_code=409, detail="数据源 ID 已存在")
    source = data_source_store.save(body.id, body.name, str(path), body.enabled)
    _audit("data_source_created", source_id=body.id, kind="sqlite")
    return source


@router.put("/data-sources/{source_id}")
async def update_managed_data_source(source_id: str, body: DataSourceRequest, _admin: None = Depends(_require_admin)):
    if data_source_store.get(source_id) is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    path = _resolve_managed_sqlite_path(body.path)
    return data_source_store.save(source_id, body.name, str(path), body.enabled)


@router.patch("/data-sources/{source_id}/status")
async def set_managed_data_source_status(source_id: str, body: DataSourceStatusRequest, _admin: None = Depends(_require_admin)):
    source = data_source_store.set_enabled(source_id, body.enabled)
    if source is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    _audit("data_source_status_changed", source_id=source_id, enabled=body.enabled)
    return source


@router.post("/data-sources/{source_id}/test")
async def test_managed_data_source(source_id: str, _admin: None = Depends(_require_admin)):
    source = data_source_store.check(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    return source


@router.post("/data-sources/{source_id}/sync")
async def sync_managed_data_source(source_id: str, _admin: None = Depends(_require_admin)):
    source = data_source_store.mark_synced(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    if source["health"] != "healthy":
        raise HTTPException(status_code=400, detail=source["last_error"] or "数据源连接失败")
    _audit("data_source_schema_synced", source_id=source_id, table_count=source["table_count"])
    return source


@router.get("/data-sources/{source_id}/schema")
async def get_managed_data_source_schema(source_id: str, _admin: None = Depends(_require_admin)):
    if data_source_store.get(source_id) is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    snapshot = data_source_store.catalog(source_id)
    if snapshot is None:
        raise HTTPException(status_code=409, detail="数据源尚未同步 Schema")
    return snapshot


@router.delete("/data-sources/{source_id}")
async def delete_managed_data_source(source_id: str, _admin: None = Depends(_require_admin)):
    if not data_source_store.delete(source_id):
        raise HTTPException(status_code=404, detail="数据源不存在")
    _audit("data_source_deleted", source_id=source_id)
    return {"success": True}
