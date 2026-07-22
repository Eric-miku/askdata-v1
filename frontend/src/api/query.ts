import axios from "axios";
import type { QueryResponse } from "../types/query";


interface QueryRequest {
  database_id:string;
  question:string;
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
