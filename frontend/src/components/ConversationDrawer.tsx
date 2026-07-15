import { useEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import type { SessionSummary } from "../types/query";
import { CloseIcon, HistoryIcon, SearchIcon } from "./Icons";

interface ConversationDrawerProps {
  open: boolean;
  sessions: SessionSummary[];
  activeSessionId: string | null;
  loading: boolean;
  error: string | null;
  triggerRef: RefObject<HTMLButtonElement>;
  onClose: () => void;
  onOpenSession: (sessionId: string) => void;
}

function sessionDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat("zh-CN", {
        month: "short",
        day: "numeric",
      }).format(date);
}

export default function ConversationDrawer({
  open,
  sessions,
  activeSessionId,
  loading,
  error,
  triggerRef,
  onClose,
  onOpenSession,
}: ConversationDrawerProps) {
  const [search, setSearch] = useState("");
  const searchInputRef = useRef<HTMLInputElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const filtered = useMemo(() => {
    const keyword = search.trim().toLocaleLowerCase();
    return keyword
      ? sessions.filter((session) =>
          `${session.title} ${session.database_id}`
            .toLocaleLowerCase()
            .includes(keyword),
        )
      : sessions;
  }, [search, sessions]);

  const closeDrawer = () => {
    onClose();
    queueMicrotask(() => triggerRef.current?.focus());
  };

  useEffect(() => {
    if (!open) {
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
  }, [open, onClose, triggerRef]);

  if (!open) {
    return null;
  }

  return (
    <>
      <button
        type="button"
        className="database-drawer__scrim"
        aria-label="关闭历史记录抽屉"
        onClick={closeDrawer}
      />
      <aside
        ref={drawerRef}
        className="database-drawer conversation-drawer"
        role="dialog"
        aria-label="历史记录"
        aria-modal="true"
      >
        <header className="database-drawer__header">
          <div>
            <strong>Conversations</strong>
            <span>继续之前的数据探索</span>
          </div>
          <button
            type="button"
            className="icon-button"
            aria-label="关闭历史记录"
            onClick={closeDrawer}
          >
            <CloseIcon />
          </button>
        </header>
        <label className="database-drawer__search">
          <SearchIcon />
          <span className="visually-hidden">搜索历史记录</span>
          <input
            ref={searchInputRef}
            type="search"
            aria-label="搜索历史记录"
            placeholder="搜索历史记录"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <div className="database-drawer__caption">最近的对话 · {sessions.length}</div>
        <div className="database-drawer__list" role="listbox" aria-label="历史对话">
          {loading ? <p className="muted-copy">正在加载历史记录…</p> : null}
          {error ? <p className="error-copy">{error}</p> : null}
          {!loading && !error && !sessions.length ? (
            <p className="muted-copy">还没有历史对话</p>
          ) : null}
          {!loading && !error && sessions.length > 0 && !filtered.length ? (
            <p className="muted-copy">没有匹配的历史对话</p>
          ) : null}
          {filtered.map((session) => (
            <button
              type="button"
              role="option"
              aria-selected={session.id === activeSessionId}
              className={`database-drawer__item conversation-drawer__item ${session.id === activeSessionId ? "is-selected" : ""}`}
              key={session.id}
              disabled={loading}
              onClick={() => {
                onOpenSession(session.id);
                closeDrawer();
              }}
            >
              <HistoryIcon />
              <span className="conversation-drawer__content">
                <strong>{session.title || "未命名对话"}</strong>
                <small>
                  {session.database_id} · {sessionDate(session.updated_at)}
                </small>
              </span>
            </button>
          ))}
        </div>
        <p className="database-drawer__footnote">历史对话会保留，直到你明确删除。</p>
      </aside>
    </>
  );
}
