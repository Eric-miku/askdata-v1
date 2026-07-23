import { create } from "zustand";
import { getSession, listSessions } from "../api/query";
import type { SessionInfo } from "../types/query";
import { useQueryStore } from "./queryStore";

interface SessionState {
  currentSessionId: string | null;
  sessions: SessionInfo[];
  loading: boolean;
  error: string | null;
  loadSessions: () => Promise<void>;
  setCurrentSession: (sessionId: string | null) => void;
  switchSession: (sessionId: string) => Promise<void>;
}

export const useSessionStore = create<SessionState>((set) => ({
  currentSessionId: null,
  sessions: [],
  loading: false,
  error: null,
  async loadSessions() {
    set({ loading: true, error: null });
    try {
      set({ sessions: await listSessions(), loading: false });
    } catch (error) {
      set({ loading: false, error: error instanceof Error ? error.message : String(error) });
    }
  },
  setCurrentSession(sessionId) {
    set({ currentSessionId: sessionId });
  },
  async switchSession(sessionId) {
    set({ loading: true, error: null });
    try {
      const detail = await getSession(sessionId);
      const latest = [...detail.history].reverse().find((item) => item.sql);
      useQueryStore.setState({
        sessionId: detail.session_id,
        database: detail.database_id || "",
        databaseSelectionSource: "user",
        turns: [],
        loading: false,
      });
      if (latest?.sql && detail.database_id) {
        await useQueryStore.getState().restoreSql(detail.database_id, latest.sql, latest.answer);
      }
      set({ currentSessionId: sessionId, loading: false });
    } catch (error) {
      set({ loading: false, error: error instanceof Error ? error.message : String(error) });
    }
  },
}));
