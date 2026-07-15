import { create } from "zustand";
import * as apiClient from "../api/query";
import { queryStream as streamQuery } from "../api/queryStream";
import type { QueryRequest } from "../api/query";
import type {
  ChatTurn,
  ClarificationResolution,
  DatabaseInfo,
  QueryStreamEvent,
  QueryResponse,
  RestoredSession,
  RestoredTurn,
  SessionInfo,
  SessionSummary,
  TraceEvent,
  V2QueryRequest,
  V2QueryResponse,
} from "../types/query";

export interface QueryApi {
  listDatabases: () => Promise<DatabaseInfo[]>;
  createSession: (databaseId: string) => Promise<SessionInfo>;
  deleteSession: (sessionId: string) => Promise<void>;
  listSessions: () => Promise<SessionSummary[]>;
  getSession: (sessionId: string) => Promise<RestoredSession>;
  queryData: (data: QueryRequest) => Promise<QueryResponse>;
  queryStream: (
    data: V2QueryRequest,
    onEvent: (event: QueryStreamEvent) => void,
    signal?: AbortSignal,
  ) => Promise<V2QueryResponse>;
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
  resolveClarification: (
    turnId: string,
    clarificationId: string,
    resolution: Omit<ClarificationResolution, "clarification_id">,
  ) => Promise<void>;
  cancelActiveQuery: () => void;
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

const defaultApi: QueryApi = {
  ...apiClient,
  queryStream: (request, onEvent, signal) =>
    streamQuery(request, onEvent, undefined, signal),
};

function finalStatus(response: V2QueryResponse): ChatTurn["status"] {
  if (response.kind === "clarification") return "awaiting_clarification";
  if (response.kind === "partial") return "partial";
  if (response.kind === "error") return "error";
  return "success";
}

function mergeTrace(...sources: TraceEvent[][]): TraceEvent[] {
  const bySequence = new Map<number, TraceEvent>();
  for (const source of sources) {
    for (const event of source) bySequence.set(event.sequence, event);
  }
  return [...bySequence.values()].sort((a, b) => a.sequence - b.sequence);
}

export function createQueryStore(api: QueryApi = defaultApi) {
  let turnSequence = 0;
  let historyRequestGeneration = 0;
  let operationSequence = 0;
  let activeOperation: {
    id: number;
    turnId: string;
    controller: AbortController;
  } | null = null;

  return create<QueryState>((set, get) => {
    const invalidateHistoryRequest = () => {
      historyRequestGeneration += 1;
      set({ sessionsLoading: false });
    };

    const updateTurn = (turnId: string, update: Partial<ChatTurn>) => {
      set((state) => ({
        turns: state.turns.map((turn) =>
          turn.id === turnId ? { ...turn, ...update } : turn,
        ),
      }));
    };

    const ensureSession = async (
      canCommit: () => boolean,
    ): Promise<string | null> => {
      const currentSession = get().sessionId;
      if (currentSession) {
        return currentSession;
      }

      const session = await api.createSession(get().database);
      if (!canCommit()) return null;
      const newerSession = get().sessionId;
      if (newerSession) return newerSession;
      set({ sessionId: session.session_id });
      return session.session_id;
    };

    const runTurn = async (
      turnId: string,
      requestForSession: (sessionId: string) => V2QueryRequest,
    ) => {
      const operation = {
        id: ++operationSequence,
        turnId,
        controller: new AbortController(),
      };
      activeOperation = operation;
      const isCurrent = () => activeOperation?.id === operation.id;
      let lastSequence = -1;
      const streamedTrace: TraceEvent[] = [];

      const onEvent = (event: QueryStreamEvent) => {
        if (!isCurrent() || event.type !== "trace") return;
        if (event.data.sequence <= lastSequence) return;
        lastSequence = event.data.sequence;
        streamedTrace.push(event.data);
        set((state) => ({
          turns: state.turns.map((turn) => {
            if (turn.id !== turnId) return turn;
            const existingTrace = turn.response?.trace ?? [];
            return {
              ...turn,
              response: {
                answer: "",
                trace: [...existingTrace, event.data],
              },
            };
          }),
        }));
      };

      try {
        const sessionId = await ensureSession(isCurrent);
        if (!sessionId || !isCurrent() || operation.controller.signal.aborted) return;
        const response = await api.queryStream(
          requestForSession(sessionId),
          onEvent,
          operation.controller.signal,
        );
        if (!isCurrent()) return;
        const normalizedResponse = {
          ...response,
          trace: mergeTrace(streamedTrace, response.trace),
        } as V2QueryResponse;
        updateTurn(turnId, {
          status: finalStatus(response),
          response: normalizedResponse,
          error: response.kind === "error" ? response.message : undefined,
        });
        activeOperation = null;
        set({ loading: false });
        await get().loadSessions();
      } catch (error) {
        if (!isCurrent()) return;
        updateTurn(turnId, {
          status: "error",
          error:
            error instanceof DOMException && error.name === "AbortError"
              ? "查询已取消。"
              : errorMessage(error),
        });
      } finally {
        if (isCurrent()) {
          activeOperation = null;
          set({ loading: false });
        }
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
        const requestGeneration = ++historyRequestGeneration;
        set({ sessionsLoading: true, sessionsError: null });
        try {
          const sessions = await api.listSessions();
          if (requestGeneration !== historyRequestGeneration) {
            return;
          }
          set({ sessions, sessionsLoading: false });
        } catch (error) {
          if (requestGeneration !== historyRequestGeneration) {
            return;
          }
          set({ sessionsLoading: false, sessionsError: errorMessage(error) });
        }
      },

      openSession: async (sessionId) => {
        if (get().loading) {
          return;
        }
        const requestGeneration = ++historyRequestGeneration;
        set({ sessionsLoading: true, sessionsError: null });
        try {
          const session = await api.getSession(sessionId);
          if (requestGeneration !== historyRequestGeneration) {
            return;
          }
          set({
            database: session.database_id,
            sessionId: session.id,
            turns: session.turns.map((turn) => restoredChatTurn(session, turn)),
            validationError: null,
            sessionsLoading: false,
          });
        } catch (error) {
          if (requestGeneration !== historyRequestGeneration) {
            return;
          }
          set({ sessionsLoading: false, sessionsError: errorMessage(error) });
        }
      },

      selectDatabase: async (databaseId) => {
        const state = get();
        if (state.loading || databaseId === state.database) {
          return;
        }
        invalidateHistoryRequest();
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
        invalidateHistoryRequest();
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

        invalidateHistoryRequest();

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
        await runTurn(turn.id, (sessionId) => ({
          database_id: state.database,
          question,
          session_id: sessionId,
        }));
      },

      retryTurn: async (turnId) => {
        if (get().loading) {
          return;
        }
        const turn = get().turns.find((item) => item.id === turnId);
        if (!turn || turn.status !== "error") {
          return;
        }
        invalidateHistoryRequest();
        updateTurn(turnId, {
          status: "loading",
          response: undefined,
          error: undefined,
        });
        set({ loading: true, validationError: null });
        await runTurn(turnId, (sessionId) => ({
          database_id: turn.databaseId,
          question: turn.question,
          session_id: sessionId,
        }));
      },

      resolveClarification: async (turnId, clarificationId, resolution) => {
        const state = get();
        if (state.loading) return;
        const turn = state.turns.find((item) => item.id === turnId);
        const response = turn?.response;
        if (
          !turn ||
          turn.status !== "awaiting_clarification" ||
          !response ||
          !("kind" in response) ||
          response.kind !== "clarification" ||
          response.clarification_id !== clarificationId ||
          !state.sessionId
        ) {
          return;
        }

        invalidateHistoryRequest();
        updateTurn(turnId, {
          status: "loading",
          response: { answer: "", trace: response.trace },
          error: undefined,
        });
        set({ loading: true, validationError: null });
        await runTurn(turnId, (sessionId) => ({
          database_id: turn.databaseId,
          session_id: sessionId,
          clarification: { clarification_id: clarificationId, ...resolution },
        }));
      },

      cancelActiveQuery: () => {
        const operation = activeOperation;
        if (!operation) return;
        activeOperation = null;
        operation.controller.abort();
        updateTurn(operation.turnId, {
          status: "error",
          error: "查询已取消。",
        });
        set({ loading: false });
      },
    };
  });
}

export const useQueryStore = createQueryStore();
