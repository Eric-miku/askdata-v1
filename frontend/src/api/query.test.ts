import { beforeEach, describe, expect, it, vi } from "vitest";

const client = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
}));

vi.mock("axios", () => ({
  default: {
    create: () => client,
  },
}));

import {
  createSession,
  deleteSession,
  executeSql,
  explainSql,
  getManagedDataSourceSchema,
  listSessions,
  listDatabases,
  queryData,
  listKnowledgeEntries,
  createPermissionPolicy,
  deletePermissionPolicy,
  listPermissionPolicies,
  importKnowledgeEntries,
} from "./query";

describe("query API", () => {
  beforeEach(() => {
    client.get.mockReset();
    client.post.mockReset();
    client.put.mockReset();
    client.patch.mockReset();
    client.delete.mockReset();
  });

  it("loads database metadata", async () => {
    const databases = [{ id: "demo", name: "Demo", tables_count: 2 }];
    client.get.mockResolvedValue({ data: databases });

    await expect(listDatabases()).resolves.toEqual(databases);
    expect(client.get).toHaveBeenCalledWith("/metadata/databases");
  });

  it("rejects malformed database metadata instead of returning HTML as data", async () => {
    client.get.mockResolvedValue({ data: "<!doctype html>" });

    await expect(listDatabases()).rejects.toThrow("数据库列表响应格式异常");
  });

  it("creates a session with the backend query parameter contract", async () => {
    const session = { session_id: "session-1", created_at: 1 };
    client.post.mockResolvedValue({ data: session });

    await expect(createSession("demo")).resolves.toEqual(session);
    expect(client.post).toHaveBeenCalledWith("/sessions", null, {
      params: { database_id: "demo" },
    });
  });

  it("deletes sessions using an encoded path", async () => {
    client.delete.mockResolvedValue({ data: { success: true } });

    await deleteSession("session/with space");

    expect(client.delete).toHaveBeenCalledWith(
      "/sessions/session%2Fwith%20space",
    );
  });

  it("sends the session id with every query", async () => {
    const response = { answer: "ok", sql: null, columns: [], rows: [], trace: [] };
    client.post.mockResolvedValue({ data: response });
    const request = {
      database_id: "demo",
      question: "How many?",
      session_id: "session-1",
    };

    await expect(queryData(request)).resolves.toEqual(response);
    expect(client.post).toHaveBeenCalledWith("/query", request);
  });

  it("executes historical SQL with the lightweight read endpoint", async () => {
    const response = {
      columns: ["name"],
      rows: [{ name: "Watch" }],
      chart: { type: "bar", series: [{ data: [1] }] },
      trace: [],
      error: null,
    };
    const request = {
      database_id: "demo",
      sql: "SELECT name FROM products",
    };
    client.post.mockResolvedValue({ data: response });

    await expect(executeSql(request)).resolves.toEqual(response);
    expect(client.post).toHaveBeenCalledWith("/query/execute-sql", request);
  });

  it("loads a read-only query plan and a persisted schema catalog", async () => {
    const plan = { success: true, normalized_sql: "SELECT 1", plan: [], suggestions: [], warnings: [] };
    client.post.mockResolvedValueOnce({ data: plan });
    await expect(explainSql({ database_id: "demo", sql: "SELECT 1" })).resolves.toEqual(plan);
    expect(client.post).toHaveBeenCalledWith("/query/explain", { database_id: "demo", sql: "SELECT 1" });

    const catalog = { source_id: "demo", fingerprint: "abc", catalog: { tables: [] } };
    client.get.mockResolvedValueOnce({ data: catalog });
    await expect(getManagedDataSourceSchema("demo/source")).resolves.toEqual(catalog);
    expect(client.get).toHaveBeenCalledWith("/data-sources/demo%2Fsource/schema");
  });

  it("rejects malformed session lists instead of returning undefined", async () => {
    client.get.mockResolvedValue({ data: { sessions: "<!doctype html>" } });

    await expect(listSessions()).rejects.toThrow("会话列表响应格式异常");
  });

  it("loads knowledge entries with an optional search filter", async () => {
    const entry = { id: "term-1", kind: "term", standard_name: "客户" };
    client.get.mockResolvedValue({ data: { entries: [entry] } });
    await expect(listKnowledgeEntries("客户")).resolves.toEqual([entry]);
    expect(client.get).toHaveBeenCalledWith("/knowledge/entries", { params: { search: "客户" } });
  });

  it("manages object permission policies", async () => {
    const policy = {
      id: "policy-1", user_id: "alice", database_id: "sales",
      table_name: "orders", field_name: null, can_query: true, can_export: false, created_at: 1,
    };
    client.get.mockResolvedValue({ data: { policies: [policy] } });
    client.post.mockResolvedValue({ data: policy });
    client.delete.mockResolvedValue({ data: { success: true } });

    await expect(listPermissionPolicies("alice")).resolves.toEqual([policy]);
    expect(client.get).toHaveBeenCalledWith("/permissions", { params: { user_id: "alice" } });
    await expect(createPermissionPolicy({
      user_id: "alice", database_id: "sales", table_name: "orders", field_name: null,
      can_query: true, can_export: false,
    })).resolves.toEqual(policy);
    await deletePermissionPolicy("policy/1");
    expect(client.delete).toHaveBeenCalledWith("/permissions/policy%2F1");
  });

  it("imports knowledge entries in upsert mode", async () => {
    const result = { requested: 1, imported: 1, failed: 0, errors: [] };
    const entries = [{ kind: "term", standard_name: "客户" }];
    client.post.mockResolvedValue({ data: result });

    await expect(importKnowledgeEntries(entries)).resolves.toEqual(result);
    expect(client.post).toHaveBeenCalledWith("/knowledge/import", { entries, mode: "upsert" });
  });
});
