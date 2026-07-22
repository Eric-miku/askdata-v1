import { useEffect, useMemo, useRef, useState } from "react";
import type { DatabaseInfo, ThemeMode } from "../types/query";
import {
  CloseIcon,
  BookIcon,
  DatabaseIcon,
  MoonIcon,
  PlusIcon,
  SearchIcon,
  SunIcon,
} from "./Icons";

interface AppSidebarProps {
  theme: ThemeMode;
  databases: DatabaseInfo[];
  database: string;
  loading: boolean;
  databasesLoading: boolean;
  databaseError: string | null;
  onNewChat: () => void;
  onSelectDatabase: (databaseId: string) => void;
  onToggleTheme: () => void;
  onManageKnowledge?: () => void;
  onManageDataSources?: () => void;
}

export default function AppSidebar({
  theme,
  databases,
  database,
  loading,
  databasesLoading,
  databaseError,
  onNewChat,
  onSelectDatabase,
  onToggleTheme,
  onManageKnowledge,
  onManageDataSources,
}: AppSidebarProps) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [search, setSearch] = useState("");
  const databaseButtonRef = useRef<HTMLButtonElement>(null);
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

  const closeDrawer = () => {
    setDrawerOpen(false);
    queueMicrotask(() => databaseButtonRef.current?.focus());
  };

  useEffect(() => {
    if (!drawerOpen) {
      return;
    }
    searchInputRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeDrawer();
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
  }, [drawerOpen]);

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
          className={`app-rail__button ${drawerOpen ? "is-active" : ""}`}
          aria-label="打开数据库"
          aria-expanded={drawerOpen}
          disabled={loading}
          onClick={() => setDrawerOpen(true)}
        >
          <DatabaseIcon />
        </button>
        <button
          type="button"
          className="app-rail__button"
          aria-label="业务术语管理"
          disabled={loading}
          onClick={onManageKnowledge}
        >
          <BookIcon />
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

      {drawerOpen ? (
        <>
          <button
            type="button"
            className="database-drawer__scrim"
            aria-label="关闭数据库抽屉"
            onClick={closeDrawer}
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
                onClick={closeDrawer}
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
                    closeDrawer();
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
            <button type="button" className="database-drawer__manage" onClick={() => { closeDrawer(); onManageDataSources?.(); }}>管理数据源</button>
          </aside>
        </>
      ) : null}
    </>
  );
}
