import { useEffect, useMemo, useRef, useState } from "react";
import type { DatabaseInfo, SessionSummary, ThemeMode } from "../types/query";
import {
  CloseIcon,
  DatabaseIcon,
  HistoryIcon,
  MoonIcon,
  PlusIcon,
  SearchIcon,
  SunIcon,
} from "./Icons";
import ConversationDrawer from "./ConversationDrawer";

interface AppSidebarProps {
  theme: ThemeMode;
  databases: DatabaseInfo[];
  database: string;
  loading: boolean;
  databasesLoading: boolean;
  databaseError: string | null;
  sessions: SessionSummary[];
  activeSessionId: string | null;
  sessionsLoading: boolean;
  sessionsError: string | null;
  onNewChat: () => void;
  onOpenSession: (sessionId: string) => void;
  onSelectDatabase: (databaseId: string) => void;
  onToggleTheme: () => void;
}

export default function AppSidebar({
  theme,
  databases,
  database,
  loading,
  databasesLoading,
  databaseError,
  sessions,
  activeSessionId,
  sessionsLoading,
  sessionsError,
  onNewChat,
  onOpenSession,
  onSelectDatabase,
  onToggleTheme,
}: AppSidebarProps) {
  const [openDrawer, setOpenDrawer] = useState<"database" | "history" | null>(null);
  const [search, setSearch] = useState("");
  const databaseButtonRef = useRef<HTMLButtonElement>(null);
  const historyButtonRef = useRef<HTMLButtonElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const filtered = useMemo(() => {
    const keyword = search.trim().toLocaleLowerCase();
    return keyword
      ? databases.filter((item) =>
          `${item.name} ${item.id}`.toLocaleLowerCase().includes(keyword),
        )
      : databases;
  }, [databases, search]);

  const closeDatabaseDrawer = () => {
    setOpenDrawer(null);
    queueMicrotask(() => databaseButtonRef.current?.focus());
  };

  useEffect(() => {
    if (openDrawer !== "database") {
      return;
    }
    searchInputRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeDatabaseDrawer();
        return;
      }
      if (event.key === "Tab") {
        const focusable = Array.from(
          drawerRef.current?.querySelectorAll<HTMLElement>(
            'button:not([disabled]), input:not([disabled])',
          ) ?? [],
        );
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [openDrawer]);

  return (
    <>
      <aside className="app-rail" aria-label="主导航">
        <div className="app-rail__logo" aria-label="AskData">
          ✦
        </div>
        <button
          type="button"
          className="app-rail__button"
          aria-label="新建对话"
          disabled={loading}
          onClick={onNewChat}
        >
          <PlusIcon />
        </button>
        <button
          ref={databaseButtonRef}
          type="button"
          className={`app-rail__button ${openDrawer === "database" ? "is-active" : ""}`}
          aria-label="打开数据库"
          aria-expanded={openDrawer === "database"}
          disabled={loading}
          onClick={() => setOpenDrawer("database")}
        >
          <DatabaseIcon />
        </button>
        <button
          ref={historyButtonRef}
          type="button"
          className={`app-rail__button ${openDrawer === "history" ? "is-active" : ""}`}
          aria-label="打开历史记录"
          aria-expanded={openDrawer === "history"}
          disabled={loading}
          onClick={() => setOpenDrawer("history")}
        >
          <HistoryIcon />
        </button>
        <div className="app-rail__spacer" />
        <button
          type="button"
          className="app-rail__button"
          aria-label={theme === "dark" ? "切换到浅色主题" : "切换到深色主题"}
          onClick={onToggleTheme}
        >
          {theme === "dark" ? <SunIcon /> : <MoonIcon />}
        </button>
        <div className="app-rail__avatar" aria-label="当前用户 user">
          U
        </div>
      </aside>

      {openDrawer === "database" ? (
        <>
          <button
            type="button"
            className="database-drawer__scrim"
            aria-label="关闭数据库抽屉"
            onClick={closeDatabaseDrawer}
          />
          <aside
            ref={drawerRef}
            className="database-drawer"
            role="dialog"
            aria-label="数据库"
            aria-modal="true"
          >
            <header className="database-drawer__header">
              <div>
                <strong>Databases</strong>
                <span>选择本次对话使用的数据源</span>
              </div>
              <button
                type="button"
                className="icon-button"
                aria-label="关闭数据库"
                onClick={closeDatabaseDrawer}
              >
                <CloseIcon />
              </button>
            </header>
            <label className="database-drawer__search">
              <SearchIcon />
              <span className="visually-hidden">搜索数据库</span>
              <input
                ref={searchInputRef}
                type="search"
                aria-label="搜索数据库"
                placeholder="搜索数据库"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </label>
            <div className="database-drawer__caption">
              可访问的数据库 · {databases.length}
            </div>
            <div className="database-drawer__list" role="listbox" aria-label="可访问的数据库">
              {databasesLoading ? <p className="muted-copy">正在加载数据库…</p> : null}
              {databaseError ? <p className="error-copy">{databaseError}</p> : null}
              {!databasesLoading && !databaseError && !filtered.length ? (
                <p className="muted-copy">没有匹配的数据库</p>
              ) : null}
              {filtered.map((item) => (
                <button
                  type="button"
                  role="option"
                  aria-selected={item.id === database}
                  className={`database-drawer__item ${item.id === database ? "is-selected" : ""}`}
                  key={item.id}
                  disabled={loading}
                  onClick={() => {
                    onSelectDatabase(item.id);
                    closeDatabaseDrawer();
                  }}
                >
                  <DatabaseIcon />
                  <span className="database-drawer__name">{item.name || item.id}</span>
                  <small>{item.tables_count ?? "-"} 张表</small>
                </button>
              ))}
            </div>
            <p className="database-drawer__footnote">
              这里只显示当前账号有权访问的数据源。
            </p>
          </aside>
        </>
      ) : null}
      <ConversationDrawer
        open={openDrawer === "history"}
        sessions={sessions}
        activeSessionId={activeSessionId}
        loading={sessionsLoading}
        error={sessionsError}
        triggerRef={historyButtonRef}
        onClose={() => setOpenDrawer(null)}
        onOpenSession={onOpenSession}
      />
    </>
  );
}
