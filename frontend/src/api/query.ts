import axios from "axios";
import type {
  DatabaseInfo,
  QueryResponse,
  SessionDetail,
  SessionInfo,
} from "../types/query";

export interface QueryRequest {
  database_id: string;
  question: string;
  session_id?: string;
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
  baseURL: import.meta.env.VITE_API_BASE_URL || "/api",
});

function requireArray<T>(value: unknown, label: string): T[] {
  if (Array.isArray(value)) {
    return value as T[];
  }
  throw new Error(`${label}响应格式异常，请确认后端服务已启动，或 Vite /api 代理配置正确。`);
}

export async function listDatabases(): Promise<DatabaseInfo[]> {
  const response = await api.get<unknown>("/metadata/databases");
  return requireArray<DatabaseInfo>(response.data, "数据库列表");
}

export async function createSession(databaseId: string): Promise<SessionInfo> {
  const response = await api.post<SessionInfo>("/sessions", null, {
    params: { database_id: databaseId },
  });
  return response.data;
}

export async function listSessions(): Promise<SessionInfo[]> {
  const response = await api.get<unknown>("/sessions");
  if (
    response.data &&
    typeof response.data === "object" &&
    "sessions" in response.data
  ) {
    return requireArray<SessionInfo>(
      (response.data as { sessions: unknown }).sessions,
      "会话列表",
    );
  }
  throw new Error("会话列表响应格式异常，请确认后端服务已启动，或 Vite /api 代理配置正确。");
}

export async function getSession(sessionId: string): Promise<SessionDetail> {
  const response = await api.get<SessionDetail>(`/sessions/${encodeURIComponent(sessionId)}`);
  return response.data;
}

export async function deleteSession(sessionId: string): Promise<void> {
  await api.delete(`/sessions/${encodeURIComponent(sessionId)}`);
}

export async function queryData(data: QueryRequest): Promise<QueryResponse> {
  const response = await api.post<QueryResponse>("/query", data);
  return response.data;
}

export async function executeSql(data: ExecuteSqlRequest): Promise<ExecuteSqlResponse> {
  const response = await api.post<ExecuteSqlResponse>("/query/execute-sql", data);
  return response.data;
}

export type { DatabaseInfo, SessionInfo } from "../types/query";
