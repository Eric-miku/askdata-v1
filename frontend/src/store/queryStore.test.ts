import { describe, expect, it, vi } from "vitest";
import { createQueryStore, type QueryApi } from "./queryStore";
import type { QueryResponse, RestoredSession } from "../types/query";

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
    listSessions: vi.fn().mockResolvedValue([]),
    getSession: vi.fn(),
    queryData: vi.fn().mockResolvedValue(successfulResponse),
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

function restoredSession(
  id: string,
  databaseId: string,
  question = `Question for ${id}`,
): RestoredSession {
  return {
    id,
    database_id: databaseId,
    title: question,
    created_at: "2026-07-15T10:00:00+00:00",
    updated_at: "2026-07-15T10:01:00+00:00",
    turns: [
      {
        id: `turn-${id}`,
        question,
        response_kind: "answer",
        answer: `Answer for ${id}`,
        sql: "SELECT 1 AS value",
        result_preview: [{ value: 1 }],
        chart: null,
        confidence: "high",
        error: null,
        trace: [],
        created_at: "2026-07-15T10:01:00+00:00",
        clarification: null,
      },
    ],
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

  it("clears the local conversation without deleting persisted history when the database changes", async () => {
    const api = createApi();
    const store = createQueryStore(api);
    await store.getState().loadDatabases();
    await store.getState().sendMessage("第一问");

    await store.getState().selectDatabase("finance");

    expect(api.deleteSession).not.toHaveBeenCalled();
    expect(store.getState().database).toBe("finance");
    expect(store.getState().sessionId).toBeNull();
    expect(store.getState().turns).toEqual([]);
  });

  it("starts a new local conversation without deleting persisted history", async () => {
    const api = createApi();
    const store = createQueryStore(api);
    await store.getState().loadDatabases();
    await store.getState().sendMessage("第一问");

    await store.getState().newChat();

    expect(api.deleteSession).not.toHaveBeenCalled();
    expect(store.getState().sessionId).toBeNull();
    expect(store.getState().turns).toEqual([]);
  });

  it("loads recent sessions and opens a persisted conversation", async () => {
    const restoredSession: RestoredSession = {
      id: "session-history",
      database_id: "finance",
      title: "How many?",
      created_at: "2026-07-15T10:00:00+00:00",
      updated_at: "2026-07-15T10:01:00+00:00",
      turns: [
        {
          id: "turn-history",
          question: "How many?",
          response_kind: "answer",
          answer: "There are 3 rows.",
          sql: "SELECT COUNT(*) AS count FROM items",
          result_preview: [{ count: 3 }],
          chart: null,
          confidence: "high",
          error: null,
          trace: [
            {
              step: "ExecuteSql",
              status: "success",
              message: "Query completed.",
              sequence: 1,
            },
          ],
          created_at: "2026-07-15T10:01:00+00:00",
          clarification: null,
        },
      ],
    };
    const sessions = [
      {
        id: "session-history",
        database_id: "finance",
        title: "How many?",
        created_at: "2026-07-15T10:00:00+00:00",
        updated_at: "2026-07-15T10:01:00+00:00",
      },
    ];
    const api = createApi({
      listSessions: vi.fn().mockResolvedValue(sessions),
      getSession: vi.fn().mockResolvedValue(restoredSession),
    });
    const store = createQueryStore(api);

    await store.getState().loadSessions();
    await store.getState().openSession("session-history");

    expect(store.getState()).toMatchObject({
      sessions,
      sessionsLoading: false,
      sessionsError: null,
      sessionId: "session-history",
      database: "finance",
    });
    expect(store.getState().turns[0]).toMatchObject({
      id: "turn-history",
      question: "How many?",
      databaseId: "finance",
      status: "success",
      response: {
        answer: "There are 3 rows.",
        sql: "SELECT COUNT(*) AS count FROM items",
        columns: ["count"],
        rows: [{ count: 3 }],
      },
    });
  });

  it("exposes recent-session loading failures without clearing the active conversation", async () => {
    const api = createApi({
      listSessions: vi.fn().mockRejectedValue(new Error("历史记录不可用")),
    });
    const store = createQueryStore(api);
    store.setState({
      sessionId: "active-session",
      turns: [
        {
          id: "active-turn",
          question: "Keep me",
          databaseId: "demo",
          status: "success",
          response: successfulResponse,
        },
      ],
    });

    await store.getState().loadSessions();

    expect(store.getState().sessionsLoading).toBe(false);
    expect(store.getState().sessionsError).toBe("历史记录不可用");
    expect(store.getState().sessionId).toBe("active-session");
    expect(store.getState().turns).toHaveLength(1);
  });

  it("keeps the newest conversation when session fetches resolve out of order", async () => {
    const first = deferred<RestoredSession>();
    const second = deferred<RestoredSession>();
    const api = createApi({
      getSession: vi.fn((sessionId: string) =>
        sessionId === "session-1" ? first.promise : second.promise,
      ),
    });
    const store = createQueryStore(api);

    const openingFirst = store.getState().openSession("session-1");
    const openingSecond = store.getState().openSession("session-2");
    second.resolve(restoredSession("session-2", "finance"));
    await openingSecond;
    first.resolve(restoredSession("session-1", "demo"));
    await openingFirst;

    expect(store.getState().sessionId).toBe("session-2");
    expect(store.getState().database).toBe("finance");
    expect(store.getState().turns[0].question).toBe("Question for session-2");
  });

  it("ignores a pending session open after starting a new chat", async () => {
    const pending = deferred<RestoredSession>();
    const store = createQueryStore(
      createApi({ getSession: vi.fn().mockReturnValue(pending.promise) }),
    );
    store.setState({ database: "demo", sessionId: "active-session" });

    const opening = store.getState().openSession("old-session");
    await store.getState().newChat();
    pending.resolve(restoredSession("old-session", "finance"));
    await opening;

    expect(store.getState().sessionId).toBeNull();
    expect(store.getState().database).toBe("demo");
    expect(store.getState().turns).toEqual([]);
  });

  it("ignores a pending session open after changing databases", async () => {
    const pending = deferred<RestoredSession>();
    const store = createQueryStore(
      createApi({ getSession: vi.fn().mockReturnValue(pending.promise) }),
    );
    store.setState({ database: "demo", sessionId: "active-session" });

    const opening = store.getState().openSession("old-session");
    await store.getState().selectDatabase("finance");
    pending.resolve(restoredSession("old-session", "demo"));
    await opening;

    expect(store.getState().sessionId).toBeNull();
    expect(store.getState().database).toBe("finance");
    expect(store.getState().turns).toEqual([]);
  });

  it("ignores a pending session open after submitting a new question", async () => {
    const pending = deferred<RestoredSession>();
    const store = createQueryStore(
      createApi({ getSession: vi.fn().mockReturnValue(pending.promise) }),
    );
    store.setState({ database: "demo", sessionId: "active-session" });

    const opening = store.getState().openSession("old-session");
    await store.getState().sendMessage("Fresh question");
    pending.resolve(restoredSession("old-session", "finance"));
    await opening;

    expect(store.getState().sessionId).toBe("active-session");
    expect(store.getState().database).toBe("demo");
    const turns = store.getState().turns;
    expect(turns[turns.length - 1]?.question).toBe("Fresh question");
  });

  it("refreshes history after a successful persisted query", async () => {
    const session = {
      id: "session-1",
      database_id: "demo",
      title: "Newly persisted question",
      created_at: "2026-07-15T10:00:00+00:00",
      updated_at: "2026-07-15T10:01:00+00:00",
    };
    const api = createApi({
      listSessions: vi.fn().mockResolvedValue([session]),
      getSession: vi
        .fn()
        .mockResolvedValue(
          restoredSession("session-1", "demo", "Newly persisted question"),
        ),
    });
    const store = createQueryStore(api);
    store.setState({ database: "demo" });

    await store.getState().sendMessage("Newly persisted question");

    expect(api.listSessions).toHaveBeenCalledTimes(1);
    expect(store.getState().sessions).toEqual([session]);
    expect(store.getState().sessionsError).toBeNull();

    await store.getState().openSession("session-1");
    expect(api.getSession).toHaveBeenCalledWith("session-1");
    expect(store.getState().turns[0].question).toBe("Newly persisted question");
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
