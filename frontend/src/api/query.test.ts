import { beforeEach, describe, expect, it, vi } from "vitest";

const client = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
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
  listSessions,
  listDatabases,
  queryData,
} from "./query";

describe("query API", () => {
  beforeEach(() => {
    client.get.mockReset();
    client.post.mockReset();
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

  it("rejects malformed session lists instead of returning undefined", async () => {
    client.get.mockResolvedValue({ data: { sessions: "<!doctype html>" } });

    await expect(listSessions()).rejects.toThrow("会话列表响应格式异常");
  });
});
