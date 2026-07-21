"""
会话管理器 —— 管理多用户的多轮对话 Session

核心升级（V2）:
  1. LangGraph SqliteSaver 检查点机制:
     每个会话对应一个 LangGraph thread_id（值 = session_id）。
     SqliteSaver 通过 SQLite 文件持久化 Agent 的每一步状态，
     使得 Agent 可以在多次调用之间保持多轮对话记忆。
     即使服务重启，也能从检查点文件恢复 Agent 状态。

  2. 内存 Dict 缓存:
     在检查点之上维护 Session 元数据（session_id, thread_id, database_id,
     created_at, updated_at, history），实现快速查询和列表展示。

  3. 新增方法:
     - list_sessions()     — 获取会话列表（分页、按时间排序）
     - update_session()    — 更新会话属性
     - clear_history()     — 清空历史记录（保留会话）
     - get_saver()         — 获取 SqliteSaver 实例
     - get_thread_id()     — 获取 LangGraph thread_id

线程安全:
    使用 asyncio.Lock 保护写操作，确保并发安全。
"""

import uuid
import time
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from asyncio import Lock


class SessionManager:
    """支持 LangGraph SqliteSaver 检查点机制的会话管理器

    SqliteSaver 工作方式:
        LangGraph Agent 在运行时通过 configurable={"thread_id": thread_id}
        指定会话线程。SqliteSaver 自动将每一步 Agent 状态（已执行的节点、
        中间变量、对话上下文等）序列化并写入 SQLite 检查点文件。
        下次相同 thread_id 的调用会自动从上次断点恢复。

    与 AgentGraph 的协作:
        routes.py 中 /api/query 调用 RunAgent 时，通过
        session_manager.get_thread_id(session_id) 获取 thread_id，
        传入 LangGraph 的 config 中，SqliteSaver 据此恢复/保存状态。

    Args:
        checkpoint_dir: 检查点文件存放目录，默认 .checkpoints/
    """

    def __init__(self, checkpoint_dir: Optional[str] = None):
        # 内存会话缓存: session_id -> { session_id, thread_id, created_at,
        #                              updated_at, database_id, history }
        self._sessions: Dict[str, dict] = {}
        self._lock = Lock()

        # 检查点文件目录
        if checkpoint_dir is None:
            self.checkpoint_dir = Path.cwd() / ".checkpoints"
        else:
            self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

        # SqliteSaver 实例缓存
        self._saver: Optional[Any] = None

        # ---------------------------------------------------------------
    # LangGraph SqliteSaver 集成
    # ---------------------------------------------------------------

    def get_saver(self):
        """获取 LangGraph SqliteSaver 实例（单例缓存）

        SqliteSaver 使用 SQLite 文件持久化 Agent 检查点:
          - Agent 每执行完一个节点，saver 自动保存当前状态
          - 按 thread_id 索引，支持按线程恢复

        langgraph-checkpoint-sqlite >= 3.0 中 from_conn_string 返回
        上下文管理器，需要用 with 语句或直接调用 __enter__ 获取实例。
        这里缓存的是进入上下文后的 SqliteSaver 实例。

        Returns:
            langgraph.checkpoint.sqlite.SqliteSaver 实例

        用法（在 AgentGraph 中）:
            saver = session_manager.get_saver()
            graph = graph.compile(checkpointer=saver)
            result = await graph.ainvoke(inputs, config={
                "configurable": {"thread_id": thread_id}
            })
        """
        if self._saver is None:
            from langgraph.checkpoint.sqlite import SqliteSaver

            checkpoint_path = self._get_checkpoint_path()
            # from_conn_string 返回上下文管理器，需进入上下文获取实例
            context_manager = SqliteSaver.from_conn_string(checkpoint_path)
            # 如果返回的是上下文管理器，进入它
            if hasattr(context_manager, '__enter__'):
                self._saver = context_manager.__enter__()
            else:
                self._saver = context_manager
        return self._saver

    def _get_checkpoint_path(self) -> str:
        """获取检查点 SQLite 文件的完整路径

        所有 thread_id 的检查点存入同一个 SQLite 文件，
        SqliteSaver 内部按 thread_id 自动区分不同会话的检查点。

        Returns:
            文件绝对路径字符串
        """
        return str(self.checkpoint_dir / "langgraph_checkpoints.sqlite")

    def get_thread_id(self, session_id: str) -> str:
        """获取会话对应的 LangGraph thread_id

        thread_id 与 session_id 保持一致，确保一一对应关系。
        LangGraph 按 thread_id 隔离不同会话的检查点数据。

        Args:
            session_id: 会话 ID

        Returns:
            LangGraph thread_id（值等于 session_id）
        """
        return session_id

    # ---------------------------------------------------------------
    # 会话 CRUD
    # ---------------------------------------------------------------

    async def create_session(self, database_id: Optional[str] = None) -> str:
        """创建一个新的会话（同时建立 LangGraph thread_id）

        生成 UUID 作为 session_id，thread_id 与之相同。
        在内存中创建会话元数据。

        Args:
            database_id: 可选的默认数据库 ID

        Returns:
            新生成的 session_id
        """
        async with self._lock:
            now = time.time()
            session_id = str(uuid.uuid4())
            self._sessions[session_id] = {
                "session_id": session_id,
                "thread_id": session_id,
                "created_at": now,
                "updated_at": now,
                "database_id": database_id,
                "history": [],
            }
            return session_id

    async def get_session(self, session_id: str) -> Optional[dict]:
        """获取会话元数据

        Args:
            session_id: 会话 ID

        Returns:
            会话数据字典（副本），包含 session_id, thread_id, created_at,
            updated_at, database_id, history；不存在返回 None
        """
        session = self._sessions.get(session_id)
        if session is None:
            return None
        return dict(session)

    async def list_sessions(
        self, limit: int = 50, offset: int = 0
    ) -> Tuple[List[dict], int]:
        """获取会话列表，按 updated_at 降序排列

        Args:
            limit: 返回条数上限（默认 50）
            offset: 分页偏移量（默认 0）

        Returns:
            (会话字典列表, 总数) 元组
        """
        async with self._lock:
            sorted_sessions = sorted(
                self._sessions.values(),
                key=lambda s: s.get("updated_at", s.get("created_at", 0)),
                reverse=True,
            )
            total = len(sorted_sessions)
            page = sorted_sessions[offset: offset + limit]
            return [dict(s) for s in page], total

    async def delete_session(self, session_id: str) -> bool:
        """删除一个会话（从内存缓存移除）

        Args:
            session_id: 会话 ID

        Returns:
            是否成功删除
        """
        async with self._lock:
            return self._sessions.pop(session_id, None) is not None

    async def update_session(
        self,
        session_id: str,
        database_id: Optional[str] = None,
    ) -> bool:
        """更新会话属性（目前支持更新 database_id），同时刷新 updated_at

        Args:
            session_id: 会话 ID
            database_id: 新的数据库 ID（None 表示不更新）

        Returns:
            是否成功更新
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            if database_id is not None:
                session["database_id"] = database_id
            session["updated_at"] = time.time()
            return True

    # ---------------------------------------------------------------
    # 历史记录管理
    # ---------------------------------------------------------------

    async def append_history(
        self,
        session_id: str,
        question: str,
        sql: Optional[str] = None,
        answer: str = "",
    ) -> bool:
        """向会话追加一条对话记录，并更新 updated_at

        Args:
            session_id: 会话 ID
            question: 用户问题
            sql: 生成的 SQL 语句
            answer: 系统的中文回答

        Returns:
            是否成功追加（False 表示会话不存在）
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            now = time.time()
            session["history"].append({
                "question": question,
                "sql": sql,
                "answer": answer,
                "timestamp": now,
            })
            session["updated_at"] = now
            return True

    async def get_history(
        self, session_id: str
    ) -> Optional[List[Dict[str, Any]]]:
        """获取会话的历史记录

        Args:
            session_id: 会话 ID

        Returns:
            历史记录列表（时间正序），会话不存在返回 None
        """
        session = self._sessions.get(session_id)
        if session is None:
            return None
        return list(session["history"])

    async def clear_history(self, session_id: str) -> bool:
        """清空会话的历史记录，但保留会话和 LangGraph thread_id

        用于"重置会话"功能：清空对话上下文，保留 session_id 不变。

        Args:
            session_id: 会话 ID

        Returns:
            是否成功清空
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            session["history"] = []
            session["updated_at"] = time.time()
            return True


# 全局单例 —— 整个应用共享一个会话管理器
# 默认检查点文件: .checkpoints/langgraph_checkpoints.sqlite
session_manager = SessionManager()