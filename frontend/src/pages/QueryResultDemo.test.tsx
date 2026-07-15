import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  listDatabases: vi.fn(),
  createSession: vi.fn(),
  deleteSession: vi.fn(),
  listSessions: vi.fn(),
  getSession: vi.fn(),
  queryData: vi.fn(),
  queryStream: vi.fn(),
}));

vi.mock("../api/query", () => apiMocks);
vi.mock("../api/queryStream", () => ({ queryStream: apiMocks.queryStream }));

import { QueryResultDemo } from "./QueryResultDemo";
import { useQueryStore } from "../store/queryStore";

const databases = [
  { id: "demo", name: "Demo", tables_count: 2 },
  { id: "finance", name: "Finance", tables_count: 4 },
];

describe("QueryResultDemo", () => {
  beforeEach(() => {
    apiMocks.listDatabases.mockReset().mockResolvedValue(databases);
    apiMocks.createSession.mockReset().mockResolvedValue({
      session_id: "session-1",
      created_at: 1,
    });
    apiMocks.deleteSession.mockReset().mockResolvedValue(undefined);
    apiMocks.listSessions.mockReset().mockResolvedValue([]);
    apiMocks.getSession.mockReset();
    apiMocks.queryData.mockReset();
    apiMocks.queryStream.mockReset().mockResolvedValue({
      kind: "answer",
      session_id: "session-1",
      turn_id: "turn-1",
      answer: "Demo 中共有 3 条记录。",
      sql: "SELECT COUNT(id) AS count FROM items",
      columns: ["count"],
      rows: [{ count: 3 }],
      chart: null,
      trace: [
        {
          step: "RetrieveSchema",
          status: "success",
          message: "Schema matched.",
          sequence: 1,
        },
      ],
      confidence: "high",
    });
    useQueryStore.setState({
      database: "",
      databases: [],
      databasesLoading: false,
      databaseError: null,
      sessions: [],
      sessionsLoading: false,
      sessionsError: null,
      sessionId: null,
      turns: [],
      loading: false,
      validationError: null,
    });
  });

  it("shows the desktop greeting and filters databases in the sidebar drawer", async () => {
    const user = userEvent.setup();
    render(<QueryResultDemo theme="dark" onToggleTheme={vi.fn()} />);

    expect(await screen.findByText("Hi, user.")).toBeVisible();
    expect(screen.queryByLabelText("示例问题")).not.toBeInTheDocument();
    expect(screen.queryByText("试试：")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开数据库" }));
    expect(screen.getByRole("dialog", { name: "数据库" })).toBeVisible();

    await user.type(screen.getByRole("searchbox", { name: "搜索数据库" }), "fin");
    expect(screen.getByText("Finance")).toBeVisible();
    expect(screen.queryByText("Demo", { selector: ".database-drawer__name" })).not.toBeInTheDocument();
  });

  it("renders a completed turn with answer, trace, SQL, and table", async () => {
    const user = userEvent.setup();
    render(<QueryResultDemo theme="dark" onToggleTheme={vi.fn()} />);
    await screen.findByText("Hi, user.");

    await user.type(
      screen.getByRole("textbox", { name: "向 AskData 提问" }),
      "有多少条记录？{enter}",
    );

    await waitFor(() => expect(apiMocks.queryStream).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Demo 中共有 3 条记录。")).toBeVisible();
    expect(screen.getByText("SELECT COUNT(id) AS count FROM items")).toBeVisible();
    expect(screen.getByText("执行过程")).toBeVisible();
    expect(screen.getByRole("columnheader", { name: "count" })).toBeVisible();
    expect(screen.queryByText("图表配置已返回")).not.toBeInTheDocument();
  });

  it("reopens a persisted conversation from the history rail action", async () => {
    const user = userEvent.setup();
    apiMocks.listSessions.mockResolvedValue([
      {
        id: "session-history",
        database_id: "finance",
        title: "Monthly revenue",
        created_at: "2026-07-15T10:00:00+00:00",
        updated_at: "2026-07-15T10:01:00+00:00",
      },
    ]);
    apiMocks.getSession.mockResolvedValue({
      id: "session-history",
      database_id: "finance",
      title: "Monthly revenue",
      created_at: "2026-07-15T10:00:00+00:00",
      updated_at: "2026-07-15T10:01:00+00:00",
      turns: [
        {
          id: "turn-history",
          question: "What was monthly revenue?",
          response_kind: "answer",
          answer: "Monthly revenue was 42.",
          sql: "SELECT 42 AS revenue",
          result_preview: [{ revenue: 42 }],
          chart: null,
          confidence: "high",
          error: null,
          trace: [],
          created_at: "2026-07-15T10:01:00+00:00",
          clarification: null,
        },
      ],
    });
    render(<QueryResultDemo theme="dark" onToggleTheme={vi.fn()} />);
    await screen.findByText("Hi, user.");
    await waitFor(() => expect(apiMocks.listSessions).toHaveBeenCalledTimes(1));

    await user.click(screen.getByRole("button", { name: "打开历史记录" }));
    await user.click(screen.getByRole("option", { name: /Monthly revenue/ }));

    await waitFor(() =>
      expect(apiMocks.getSession).toHaveBeenCalledWith("session-history"),
    );
    expect(await screen.findByText("Monthly revenue was 42.")).toBeVisible();
    expect(screen.getAllByText("Finance")[0]).toBeVisible();
  });
});
