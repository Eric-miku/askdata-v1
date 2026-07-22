import { create } from "zustand";
import {
  createSession,
  executeSql,
  listDatabases,
  queryData,
  type ExecuteSqlRequest,
  type QueryRequest,
} from "../api/query";
import type { ChatTurn, DatabaseInfo, QueryResponse, SessionInfo } from "../types/query";
import { buildChartFromRows } from "../utils/chartBuilder";

export interface QueryApi {
  listDatabases: () => Promise<DatabaseInfo[]>;
  createSession: (databaseId: string) => Promise<SessionInfo>;
  queryData: (request: QueryRequest) => Promise<QueryResponse>;
  executeSql?: (request: ExecuteSqlRequest) => Promise<Pick<QueryResponse, "columns" | "rows" | "chart" | "trace" | "error">>;
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
  restoreSql: (databaseId: string, sql: string, answer?: string) => Promise<void>;
}

const defaultApi: QueryApi = { listDatabases, createSession, queryData, executeSql };

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function makeTurn(question: string, status: ChatTurn["status"]): ChatTurn {
  return { id: crypto.randomUUID(), question, status };
}

export function createQueryStore(api: QueryApi = defaultApi) {
  return create<QueryState>((set, get) => {
    const submit = async (turnId: string, question: string) => {
      const { database } = get();
      if (!database) {
        set({ validationError: "请先选择数据库" });
        return;
      }
      let sessionId = get().sessionId;
      try {
        if (!sessionId) {
          sessionId = (await api.createSession(database)).session_id;
          set({ sessionId });
        }
        const response = await api.queryData({ database_id: database, question, session_id: sessionId });
        set((state) => ({
          loading: false,
          turns: state.turns.map((turn) => turn.id !== turnId ? turn : {
            ...turn,
            status: response.error ? "error" : "success",
            response,
            error: response.error || undefined,
          }),
        }));
      } catch (error) {
        const message = errorMessage(error);
        set((state) => ({
          loading: false,
          turns: state.turns.map((turn) => turn.id !== turnId ? turn : { ...turn, status: "error", error: message }),
        }));
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
      async loadDatabases() {
        set({ databasesLoading: true, databaseError: null });
        try {
          const databases = await api.listDatabases();
          set((state) => ({
            databases,
            database: state.database || databases[0]?.id || "",
            databasesLoading: false,
          }));
        } catch (error) {
          set({ databasesLoading: false, databaseError: errorMessage(error) });
        }
      },
      async selectDatabase(database) {
        if (database === get().database) return;
        await get().newChat();
        set({ database, validationError: null });
      },
      async newChat() {
        set({ sessionId: null, turns: [], loading: false, validationError: null });
      },
      async sendMessage(question) {
        const text = question.trim();
        if (!text) {
          set({ validationError: "请输入问题" });
          return;
        }
        const turn = { ...makeTurn(text, "loading"), databaseId: get().database };
        set((state) => ({ turns: [...state.turns, turn], loading: true, validationError: null }));
        await submit(turn.id, text);
      },
      async retryTurn(turnId) {
        const turn = get().turns.find((item) => item.id === turnId);
        if (!turn || get().loading) return;
        set((state) => ({
          loading: true,
          turns: state.turns.map((item) => item.id === turnId ? { ...item, status: "loading", error: undefined } : item),
        }));
        await submit(turnId, turn.question);
      },
      async restoreSql(databaseId, sql, answer = "") {
        const turn = { ...makeTurn("历史查询", "loading"), databaseId };
        set({ database: databaseId, turns: [turn], loading: true, validationError: null });
        try {
          const response = await (api.executeSql || executeSql)({ database_id: databaseId, sql });
          const fullResponse: QueryResponse = {
            answer,
            sql,
            columns: response.columns,
            rows: response.rows,
            chart: response.chart ?? buildChartFromRows(response.columns, response.rows),
            trace: response.trace,
            error: response.error,
          };
          set({ turns: [{ ...turn, status: response.error ? "error" : "success", response: fullResponse, error: response.error || undefined }], loading: false });
        } catch (error) {
          set({ turns: [{ ...turn, status: "error", error: errorMessage(error) }], loading: false });
        }
      },
    };
  });
}

export const useQueryStore = createQueryStore();
