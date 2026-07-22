import axios from "axios";
import type {
  DatabaseInfo,
  QueryResponse,
  SessionInfo,
} from "../types/query";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000/api";

export interface QueryRequest {
  database_id: string;
  question: string;
  session_id: string;
}

export interface ExecuteSqlRequest {
  database_id: string;
  sql: string;
}

export type ExecuteSqlResponse = Pick<
  QueryResponse,
  "columns" | "rows" | "chart" | "trace" | "error"
>;

const api = axios.create({
  baseURL: API_BASE_URL,
});

export async function listDatabases(): Promise<DatabaseInfo[]> {
  const response = await api.get<DatabaseInfo[]>("/metadata/databases");
  return response.data;
}

export async function createSession(databaseId: string): Promise<SessionInfo> {
  const response = await api.post<SessionInfo>("/sessions", null, {
    params: { database_id: databaseId },
  });
  return response.data;
}

export async function deleteSession(sessionId: string): Promise<void> {
  await api.delete(`/sessions/${encodeURIComponent(sessionId)}`);
}

export async function queryData(data: QueryRequest): Promise<QueryResponse> {
  const response = await api.post<QueryResponse>("/query", data);
  return response.data;
}

export async function executeSql(
  data: ExecuteSqlRequest,
): Promise<ExecuteSqlResponse> {
  const response = await api.post<ExecuteSqlResponse>("/query/execute-sql", data);
  return response.data;
}

export type { DatabaseInfo, SessionInfo } from "../types/query";
