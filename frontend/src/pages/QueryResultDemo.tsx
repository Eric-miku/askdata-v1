import { useEffect, useState } from "react";
import AppSidebar from "../components/AppSidebar";
import ChatComposer from "../components/ChatComposer";
import { DatabaseIcon } from "../components/Icons";
import { QueryResultView } from "../components/QueryResultView";
import KnowledgeManager from "../components/KnowledgeManager";
import DataSourceManager from "../components/DataSourceManager";
import type { ThemeMode } from "../types/query";
import { useQueryStore } from "../store/queryStore";

interface QueryResultDemoProps {
  theme: ThemeMode;
  onToggleTheme: () => void;
}

export function QueryResultDemo({ theme, onToggleTheme }: QueryResultDemoProps) {
  const [knowledgeOpen, setKnowledgeOpen] = useState(false);
  const [dataSourcesOpen, setDataSourcesOpen] = useState(false);
  const store = useQueryStore();
  const selectedDatabase = store.databases.find((item) => item.id === store.database);

  useEffect(() => { void store.loadDatabases(); }, [store.loadDatabases]);

  return (
    <div className="query-demo">
      <AppSidebar
        theme={theme}
        databases={store.databases}
        database={store.database}
        loading={store.loading}
        databasesLoading={store.databasesLoading}
        databaseError={store.databaseError}
        onNewChat={() => void store.newChat()}
        onSelectDatabase={(database) => void store.selectDatabase(database)}
        onToggleTheme={onToggleTheme}
        onManageKnowledge={() => setKnowledgeOpen(true)}
        onManageDataSources={() => setDataSourcesOpen(true)}
      />
      <KnowledgeManager open={knowledgeOpen} onClose={() => setKnowledgeOpen(false)} />
      <DataSourceManager open={dataSourcesOpen} onClose={() => setDataSourcesOpen(false)} onChanged={() => void store.loadDatabases()} />
      <main className="chat-workspace">
        <header className="workspace-header">
          <div className="workspace-header__title">
            <span className={`connection-dot ${store.database ? "is-connected" : ""}`} />
            <strong>AskData</strong>
            <span>智能问数</span>
          </div>
          <div className="workspace-header__database">
            <DatabaseIcon />
            <span>{selectedDatabase?.name || store.database || "未选择数据库"}</span>
          </div>
        </header>
        {store.databaseError ? (
          <div className="workspace-alert" role="alert">
            <strong>数据库加载失败</strong>
            <span>{store.databaseError}</span>
          </div>
        ) : null}
        {store.turns.length ? (
          <section className="conversation" aria-live="polite">
            {store.turns.map((turn) => (
              <QueryResultView
                key={turn.id}
                turn={turn}
                onRetry={(turnId) => void store.retryTurn(turnId)}
                onSuggestion={(question) => void store.sendMessage(question)}
              />
            ))}
          </section>
        ) : (
          <section className="welcome-panel" aria-live="polite">
            <div className="welcome-panel__content">
              <h1>Hi, user.</h1>
            </div>
          </section>
        )}
        <div className="composer-dock">
          <ChatComposer
            database={store.database}
            databases={store.databases}
            loading={store.loading}
            validationError={store.validationError}
            onDatabaseChange={(database) => void store.selectDatabase(database)}
            onSubmit={(question) => store.sendMessage(question)}
          />
        </div>
      </main>
    </div>
  );
}
