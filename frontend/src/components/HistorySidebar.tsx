import { useEffect, useState } from "react";
import { useSessionStore } from "../store/sessionStore";
import { useQueryStore } from "../store/queryStore";

export default function HistorySidebar() {
  const [collapsed, setCollapsed] = useState(false);
  const { sessions, currentSessionId, loading, error, loadSessions, setCurrentSession, switchSession } = useSessionStore();
  const activeSessionId = useQueryStore((state) => state.sessionId);
  const turnCount = useQueryStore((state) => state.turns.length);

  useEffect(() => {
    setCurrentSession(activeSessionId);
    void loadSessions();
  }, [activeSessionId, turnCount, loadSessions, setCurrentSession]);

  return (
    <aside className={`history-sidebar ${collapsed ? "is-collapsed" : ""}`} aria-label="历史记录">
      <button
        type="button"
        className="history-sidebar__toggle"
        aria-label={collapsed ? "展开历史记录" : "折叠历史记录"}
        onClick={() => setCollapsed((value) => !value)}
      >
        {collapsed ? ">" : "<"}
      </button>
      {!collapsed ? (
        <div className="history-sidebar__content">
          <h2>历史记录</h2>
          {loading ? <p>加载中...</p> : null}
          {error ? <p role="alert">{error}</p> : null}
          {!loading && !error && !sessions.length ? <p>暂无历史记录</p> : null}
          {sessions.map((session) => (
            <button
              type="button"
              className={session.session_id === currentSessionId ? "is-active" : ""}
              key={session.session_id}
              onClick={() => void switchSession(session.session_id)}
            >
              <strong>{session.database_id || "未命名数据源"}</strong>
              <span>{session.question_count || 0} 个问题</span>
              <time dateTime={new Date(session.updated_at || session.created_at).toISOString()}>
                {new Date(session.updated_at || session.created_at).toLocaleString("zh-CN")}
              </time>
            </button>
          ))}
        </div>
      ) : null}
    </aside>
  );
}
