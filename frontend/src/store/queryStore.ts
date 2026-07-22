import { create } from "zustand";
import * as apiClient from "../api/query";
import type { QueryRequest } from "../api/query";
import type {
  ChatTurn,
  DatabaseInfo,
  QueryResponse,
  SessionInfo,
} from "../types/query";

export interface QueryApi {
  listDatabases: () => Promise<DatabaseInfo[]>;
  createSession: (databaseId: string) => Promise<SessionInfo>;
  deleteSession: (sessionId: string) => Promise<void>;
  queryData: (data: QueryRequest) => Promise<QueryResponse>;
}

export interface QueryState {
  database: string;
  databases: DatabaseInfo[];
  databasesLoading: boolean;
  databaseError: string | null;
  sessionId: string | null;
  turns: ChatTurn[];
  loading: boolean;
  validationError: string | null;
  loadDatabases: () => Promise<void>;
  selectDatabase: (databaseId: string) => Promise<void>;
  newChat: () => Promise<void>;
  sendMessage: (question: string) => Promise<void>;
  retryTurn: (turnId: string) => Promise<void>;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
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

    const retireSession = async (sessionId: string | null) => {
      if (!sessionId) {
        return;
      }
      try {
        await api.deleteSession(sessionId);
      } catch {
        // Session cleanup is best-effort; the local conversation is already reset.
      }
    };

    return {
      database: "",
      databases: [],
      databasesLoading: false,
      databaseError: null,
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

      selectDatabase: async (databaseId) => {
        const state = get();
        if (state.loading || databaseId === state.database) {
          return;
        }
        const oldSession = state.sessionId;
        set({
          database: databaseId,
          sessionId: null,
          turns: [],
          validationError: null,
        });
        await retireSession(oldSession);
      },

      newChat: async () => {
        if (get().loading) {
          return;
        }
        const oldSession = get().sessionId;
        set({ sessionId: null, turns: [], validationError: null });
        await retireSession(oldSession);
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
