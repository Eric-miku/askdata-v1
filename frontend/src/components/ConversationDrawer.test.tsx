import { createRef, useRef, useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { SessionSummary } from "../types/query";
import ConversationDrawer from "./ConversationDrawer";

const sessions: SessionSummary[] = [
  {
    id: "session-schools",
    database_id: "education",
    title: "Schools by enrollment",
    created_at: "2026-07-15T10:00:00+00:00",
    updated_at: "2026-07-15T10:01:00+00:00",
  },
  {
    id: "session-revenue",
    database_id: "finance",
    title: "Monthly revenue",
    created_at: "2026-07-14T10:00:00+00:00",
    updated_at: "2026-07-14T10:01:00+00:00",
  },
];

function DrawerHarness({ onOpenSession = vi.fn() }) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button ref={triggerRef} type="button" onClick={() => setOpen(true)}>
        打开历史记录
      </button>
      <ConversationDrawer
        open={open}
        sessions={sessions}
        activeSessionId="session-schools"
        loading={false}
        error={null}
        triggerRef={triggerRef}
        onClose={() => setOpen(false)}
        onOpenSession={onOpenSession}
      />
    </>
  );
}

describe("ConversationDrawer", () => {
  it("focuses search, traps focus, and restores trigger focus after Escape", async () => {
    const user = userEvent.setup();
    render(<DrawerHarness />);

    const trigger = screen.getByRole("button", { name: "打开历史记录" });
    await user.click(trigger);

    const search = screen.getByRole("searchbox", { name: "搜索历史记录" });
    expect(search).toHaveFocus();
    expect(screen.getByRole("dialog", { name: "历史记录" })).toHaveAttribute(
      "aria-modal",
      "true",
    );

    await user.tab({ shift: true });
    expect(screen.getByRole("button", { name: "关闭历史记录" })).toHaveFocus();
    await user.tab({ shift: true });
    expect(screen.getByRole("option", { name: /Monthly revenue/ })).toHaveFocus();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog", { name: "历史记录" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("filters sessions and opens the selected conversation", async () => {
    const user = userEvent.setup();
    const onOpenSession = vi.fn();
    render(<DrawerHarness onOpenSession={onOpenSession} />);
    await user.click(screen.getByRole("button", { name: "打开历史记录" }));

    await user.type(screen.getByRole("searchbox", { name: "搜索历史记录" }), "revenue");

    expect(screen.queryByText("Schools by enrollment")).not.toBeInTheDocument();
    await user.click(screen.getByRole("option", { name: /Monthly revenue/ }));
    expect(onOpenSession).toHaveBeenCalledWith("session-revenue");
    expect(screen.queryByRole("dialog", { name: "历史记录" })).not.toBeInTheDocument();
  });

  it("renders loading, error, and empty-search feedback", async () => {
    const triggerRef = createRef<HTMLButtonElement>();
    const { rerender } = render(
      <ConversationDrawer
        open
        sessions={[]}
        activeSessionId={null}
        loading
        error={null}
        triggerRef={triggerRef}
        onClose={vi.fn()}
        onOpenSession={vi.fn()}
      />,
    );
    expect(screen.getByText("正在加载历史记录…")).toBeVisible();

    rerender(
      <ConversationDrawer
        open
        sessions={[]}
        activeSessionId={null}
        loading={false}
        error="历史记录不可用"
        triggerRef={triggerRef}
        onClose={vi.fn()}
        onOpenSession={vi.fn()}
      />,
    );
    expect(screen.getByText("历史记录不可用")).toBeVisible();

    rerender(
      <ConversationDrawer
        open
        sessions={[]}
        activeSessionId={null}
        loading={false}
        error={null}
        triggerRef={triggerRef}
        onClose={vi.fn()}
        onOpenSession={vi.fn()}
      />,
    );
    expect(screen.getByText("还没有历史对话")).toBeVisible();
  });
});
