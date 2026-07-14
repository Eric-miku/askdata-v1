import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ChatComposer from "./ChatComposer";
import styles from "../styles.css?raw";

const databases = [
  { id: "demo", name: "Demo", tables_count: 2 },
  { id: "finance", name: "Finance", tables_count: 4 },
];

describe("ChatComposer", () => {
  it("submits with Enter and keeps Shift+Enter for a new line", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(
      <ChatComposer
        database="demo"
        databases={databases}
        loading={false}
        onDatabaseChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );
    const textbox = screen.getByRole("textbox", { name: "向 AskData 提问" });

    await user.type(textbox, "第一行{shift>}{enter}{/shift}第二行");
    expect(onSubmit).not.toHaveBeenCalled();
    expect(textbox).toHaveValue("第一行\n第二行");

    await user.type(textbox, "{enter}");
    expect(onSubmit).toHaveBeenCalledWith("第一行\n第二行");
    expect(textbox).toHaveValue("");
  });

  it("does not render a send button and blocks Enter while no database is selected", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(
      <ChatComposer
        database=""
        databases={databases}
        loading={false}
        onDatabaseChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    await user.type(
      screen.getByRole("textbox", { name: "向 AskData 提问" }),
      "问题{enter}",
    );

    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "发送问题" })).not.toBeInTheDocument();
  });

  it("does not submit while an input method composition is active", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(
      <ChatComposer
        database="demo"
        databases={databases}
        loading={false}
        onDatabaseChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );
    const textbox = screen.getByRole("textbox", { name: "向 AskData 提问" });
    await user.type(textbox, "正在输入中文");

    fireEvent.keyDown(textbox, { key: "Enter", isComposing: true });

    expect(onSubmit).not.toHaveBeenCalled();
    expect(textbox).toHaveValue("正在输入中文");
  });

  it("does not draw a focus outline around the message field", () => {
    expect(styles).toMatch(
      /\.chat-composer__input:focus-visible\s*{[^}]*outline:\s*none;/,
    );
  });
});
