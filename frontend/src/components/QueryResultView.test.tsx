import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { QueryResultView } from "./QueryResultView";

describe("QueryResultView", () => {
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
