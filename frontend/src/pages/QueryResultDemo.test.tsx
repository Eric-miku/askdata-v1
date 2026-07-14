import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  listDatabases: vi.fn(),
  createSession: vi.fn(),
  deleteSession: vi.fn(),
  queryData: vi.fn(),
}));

vi.mock("../api/query", () => apiMocks);

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
    apiMocks.queryData.mockReset().mockResolvedValue({
      answer: "Demo 中共有 3 条记录。",
      sql: "SELECT COUNT(id) AS count FROM items",
      columns: ["count"],
      rows: [{ count: 3 }],
      chart: null,
      trace: [
        { step: "RetrieveSchema", status: "success", message: "Schema matched." },
      ],
      error: null,
    });
    useQueryStore.setState({
      database: "",
      databases: [],
      databasesLoading: false,
      databaseError: null,
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

    await waitFor(() => expect(apiMocks.queryData).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Demo 中共有 3 条记录。")).toBeVisible();
    expect(screen.getByText("SELECT COUNT(id) AS count FROM items")).toBeVisible();
    expect(screen.getByText("思考过程")).toBeVisible();
    expect(screen.getByRole("columnheader", { name: "count" })).toBeVisible();
    expect(screen.queryByText("图表配置已返回")).not.toBeInTheDocument();
  });
});
