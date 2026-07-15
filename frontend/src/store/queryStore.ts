import { create } from "zustand";
import * as apiClient from "../api/query";
import type { QueryRequest } from "../api/query";
import type {
  ChatTurn,
  DatabaseInfo,
  QueryResponse,
  RestoredSession,
  RestoredTurn,
  SessionInfo,
  SessionSummary,
} from "../types/query";

export interface QueryApi {
  listDatabases: () => Promise<DatabaseInfo[]>;
  createSession: (databaseId: string) => Promise<SessionInfo>;
  deleteSession: (sessionId: string) => Promise<void>;
  listSessions: () => Promise<SessionSummary[]>;
  getSession: (sessionId: string) => Promise<RestoredSession>;
  queryData: (data: QueryRequest) => Promise<QueryResponse>;
}

export interface QueryState {
  database: string;
  databases: DatabaseInfo[];
  databasesLoading: boolean;
  databaseError: string | null;
  sessions: SessionSummary[];
  sessionsLoading: boolean;
  sessionsError: string | null;
  sessionId: string | null;
  turns: ChatTurn[];
  loading: boolean;
  validationError: string | null;
  loadDatabases: () => Promise<void>;
  loadSessions: () => Promise<void>;
  openSession: (sessionId: string) => Promise<void>;
  selectDatabase: (databaseId: string) => Promise<void>;
  newChat: () => Promise<void>;
  sendMessage: (question: string) => Promise<void>;
  retryTurn: (turnId: string) => Promise<void>;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function recordString(
  record: Record<string, unknown> | null,
  key: string,
  fallback: string,
): string {
  const value = record?.[key];
  return typeof value === "string" && value.trim() ? value : fallback;
}

function restoredResponse(
  session: RestoredSession,
  turn: RestoredTurn,
): QueryResponse {
  const rows = turn.result_preview ?? [];
  const columns = rows.length ? Object.keys(rows[0]) : [];
  const base = {
    session_id: session.id,
    turn_id: turn.id,
    trace: turn.trace,
  };

  if (turn.response_kind === "clarification" && turn.clarification) {
    return {
      ...base,
      kind: "clarification",
      clarification_id: turn.clarification.id,
      question: turn.clarification.prompt,
      options: turn.clarification.options,
      recommended_option_id: null,
    };
  }

  if (turn.response_kind === "partial") {
    return {
      ...base,
      kind: "partial",
      answer: turn.answer ?? "",
      limitations: [],
      suggestions: [],
      confidence: turn.confidence ?? "low",
      sql: turn.sql,
      columns,
      rows,
      chart: turn.chart,
    };
  }

  if (turn.response_kind === "error") {
    const message = recordString(turn.error, "message", "这次查询没有完成。");
    return {
      ...base,
      kind: "error",
      code: recordString(turn.error, "code", "query_failed"),
      message,
      retryable: turn.error?.retryable === true,
      suggestions: Array.isArray(turn.error?.suggestions)
        ? turn.error.suggestions.filter(
            (suggestion): suggestion is string => typeof suggestion === "string",
          )
        : [],
      error: message,
    };
  }

  return {
    ...base,
    kind: "answer",
    answer: turn.answer ?? "",
    sql: turn.sql ?? "",
    columns,
    rows,
    chart: turn.chart,
    confidence: turn.confidence ?? "low",
  };
}

function restoredChatTurn(
  session: RestoredSession,
  turn: RestoredTurn,
): ChatTurn {
  const response = restoredResponse(session, turn);
  const status =
    turn.response_kind === "clarification"
      ? "awaiting_clarification"
      : turn.response_kind === "partial"
        ? "partial"
        : turn.response_kind === "error"
          ? "error"
          : "success";

  return {
    id: turn.id,
    question: turn.question,
    databaseId: session.database_id,
    status,
    response,
    error:
      turn.response_kind === "error"
        ? recordString(turn.error, "message", "这次查询没有完成。")
        : undefined,
  };
}

export function createQueryStore(api: QueryApi = apiClient) {
  let turnSequence = 0;

  return create<QueryState>((set, get) => {
    const updateTurn = (turnId: string, update: Partial<ChatTurn>) => {
      set((state) => ({
        turns: state.turns.map((turn) =>
          turn.id === turnId ? { ...turn, ...update } : turn,
        ),
      }));
    };

    const ensureSession = async (): Promise<string> => {
      const currentSession = get().sessionId;
      if (currentSession) {
        return currentSession;
      }

      const session = await api.createSession(get().database);
      set({ sessionId: session.session_id });
      return session.session_id;
    };

    const runTurn = async (turnId: string, question: string) => {
      try {
        const sessionId = await ensureSession();
        const response = await api.queryData({
          database_id: get().database,
          question,
          session_id: sessionId,
        });
        updateTurn(turnId, {
          status: response.error ? "error" : "success",
          response,
          error: response.error || undefined,
        });
      } catch (error) {
        updateTurn(turnId, {
          status: "error",
          error: errorMessage(error),
        });
      } finally {
        set({ loading: false });
      }
    };

    return {
      database: "",
      databases: [],
      databasesLoading: false,
      databaseError: null,
      sessions: [],
      sessionsLoading: false,
      sessionsError: null,
      sessionId: null,
      turns: [],
      loading: false,
      validationError: null,

      loadDatabases: async () => {
        set({ databasesLoading: true, databaseError: null });
        try {
          const databases = await api.listDatabases();
          set((state) => ({
            databases,
            database:
              state.database && databases.some((item) => item.id === state.database)
                ? state.database
                : databases[0]?.id || "",
            databasesLoading: false,
          }));
        } catch (error) {
          set({
            databases: [],
            database: "",
            databasesLoading: false,
            databaseError: errorMessage(error),
          });
        }
      },

      loadSessions: async () => {
        set({ sessionsLoading: true, sessionsError: null });
        try {
          const sessions = await api.listSessions();
          set({ sessions, sessionsLoading: false });
        } catch (error) {
          set({ sessionsLoading: false, sessionsError: errorMessage(error) });
        }
      },

      openSession: async (sessionId) => {
        if (get().loading) {
          return;
        }
        set({ sessionsLoading: true, sessionsError: null });
        try {
          const session = await api.getSession(sessionId);
          set({
            database: session.database_id,
            sessionId: session.id,
            turns: session.turns.map((turn) => restoredChatTurn(session, turn)),
            validationError: null,
            sessionsLoading: false,
          });
        } catch (error) {
          set({ sessionsLoading: false, sessionsError: errorMessage(error) });
        }
      },

      selectDatabase: async (databaseId) => {
        const state = get();
        if (state.loading || databaseId === state.database) {
          return;
        }
        set({
          database: databaseId,
          sessionId: null,
          turns: [],
          validationError: null,
        });
      },

      newChat: async () => {
        if (get().loading) {
          return;
        }
        set({ sessionId: null, turns: [], validationError: null });
      },

      sendMessage: async (rawQuestion) => {
        const question = rawQuestion.trim();
        const state = get();
        if (state.loading) {
          return;
        }
        if (!state.database) {
          set({ validationError: "请先选择数据库。" });
          return;
        }
        if (!question) {
          set({ validationError: "请输入问题。" });
          return;
        }

        const turn: ChatTurn = {
          id: `turn-${++turnSequence}`,
          question,
          databaseId: state.database,
          status: "loading",
        };
        set((current) => ({
          turns: [...current.turns, turn],
          loading: true,
          validationError: null,
        }));
        await runTurn(turn.id, question);
      },

      retryTurn: async (turnId) => {
        if (get().loading) {
          return;
        }
        const turn = get().turns.find((item) => item.id === turnId);
        if (!turn || turn.status !== "error") {
          return;
        }
        updateTurn(turnId, {
          status: "loading",
          response: undefined,
          error: undefined,
        });
        set({ loading: true, validationError: null });
        await runTurn(turnId, turn.question);
      },
    };
  });
}

export const useQueryStore = createQueryStore();
