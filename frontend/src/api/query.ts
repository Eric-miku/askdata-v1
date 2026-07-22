import axios from "axios";
import type {
  DatabaseInfo,
  KnowledgeEntry,
  KnowledgeEntryInput,
  ManagedDataSource,
  ManagedDataSourceInput,
  PermissionPolicy,
  PermissionPolicyInput,
  QueryPlanResult,
  QueryResponse,
  SchemaCatalogSnapshot,
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
  "columns" | "rows" | "chart" | "analysis" | "suggestions" | "trace" | "error"
>;

const adminToken = import.meta.env.VITE_ADMIN_API_TOKEN;
const userId = import.meta.env.VITE_USER_ID || "local-user";
const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || "/api",
  headers: {
    "X-User-ID": userId,
    ...(adminToken ? { "X-Admin-Token": adminToken } : {}),
  },
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

export async function explainSql(data: ExecuteSqlRequest): Promise<QueryPlanResult> {
  return (await api.post<QueryPlanResult>("/query/explain", data)).data;
}

export async function downloadQueryExport(
  data: ExecuteSqlRequest & { question: string; format: "csv" | "xlsx" },
): Promise<void> {
  const response = await api.post<Blob>("/query/export", data, { responseType: "blob" });
  const url = URL.createObjectURL(response.data);
  const link = document.createElement("a");
  link.href = url;
  link.download = `askdata-result.${data.format}`;
  link.click();
  URL.revokeObjectURL(url);
}

export async function listKnowledgeEntries(search = ""): Promise<KnowledgeEntry[]> {
  const response = await api.get<{ entries: KnowledgeEntry[] }>("/knowledge/entries", {
    params: search ? { search } : undefined,
  });
  return requireArray<KnowledgeEntry>(response.data?.entries, "术语列表");
}

export async function createKnowledgeEntry(data: KnowledgeEntryInput): Promise<KnowledgeEntry> {
  return (await api.post<KnowledgeEntry>("/knowledge/entries", data)).data;
}

export async function updateKnowledgeEntry(id: string, data: KnowledgeEntryInput): Promise<KnowledgeEntry> {
  return (await api.put<KnowledgeEntry>(`/knowledge/entries/${encodeURIComponent(id)}`, data)).data;
}

export async function deleteKnowledgeEntry(id: string): Promise<void> {
  await api.delete(`/knowledge/entries/${encodeURIComponent(id)}`);
}

export async function publishKnowledgeEntry(id: string): Promise<KnowledgeEntry> {
  return (await api.post<KnowledgeEntry>(`/knowledge/entries/${encodeURIComponent(id)}/publish`)).data;
}

export async function listKnowledgeVersions(id: string): Promise<KnowledgeEntry[]> {
  const response = await api.get<{ versions: KnowledgeEntry[] }>(`/knowledge/entries/${encodeURIComponent(id)}/versions`);
  return requireArray<KnowledgeEntry>(response.data?.versions, "术语版本");
}

export async function rollbackKnowledgeEntry(id: string, version: number): Promise<KnowledgeEntry> {
  return (await api.post<KnowledgeEntry>(`/knowledge/entries/${encodeURIComponent(id)}/rollback/${version}`)).data;
}

export async function importKnowledgeEntries(
  entries: Array<Record<string, unknown>>,
): Promise<{ requested: number; imported: number; failed: number; errors: Array<{ index: number; error: string }> }> {
  return (await api.post("/knowledge/import", { entries, mode: "upsert" })).data;
}

export async function downloadKnowledgeExport(format: "json" | "csv" = "json"): Promise<void> {
  const response = await api.get<Blob>("/knowledge/export", { params: { format }, responseType: "blob" });
  const url = URL.createObjectURL(response.data);
  const link = document.createElement("a");
  link.href = url;
  link.download = `askdata-knowledge.${format}`;
  link.click();
  URL.revokeObjectURL(url);
}

export async function listManagedDataSources(): Promise<ManagedDataSource[]> {
  const response = await api.get<{ data_sources: ManagedDataSource[] }>("/data-sources");
  return requireArray<ManagedDataSource>(response.data?.data_sources, "数据源管理");
}

export async function createManagedDataSource(data: ManagedDataSourceInput): Promise<ManagedDataSource> {
  return (await api.post<ManagedDataSource>("/data-sources", data)).data;
}

export async function testManagedDataSource(id: string): Promise<ManagedDataSource> {
  return (await api.post<ManagedDataSource>(`/data-sources/${encodeURIComponent(id)}/test`)).data;
}

export async function syncManagedDataSource(id: string): Promise<ManagedDataSource> {
  return (await api.post<ManagedDataSource>(`/data-sources/${encodeURIComponent(id)}/sync`)).data;
}

export async function getManagedDataSourceSchema(id: string): Promise<SchemaCatalogSnapshot> {
  return (await api.get<SchemaCatalogSnapshot>(`/data-sources/${encodeURIComponent(id)}/schema`)).data;
}

export async function setManagedDataSourceStatus(id: string, enabled: boolean): Promise<ManagedDataSource> {
  return (await api.patch<ManagedDataSource>(`/data-sources/${encodeURIComponent(id)}/status`, { enabled })).data;
}

export async function deleteManagedDataSource(id: string): Promise<void> {
  await api.delete(`/data-sources/${encodeURIComponent(id)}`);
}

export async function listPermissionPolicies(userIdFilter = ""): Promise<PermissionPolicy[]> {
  const response = await api.get<{ policies: PermissionPolicy[] }>("/permissions", {
    params: userIdFilter ? { user_id: userIdFilter } : undefined,
  });
  return requireArray<PermissionPolicy>(response.data?.policies, "权限策略");
}

export async function createPermissionPolicy(data: PermissionPolicyInput): Promise<PermissionPolicy> {
  return (await api.post<PermissionPolicy>("/permissions", data)).data;
}

export async function deletePermissionPolicy(id: string): Promise<void> {
  await api.delete(`/permissions/${encodeURIComponent(id)}`);
}

export type { DatabaseInfo, SessionInfo } from "../types/query";
