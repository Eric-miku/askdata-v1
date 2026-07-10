"""
会话管理器 —— 管理多用户的多轮对话 Session

V1 阶段使用内存存储（Dict），每个 Session 包含:
  - session_id: UUID 字符串，作为会话唯一标识
  - created_at: 创建时间戳
  - database_id: 该会话关联的数据库（可选）
  - history: 对话历史列表，每条记录包含用户问题、生成的 SQL 和最终回答

线程安全说明:
  使用 asyncio.Lock 保护写操作（create/append/delete），
  确保在并发请求下不会出现数据竞争。
"""

import uuid
import time
from typing import Dict, List, Optional, Any
from asyncio import Lock


class SessionManager:
    """轻量级内存会话管理器

    用法:
        manager = SessionManager()
        sid = manager.create_session("california_schools")
        manager.append_history(sid, "用户问题", "系统回答")
        history = manager.get_history(sid)
    """

    def __init__(self):
        # _sessions: session_id -> session_data
        self._sessions: Dict[str, dict] = {}
        self._lock = Lock()

    async def create_session(self, database_id: Optional[str] = None) -> str:
        """创建一个新的会话

        Args:
            database_id: 可选的默认数据库 ID

        Returns:
            新生成的 session_id
        """
        async with self._lock:
            session_id = str(uuid.uuid4())
            self._sessions[session_id] = {
                "session_id": session_id,
                "created_at": time.time(),
                "database_id": database_id,
                "history": [],
            }
            return session_id

    async def get_session(self, session_id: str) -> Optional[dict]:
        """获取会话信息

        Args:
            session_id: 会话 ID

        Returns:
            会话数据字典，如果不存在返回 None
        """
        return self._sessions.get(session_id)

    async def delete_session(self, session_id: str) -> bool:
        """删除一个会话

        Args:
            session_id: 会话 ID

        Returns:
            是否成功删除
        """
        async with self._lock:
            return self._sessions.pop(session_id, None) is not None

    async def append_history(
        self,
        session_id: str,
        question: str,
        sql: Optional[str] = None,
        answer: str = "",
    ) -> bool:
        """向会话追加一条对话记录

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
            session["history"].append({
                "question": question,
                "sql": sql,
                "answer": answer,
                "timestamp": time.time(),
            })
            return True

    async def get_history(
        self, session_id: str
    ) -> Optional[List[Dict[str, Any]]]:
        """获取会话的历史记录

        Args:
            session_id: 会话 ID

        Returns:
            历史记录列表，会话不存在返回 None
        """
        session = self._sessions.get(session_id)
        if session is None:
            return None
        return session["history"]

    async def update_database(
        self, session_id: str, database_id: str
    ) -> bool:
        """更新会话关联的数据库

        Args:
            session_id: 会话 ID
            database_id: 新的数据库 ID

        Returns:
            是否成功更新
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            session["database_id"] = database_id
            return True


# 全局单例 —— 整个应用共享一个会话管理器
# 所有 API 路由都通过导入此全局变量来操作会话
session_manager = SessionManager()
