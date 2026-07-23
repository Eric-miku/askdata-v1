import axios from "axios";
import type {
  DatabaseInfo,
  QueryResponse,
  RestoredSession,
  SessionInfo,
  SessionSummary,
  V2QueryRequest,
} from "../types/query";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000/api";

export type QueryRequest = V2QueryRequest;

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

export async function listSessions(): Promise<SessionSummary[]> {
  const response = await api.get<SessionSummary[]>("/sessions");
  return response.data;
}

export async function getSession(sessionId: string): Promise<RestoredSession> {
  const response = await api.get<RestoredSession>(
    `/sessions/${encodeURIComponent(sessionId)}`,
  );
  return response.data;
}

export async function queryData(data: QueryRequest): Promise<QueryResponse> {
  const response = await api.post<QueryResponse>("/query", data);
  return response.data;
}

export type {
  DatabaseInfo,
  RestoredSession,
  SessionInfo,
  SessionSummary,
} from "../types/query";
