import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
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
});
