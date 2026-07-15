import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import AgentTrace from "./AgentTrace";

describe("AgentTrace", () => {
  it("summarizes and expands structured and string trace entries", async () => {
    const user = userEvent.setup();
    render(
      <AgentTrace
        steps={[
          {
            step: "RetrieveSchema",
            status: "success",
            message: "Schema matched.",
          },
          "[abc][+0.10s] 查询失败: SQL execution failed",
        ]}
      />,
    );

    expect(screen.getByText("执行过程")).toBeInTheDocument();
    expect(screen.queryByText("思考过程")).not.toBeInTheDocument();
    expect(screen.getByText("2 个步骤")).toBeInTheDocument();
    expect(screen.queryByText("检索数据库结构")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "展开执行过程" }));

    expect(screen.getByText("检索数据库结构")).toBeVisible();
    expect(screen.getByText(/SQL execution failed/)).toBeVisible();
  });

  it("uses code styling for SQL generation steps", async () => {
    const user = userEvent.setup();
    render(
      <AgentTrace
        steps={[
          {
            step: "GenerateSql",
            status: "success",
            message: "SELECT COUNT(id) FROM items",
          },
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: "展开执行过程" }));

    expect(screen.getByText("SELECT COUNT(id) FROM items").tagName).toBe("CODE");
  });
});
