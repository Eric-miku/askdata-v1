import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import SqlPanel from "./SqlPanel";

describe("SqlPanel", () => {
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
});
