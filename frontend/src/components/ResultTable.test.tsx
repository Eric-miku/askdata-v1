import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ResultTable } from "./ResultTable";
import styles from "../styles.css?raw";

describe("ResultTable pagination", () => {
  it("shows only the current page number as plain text between the arrows", async () => {
    const user = userEvent.setup();
    const rows = Array.from({ length: 15 }, (_, index) => ({ id: index + 1 }));
    const { container } = render(<ResultTable columns={["id"]} rows={rows} />);

    expect(container.querySelector(".ant-pagination-prev")).toBeInTheDocument();
    expect(container.querySelector(".ant-pagination-next")).toBeInTheDocument();
    expect(container.querySelector(".ant-pagination-item-active")).toHaveTextContent("1");

    const nextButton = container.querySelector<HTMLButtonElement>(
      ".ant-pagination-next button",
    );
    expect(nextButton).not.toBeNull();
    await user.click(nextButton!);
    expect(container.querySelector(".ant-pagination-item-active")).toHaveTextContent("2");

    expect(styles).toMatch(
      /\.result-table \.ant-pagination-item-active\s*{[^}]*background:\s*transparent[^}]*border:\s*0/,
    );
    expect(styles).toMatch(
      /\.result-table \.ant-pagination-item-active\s*{[^}]*display:\s*inline-flex[^}]*align-items:\s*center[^}]*justify-content:\s*center/,
    );
  });

  it("keeps the page-size control typographic, borderless, and searchable-icon free", async () => {
    const user = userEvent.setup();
    const rows = Array.from({ length: 15 }, (_, index) => ({ id: index + 1 }));
    const { container } = render(<ResultTable columns={["id"]} rows={rows} />);

    const selector = container.querySelector<HTMLElement>(".ant-select-selector");
    expect(selector).not.toBeNull();
    await user.click(selector!);

    expect(
      container.querySelector(".result-table__page-size-chevron"),
    ).toBeInTheDocument();
    expect(container.querySelector(".anticon-search")).not.toBeInTheDocument();
    expect(styles).toMatch(
      /\.result-table \.ant-pagination-options \.ant-select-selection-item\s*{[^}]*font-family:\s*inherit[^}]*font-weight:\s*400/,
    );
    expect(styles).toMatch(
      /\.result-table \.ant-pagination-options \.ant-select-selector\s*{[^}]*border:\s*0\s*!important[^}]*box-shadow:\s*none\s*!important/,
    );
    expect(styles).toMatch(
      /\.result-table \.ant-select-open \.result-table__page-size-chevron\s*{[^}]*transform:\s*rotate\(180deg\)/,
    );
    expect(styles).toMatch(
      /\.result-table__page-size-dropdown\s*{[^}]*font-family:\s*inherit/,
    );
    expect(styles).toMatch(
      /\.result-table__page-size-dropdown \.ant-select-item-option-active:not\(\.ant-select-item-option-disabled\)\s*{[^}]*background:\s*var\(--surface-raised\)/,
    );
  });

  it("keeps the table bottom border inside complete rounded corners", () => {
    expect(styles).toMatch(
      /\.result-table \.ant-table-container\s*{[^}]*overflow:\s*hidden[^}]*border-bottom:\s*1px solid var\(--border\)[^}]*border-radius:\s*10px/,
    );
  });
});

describe("ResultTable long text", () => {
  it("wraps, expands, collapses, and copies long strings", async () => {
    const user = userEvent.setup();
    const longSql = `CREATE TABLE schools (${" column_name TEXT,".repeat(20)} id INTEGER)`;
    const writeText = vi
      .spyOn(navigator.clipboard, "writeText")
      .mockResolvedValue(undefined);

    render(<ResultTable columns={["sql"]} rows={[{ sql: longSql }]} />);

    const content = screen.getByText(longSql);
    expect(content).toHaveClass("long-text-cell__content", "is-collapsed");

    await user.click(screen.getByRole("button", { name: "展开长文本" }));
    expect(content).toHaveClass("is-expanded");

    await user.click(screen.getByRole("button", { name: "复制长文本" }));
    expect(writeText).toHaveBeenCalledWith(longSql);
    expect(screen.getByRole("button", { name: "复制长文本" })).toHaveTextContent(
      "已复制",
    );

    await user.click(screen.getByRole("button", { name: "收起长文本" }));
    expect(content).toHaveClass("is-collapsed");

    expect(styles).toMatch(
      /\.long-text-cell\s*{[^}]*font-family:\s*"SFMono-Regular"/,
    );
    expect(styles).toMatch(
      /\.long-text-cell__content\s*{[^}]*white-space:\s*pre-wrap[^}]*overflow-wrap:\s*anywhere/,
    );
  });
});
