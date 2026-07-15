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
  getSession,
  listDatabases,
  listSessions,
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

  it("lists persisted session summaries", async () => {
    const sessions = [
      {
        id: "session-1",
        database_id: "demo",
        title: "First question",
        created_at: "2026-07-15T10:00:00+00:00",
        updated_at: "2026-07-15T10:01:00+00:00",
      },
    ];
    client.get.mockResolvedValue({ data: sessions });

    await expect(listSessions()).resolves.toEqual(sessions);
    expect(client.get).toHaveBeenCalledWith("/sessions");
  });

  it("gets a restored session using an encoded path", async () => {
    const session = {
      id: "session/with space",
      database_id: "demo",
      title: "First question",
      created_at: "2026-07-15T10:00:00+00:00",
      updated_at: "2026-07-15T10:01:00+00:00",
      turns: [],
    };
    client.get.mockResolvedValue({ data: session });

    await expect(getSession("session/with space")).resolves.toEqual(session);
    expect(client.get).toHaveBeenCalledWith(
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
});
