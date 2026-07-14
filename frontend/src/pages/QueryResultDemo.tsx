import { useEffect, useMemo, useRef } from "react";
import AppSidebar from "../components/AppSidebar";
import ChatComposer from "../components/ChatComposer";
import { DatabaseIcon } from "../components/Icons";
import { QueryResultView } from "../components/QueryResultView";
import { useQueryStore } from "../store/queryStore";
import type { ThemeMode } from "../types/query";

interface QueryResultDemoProps {
  theme: ThemeMode;
  onToggleTheme: () => void;
}

export function QueryResultDemo({ theme, onToggleTheme }: QueryResultDemoProps) {
  const {
    database,
    databases,
    databasesLoading,
    databaseError,
    turns,
    loading,
    validationError,
    loadDatabases,
    selectDatabase,
    newChat,
    sendMessage,
    retryTurn,
  } = useQueryStore();
  const endRef = useRef<HTMLDivElement>(null);
  const selectedDatabase = useMemo(
    () => databases.find((item) => item.id === database),
    [database, databases],
  );

  useEffect(() => {
    void loadDatabases();
  }, [loadDatabases]);

  useEffect(() => {
    if (turns.length) {
      endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [turns]);

  const composer = (
    <ChatComposer
      database={database}
      databases={databases}
      loading={loading}
      validationError={validationError}
      onDatabaseChange={(databaseId) => void selectDatabase(databaseId)}
      onSubmit={sendMessage}
    />
  );

  return (
    <div className="app-layout">
      <AppSidebar
        theme={theme}
        databases={databases}
        database={database}
        loading={loading}
        databasesLoading={databasesLoading}
        databaseError={databaseError}
        onNewChat={() => void newChat()}
        onSelectDatabase={(databaseId) => void selectDatabase(databaseId)}
        onToggleTheme={onToggleTheme}
      />

      <main className={`chat-workspace ${turns.length ? "has-conversation" : "is-empty"}`}>
        <header className="workspace-header">
          <div className="workspace-header__title">
            <strong>AskData</strong>
            <span>/</span>
            <span>{turns.length ? "当前对话" : "New query"}</span>
          </div>
          <div className="workspace-header__database">
            <span className={`connection-dot ${database ? "is-connected" : ""}`} />
            <DatabaseIcon />
            <span>{selectedDatabase?.name || (databasesLoading ? "加载中…" : "未选择数据库")}</span>
          </div>
        </header>

        {databaseError ? (
          <div className="workspace-alert" role="alert">
            <strong>数据库列表加载失败</strong>
            <span>{databaseError}</span>
            <button type="button" onClick={() => void loadDatabases()}>
              重新加载
            </button>
          </div>
        ) : null}

        {!turns.length ? (
          <section className="welcome-panel">
            <div className="welcome-panel__content">
              <h1>
                <span>✦</span> Hi, user.
              </h1>
              {composer}
            </div>
          </section>
        ) : (
          <>
            <section className="conversation" aria-live="polite">
              {turns.map((turn) => (
                <QueryResultView key={turn.id} turn={turn} onRetry={retryTurn} />
              ))}
              <div ref={endRef} />
            </section>
            <div className="composer-dock">{composer}</div>
          </>
        )}
      </main>
    </div>
  );
}
