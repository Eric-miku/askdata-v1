import { describe, expect, it, vi } from "vitest";
import { createQueryStore, type QueryApi } from "./queryStore";
import type { QueryResponse } from "../types/query";

const successfulResponse: QueryResponse = {
  answer: "共有 3 条记录。",
  sql: "SELECT COUNT(id) AS count FROM items",
  columns: ["count"],
  rows: [{ count: 3 }],
  chart: null,
  trace: [
    { step: "RetrieveSchema", status: "success", message: "Schema matched." },
  ],
  error: null,
};

function createApi(overrides: Partial<QueryApi> = {}): QueryApi {
  return {
    listDatabases: vi.fn().mockResolvedValue([
      { id: "demo", name: "Demo", tables_count: 2 },
      { id: "finance", name: "Finance", tables_count: 4 },
    ]),
    createSession: vi.fn().mockResolvedValue({
      session_id: "session-1",
      created_at: 1,
    }),
    deleteSession: vi.fn().mockResolvedValue(undefined),
    queryData: vi.fn().mockResolvedValue(successfulResponse),
    ...overrides,
  };
}

describe("query store", () => {
  it("loads databases and selects the first available database", async () => {
    const store = createQueryStore(createApi());

    await store.getState().loadDatabases();

    expect(store.getState().databases).toHaveLength(2);
    expect(store.getState().database).toBe("demo");
    expect(store.getState().databaseError).toBeNull();
  });

  it("creates one session and reuses it for consecutive messages", async () => {
    const api = createApi();
    const store = createQueryStore(api);
    await store.getState().loadDatabases();

    await store.getState().sendMessage("第一问");
    await store.getState().sendMessage("第二问");

    expect(api.createSession).toHaveBeenCalledTimes(1);
    expect(api.queryData).toHaveBeenNthCalledWith(1, {
      database_id: "demo",
      question: "第一问",
      session_id: "session-1",
    });
    expect(api.queryData).toHaveBeenNthCalledWith(2, {
      database_id: "demo",
      question: "第二问",
      session_id: "session-1",
    });
    expect(store.getState().turns.map((turn) => turn.status)).toEqual([
      "success",
      "success",
    ]);
  });

  it("clears the conversation and retires the old session when the database changes", async () => {
    const api = createApi();
    const store = createQueryStore(api);
    await store.getState().loadDatabases();
    await store.getState().sendMessage("第一问");

    await store.getState().selectDatabase("finance");

    expect(api.deleteSession).toHaveBeenCalledWith("session-1");
    expect(store.getState().database).toBe("finance");
    expect(store.getState().sessionId).toBeNull();
    expect(store.getState().turns).toEqual([]);
  });

  it("keeps a failed turn and can retry the original question", async () => {
    const api = createApi({
      queryData: vi
        .fn()
        .mockRejectedValueOnce(new Error("网络不可用"))
        .mockResolvedValueOnce(successfulResponse),
    });
    const store = createQueryStore(api);
    await store.getState().loadDatabases();

    await store.getState().sendMessage("失败的问题");
    const failedTurn = store.getState().turns[0];
    expect(failedTurn.status).toBe("error");
    expect(failedTurn.error).toBe("网络不可用");

    await store.getState().retryTurn(failedTurn.id);

    expect(store.getState().turns[0].status).toBe("success");
    expect(api.queryData).toHaveBeenLastCalledWith({
      database_id: "demo",
      question: "失败的问题",
      session_id: "session-1",
    });
  });

  it("treats an HTTP 200 response with an error field as a failed turn", async () => {
    const responseWithError: QueryResponse = {
      ...successfulResponse,
      answer: "查询失败，请稍后重试。",
      error: "SQL execution failed",
      trace: ["[trace][+0.10s] 查询失败: SQL execution failed"],
    };
    const store = createQueryStore(
      createApi({ queryData: vi.fn().mockResolvedValue(responseWithError) }),
    );
    await store.getState().loadDatabases();

    await store.getState().sendMessage("错误问题");

    expect(store.getState().turns[0]).toMatchObject({
      status: "error",
      error: "SQL execution failed",
      response: responseWithError,
    });
  });
});
