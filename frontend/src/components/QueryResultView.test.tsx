import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { QueryResultView } from "./QueryResultView";

describe("QueryResultView", () => {
  it("renders assistant answers as Markdown", () => {
    render(
      <QueryResultView
        turn={{
          id: "turn-markdown",
          question: "有哪些表？",
          databaseId: "demo",
          status: "success",
          response: {
            answer:
              "以下是所有表：\n\n**1. customers（客户表）**\n- CustomerID\n- Segment\n\n**2. transactions\\_1k（交易表）**\n- TransactionID",
            sql: null,
            columns: null,
            rows: null,
            trace: [],
            error: null,
          },
        }}
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByText("1. customers（客户表）").tagName).toBe("STRONG");
    expect(screen.getByText("2. transactions_1k（交易表）").tagName).toBe(
      "STRONG",
    );
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
  });

  it("shows the table empty state for a valid zero-row query", () => {
    render(
      <QueryResultView
        turn={{
          id: "turn-1",
          question: "没有匹配项吗？",
          databaseId: "demo",
          status: "success",
          response: {
            answer: "没有找到匹配结果。",
            sql: "SELECT id FROM items WHERE 1 = 0",
            columns: ["id"],
            rows: [],
            trace: [],
            error: null,
          },
        }}
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByText("查询结果")).toBeVisible();
    expect(screen.getByText("暂无表格数据")).toBeVisible();
  });
});
