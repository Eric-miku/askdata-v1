import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { QueryResultView } from "./QueryResultView";

vi.mock("echarts-for-react", () => ({ default: () => <div data-testid="echart" /> }));

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

  it("renders partial state in identity, answer, warning, trace, SQL, chart, table order", () => {
    const { container } = render(
      <QueryResultView
        turn={{
          id: "turn-partial",
          question: "列出前五名",
          databaseId: "demo",
          status: "partial",
          response: {
            kind: "partial",
            session_id: "s1",
            turn_id: "turn-partial",
            answer: "目前找到这些结果。",
            limitations: ["缺少上月数据"],
            suggestions: ["缩小日期范围"],
            confidence: "low",
            sql: "SELECT school, enrollment FROM schools LIMIT 5",
            columns: ["school", "enrollment"],
            rows: [{ school: "North", enrollment: 320 }],
            chart: {
              type: "horizontal_bar",
              title: "学校排名",
              category_field: "school",
              category_label: "学校",
              value_fields: ["enrollment"],
              value_labels: { enrollment: "学生数" },
              reason: "ranking",
            },
            trace: [
              { step: "ExecuteSql", status: "warning", message: "Partial", sequence: 1 },
            ],
          },
        }}
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByRole("status", { name: "部分结果" })).toHaveTextContent(
      "缺少上月数据",
    );
    const order = [
      ".chat-turn__identity",
      ".chat-turn__answer",
      ".chat-turn__partial",
      ".agent-trace",
      ".sql-panel",
      ".chart-panel",
      ".chat-turn__result",
    ].map((selector) => container.querySelector(selector));
    order.forEach((element) => expect(element).not.toBeNull());
    for (let index = 1; index < order.length; index += 1) {
      expect(order[index - 1]!.compareDocumentPosition(order[index]!)).toBe(
        Node.DOCUMENT_POSITION_FOLLOWING,
      );
    }
  });

  it("renders an inline clarification and resolves the same turn", async () => {
    const user = userEvent.setup();
    const onResolve = vi.fn();
    render(
      <QueryResultView
        turn={{
          id: "turn-c1",
          question: "收入是多少？",
          databaseId: "demo",
          status: "awaiting_clarification",
          response: {
            kind: "clarification",
            session_id: "s1",
            turn_id: "turn-c1",
            trace: [],
            clarification_id: "c1",
            question: "请选择收入定义",
            options: [
              { id: "gross", label: "毛收入", description: null },
              { id: "net", label: "净收入", description: null },
            ],
            recommended_option_id: "net",
          },
        }}
        onRetry={vi.fn()}
        onResolveClarification={onResolve}
      />,
    );

    await user.click(screen.getByRole("button", { name: "净收入" }));
    expect(onResolve).toHaveBeenCalledWith("turn-c1", "c1", { option_id: "net" });
  });
});
