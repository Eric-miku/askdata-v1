import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ClarificationResponse } from "../types/query";
import ClarificationPrompt from "./ClarificationPrompt";

const response: ClarificationResponse = {
  kind: "clarification",
  session_id: "s1",
  turn_id: "t1",
  trace: [],
  clarification_id: "c1",
  question: "你希望使用哪一种收入定义？",
  options: [
    { id: "gross", label: "Gross revenue", description: "折扣前收入" },
    { id: "net", label: "Net revenue", description: "折扣后收入" },
  ],
  recommended_option_id: "net",
};

describe("ClarificationPrompt", () => {
  it("renders choices inline and submits the selected option once", async () => {
    const user = userEvent.setup();
    const pending = new Promise<void>(() => undefined);
    const onResolve = vi.fn().mockReturnValue(pending);
    render(<ClarificationPrompt response={response} onResolve={onResolve} />);

    const option = screen.getByRole("button", { name: /Net revenue/ });
    expect(option).toHaveAttribute("aria-describedby", "clarification-c1-net");
    await user.dblClick(option);

    expect(onResolve).toHaveBeenCalledTimes(1);
    expect(onResolve).toHaveBeenCalledWith("c1", { option_id: "net" });
    expect(option).toBeDisabled();
  });

  it("reveals an accessible custom answer without opening a modal", async () => {
    const user = userEvent.setup();
    const onResolve = vi.fn().mockResolvedValue(undefined);
    const { container } = render(
      <ClarificationPrompt response={response} onResolve={onResolve} />,
    );

    await user.click(screen.getByRole("button", { name: "其他" }));
    const input = screen.getByRole("textbox", { name: "补充说明" });
    expect(input).toHaveFocus();
    expect(container.querySelector('[role="dialog"]')).not.toBeInTheDocument();

    await user.type(input, "使用已退款后的已确认收入");
    await user.click(screen.getByRole("button", { name: "提交补充说明" }));

    expect(onResolve).toHaveBeenCalledWith("c1", {
      text: "使用已退款后的已确认收入",
    });
  });
});
