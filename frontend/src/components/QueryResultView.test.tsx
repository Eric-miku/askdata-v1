import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryResultView } from "./QueryResultView";

const executeSqlMock = vi.hoisted(() => vi.fn());

vi.mock("../api/query", () => ({
  executeSql: executeSqlMock,
}));

vi.mock("echarts-for-react", () => ({
  default: () => <div data-testid="echarts-chart" />,
}));

describe("QueryResultView", () => {
  beforeEach(() => {
    executeSqlMock.mockReset();
  });

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

  it("re-executes historical SQL at read time to restore rows and chart", async () => {
    executeSqlMock.mockResolvedValue({
      columns: ["product_name", "sales_amount"],
      rows: [{ product_name: "智能手表", sales_amount: 197000 }],
      chart: {
        type: "bar",
        xAxis: { data: ["智能手表"] },
        yAxis: { name: "销售额" },
        series: [{ data: [197000] }],
      },
      trace: [],
      error: null,
    });

    render(
      <QueryResultView
        turn={{
          id: "turn-history",
          question: "历史里销售额最高的产品是什么？",
          databaseId: "demo",
          status: "success",
          response: {
            answer: "历史查询已经生成过 SQL，正在恢复结果。",
            sql: "SELECT product_name, sales_amount FROM product_sales LIMIT 1",
            columns: null,
            rows: null,
            chart: null,
            trace: [],
            error: null,
          },
        }}
        onRetry={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(executeSqlMock).toHaveBeenCalledWith({
        database_id: "demo",
        sql: "SELECT product_name, sales_amount FROM product_sales LIMIT 1",
      });
    });

    expect(await screen.findByText("智能手表")).toBeVisible();
    expect(screen.getByText("可视化")).toBeVisible();
    expect(screen.getByTestId("echarts-chart")).toBeVisible();
  });
});
