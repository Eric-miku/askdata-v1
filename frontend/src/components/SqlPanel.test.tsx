import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

const explainSqlMock = vi.hoisted(() => vi.fn());
vi.mock("../api/query", () => ({ explainSql: explainSqlMock }));
import SqlPanel from "./SqlPanel";
import styles from "../styles.css?raw";

describe("SqlPanel", () => {
  it("uses a light code surface and theme-aware SQL colors in light mode", () => {
    expect(styles).toMatch(
      /:root\[data-theme="light"\][^{]*{[^}]*--surface-code:\s*#[ef][0-9a-f]{5}/i,
    );
    expect(styles).toMatch(/\.sql-panel__code\s*{[^}]*color:\s*var\(--code-text\)/);
  });

  it("starts expanded and can be collapsed", async () => {
    const user = userEvent.setup();
    render(<SqlPanel sql="SELECT id FROM items" />);

    expect(screen.getByText("SELECT id FROM items")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "折叠 SQL" }));
    expect(screen.queryByText("SELECT id FROM items")).not.toBeInTheDocument();
  });

  it("copies the generated SQL and announces success", async () => {
    const user = userEvent.setup();
    const writeText = vi
      .spyOn(navigator.clipboard, "writeText")
      .mockResolvedValue(undefined);
    render(<SqlPanel sql="SELECT id FROM items" />);

    await user.click(screen.getByRole("button", { name: "复制 SQL" }));

    expect(writeText).toHaveBeenCalledWith("SELECT id FROM items");
    expect(screen.getByRole("status")).toHaveTextContent("已复制");
    expect(screen.getByRole("button", { name: "复制 SQL" })).toHaveTextContent(
      "已复制",
    );
  });

  it("shows an authorized read-only plan and manual index suggestion", async () => {
    explainSqlMock.mockResolvedValue({
      success: true,
      normalized_sql: "SELECT region FROM orders WHERE region = '华东'",
      plan: [{ id: 2, parent: 0, detail: "SCAN orders" }],
      suggestions: [{
        type: "index_candidate",
        table: "orders",
        columns: ["region"],
        reason: "执行计划显示筛选或关联条件正在扫描整表",
        sql: "CREATE INDEX idx_orders_region ON orders (region)",
        automatic: false,
      }],
      warnings: ["AskData 不会自动修改数据库索引"],
    });
    const user = userEvent.setup();
    render(<SqlPanel sql="SELECT region FROM orders WHERE region = '华东'" databaseId="sales" />);

    await user.click(screen.getByRole("button", { name: "执行计划" }));
    await waitFor(() => expect(explainSqlMock).toHaveBeenCalledWith({
      database_id: "sales",
      sql: "SELECT region FROM orders WHERE region = '华东'",
    }));
    expect(screen.getByLabelText("SQL 执行计划")).toBeVisible();
    expect(screen.getByText("SCAN orders")).toBeVisible();
    expect(screen.getByText("候选索引")).toBeVisible();
    expect(screen.getByText(/CREATE INDEX idx_orders_region/)).toBeVisible();
    expect(screen.getByText(/不会自动修改数据库索引/)).toBeVisible();
  });
});
