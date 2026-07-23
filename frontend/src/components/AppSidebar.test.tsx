import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import AppSidebar from "./AppSidebar";

describe("AppSidebar", () => {
  it("focuses the database search and closes the drawer with Escape", async () => {
    const user = userEvent.setup();
    render(
      <AppSidebar
        theme="dark"
        databases={[{ id: "demo", name: "Demo", tables_count: 2 }]}
        database="demo"
        loading={false}
        databasesLoading={false}
        databaseError={null}
        sessions={[]}
        activeSessionId={null}
        sessionsLoading={false}
        sessionsError={null}
        onNewChat={vi.fn()}
        onOpenSession={vi.fn()}
        onSelectDatabase={vi.fn()}
        onToggleTheme={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "打开数据库" }));
    const search = screen.getByRole("searchbox", { name: "搜索数据库" });
    expect(search).toHaveFocus();
    expect(screen.getByRole("dialog", { name: "数据库" })).toHaveAttribute(
      "aria-modal",
      "true",
    );

    await user.tab({ shift: true });
    expect(screen.getByRole("button", { name: "关闭数据库" })).toHaveFocus();
    await user.tab({ shift: true });
    expect(screen.getByRole("option", { name: "Demo 2 张表" })).toHaveFocus();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog", { name: "数据库" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "打开数据库" })).toHaveFocus();
  });

  it("keeps the database and history drawers mutually exclusive", async () => {
    const user = userEvent.setup();
    render(
      <AppSidebar
        theme="dark"
        databases={[{ id: "demo", name: "Demo", tables_count: 2 }]}
        database="demo"
        loading={false}
        databasesLoading={false}
        databaseError={null}
        sessions={[
          {
            id: "session-1",
            database_id: "demo",
            title: "My conversation",
            created_at: "2026-07-15T10:00:00+00:00",
            updated_at: "2026-07-15T10:01:00+00:00",
          },
        ]}
        activeSessionId="session-1"
        sessionsLoading={false}
        sessionsError={null}
        onNewChat={vi.fn()}
        onOpenSession={vi.fn()}
        onSelectDatabase={vi.fn()}
        onToggleTheme={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "打开数据库" }));
    expect(screen.getByRole("dialog", { name: "数据库" })).toBeVisible();

    await user.click(screen.getByRole("button", { name: "打开历史记录" }));
    expect(screen.queryByRole("dialog", { name: "数据库" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "历史记录" })).toBeVisible();
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
  });
});
