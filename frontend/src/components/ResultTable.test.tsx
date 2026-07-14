import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
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
  });

  it("keeps the table bottom border inside complete rounded corners", () => {
    expect(styles).toMatch(
      /\.result-table \.ant-table-container\s*{[^}]*overflow:\s*hidden[^}]*border-bottom:\s*1px solid var\(--border\)[^}]*border-radius:\s*10px/,
    );
  });
});
