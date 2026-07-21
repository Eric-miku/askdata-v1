import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import DatabasePicker from "./DatabasePicker";

describe("DatabasePicker", () => {
  it("supports arrow navigation and Escape focus restoration", async () => {
    const user = userEvent.setup();
    render(
      <DatabasePicker
        value="demo"
        databases={[
          { id: "demo", name: "Demo", tables_count: 2 },
          { id: "finance", name: "Finance", tables_count: 4 },
        ]}
        onChange={vi.fn()}
      />,
    );

    const trigger = screen.getByRole("button", { name: "选择数据库" });
    await user.click(trigger);
    expect(screen.getByRole("option", { name: "Demo 2 张表" })).toHaveFocus();

    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("option", { name: "Finance 4 张表" })).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox", { name: "数据库" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
