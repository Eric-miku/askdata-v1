import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ResultTable } from "./ResultTable";

describe("ResultTable pagination", () => {
  it("keeps only previous and next navigation without numbered page buttons", () => {
    const rows = Array.from({ length: 15 }, (_, index) => ({ id: index + 1 }));
    const { container } = render(<ResultTable columns={["id"]} rows={rows} />);

    expect(container.querySelector(".ant-pagination-prev")).toBeInTheDocument();
    expect(container.querySelector(".ant-pagination-next")).toBeInTheDocument();
    expect(container.querySelector(".ant-pagination-item a")).not.toBeInTheDocument();
  });
});
