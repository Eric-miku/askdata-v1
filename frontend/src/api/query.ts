import axios from "axios";
import type { QueryResponse } from "../types/query";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000/api";

interface QueryRequest {
  database_id: string;
  question: string;
  session_id?: string;
}

export interface DatabaseInfo {
  id: string;
  name: string;
  tables_count?: number;
}

const api = axios.create({
  baseURL: API_BASE_URL,
});

export async function listDatabases(): Promise<DatabaseInfo[]> {
  const response = await api.get<DatabaseInfo[]>("/metadata/databases");
  return response.data;
}

export async function queryData(data: QueryRequest): Promise<QueryResponse> {
  const response = await api.post<QueryResponse>("/query", data);
  return response.data;
}
