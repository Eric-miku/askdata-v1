import { describe, expect, it, vi } from "vitest";
import type {
  AnswerResponse,
  ClarificationResponse,
  ErrorResponse,
  QueryStreamEvent,
} from "../types/query";
import { queryStream } from "./queryStream";

const request = {
  database_id: "demo",
  question: "How many?",
  session_id: "s1",
};

const answer: AnswerResponse = {
  kind: "answer",
  session_id: "s1",
  turn_id: "t1",
  answer: "3",
  sql: "SELECT 3",
  columns: [],
  rows: [],
  chart: null,
  confidence: "high",
  trace: [],
};

const clarification: ClarificationResponse = {
  kind: "clarification",
  session_id: "s1",
  turn_id: "t1",
  trace: [],
  clarification_id: "c1",
  question: "Which revenue metric?",
  options: [{ id: "net", label: "Net revenue", description: null }],
  recommended_option_id: "net",
};

const errorResponse: ErrorResponse = {
  kind: "error",
  session_id: "s1",
  turn_id: "t1",
  trace: [],
  code: "query_failed",
  message: "Try again.",
  retryable: true,
  suggestions: ["Retry"],
};

function fakeFetch(
  chunks: string[],
  options: { ok?: boolean; status?: number; body?: boolean } = {},
) {
  return vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
    const encoded = chunks.map((chunk) => new TextEncoder().encode(chunk));
    let index = 0;
    const body = options.body === false
      ? null
      : new ReadableStream<Uint8Array>({
          pull(controller) {
            const chunk = encoded[index++];
            if (chunk) {
              controller.enqueue(chunk);
            } else {
              controller.close();
            }
          },
        });
    return {
      ok: options.ok ?? true,
      status: options.status ?? 200,
      body,
    } as Response;
  });
}

describe("queryStream", () => {
  it("parses split SSE frames and returns the mandatory final response", async () => {
    const chunks = [
      'event: trace\ndata: {"step":"RetrieveSchema","status":"success",',
      '"message":"ok","sequence":1}\n\nevent: final\ndata: ',
      `${JSON.stringify(answer)}\n\n`,
    ];
    const events: QueryStreamEvent[] = [];

    const result = await queryStream(request, events.push.bind(events), fakeFetch(chunks));

    expect(events).toEqual([
      {
        type: "trace",
        data: {
          step: "RetrieveSchema",
          status: "success",
          message: "ok",
          sequence: 1,
        },
      },
    ]);
    expect(result).toEqual(answer);
  });

  it("decodes UTF-8 split across byte chunks and accepts CRLF frames", async () => {
    const frame = `event: final\r\ndata: ${JSON.stringify({
      ...answer,
      answer: "共有三条。",
    })}\r\n\r\n`;
    const bytes = new TextEncoder().encode(frame);
    const splitAt = bytes.indexOf(new TextEncoder().encode("共")[0]) + 1;
    const chunks = [bytes.slice(0, splitAt), bytes.slice(splitAt)];
    const fetchImpl = vi.fn(async () => {
      let index = 0;
      return {
        ok: true,
        status: 200,
        body: new ReadableStream<Uint8Array>({
          pull(controller) {
            const chunk = chunks[index++];
            if (chunk) controller.enqueue(chunk);
            else controller.close();
          },
        }),
      } as Response;
    });

    await expect(queryStream(request, vi.fn(), fetchImpl)).resolves.toMatchObject({
      answer: "共有三条。",
    });
  });

  it("joins multi-line data and reports clarification and error lifecycle events", async () => {
    const clarificationJson = JSON.stringify(clarification);
    const split = clarificationJson.indexOf(',"options"') + 1;
    const frames = [
      `event: clarification\ndata: ${clarificationJson.slice(0, split)}\ndata: ${clarificationJson.slice(split)}\n\n`,
      `event: error\ndata: ${JSON.stringify(errorResponse)}\n\n`,
      `event: final\ndata: ${JSON.stringify(errorResponse)}\n\n`,
    ];
    const events: QueryStreamEvent[] = [];

    const result = await queryStream(request, events.push.bind(events), fakeFetch(frames));

    expect(events).toEqual([
      { type: "clarification", data: clarification },
      { type: "error", data: errorResponse },
    ]);
    expect(result).toEqual(errorResponse);
  });

  it("posts the request contract and propagates the abort signal", async () => {
    const controller = new AbortController();
    const fetchImpl = fakeFetch([
      `event: final\ndata: ${JSON.stringify(answer)}\n\n`,
    ]);

    await queryStream(request, vi.fn(), fetchImpl, controller.signal);

    expect(fetchImpl).toHaveBeenCalledWith(
      expect.stringMatching(/\/api\/query\/stream$/),
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          Accept: "text/event-stream",
          "Content-Type": "application/json",
        }),
        body: JSON.stringify(request),
        signal: controller.signal,
      }),
    );
  });

  it("rejects with AbortError when already cancelled", async () => {
    const controller = new AbortController();
    controller.abort();
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.signal?.aborted) {
        throw new DOMException("The operation was aborted.", "AbortError");
      }
      throw new Error("expected an aborted signal");
    });

    await expect(
      queryStream(request, vi.fn(), fetchImpl, controller.signal),
    ).rejects.toMatchObject({ name: "AbortError" });
  });

  it.each([
    {
      name: "non-success HTTP response",
      fetchImpl: fakeFetch([], { ok: false, status: 503 }),
      code: "http_error",
      message: "Query stream request failed with HTTP 503.",
    },
    {
      name: "missing response body",
      fetchImpl: fakeFetch([], { body: false }),
      code: "missing_body",
      message: "Query stream response body is unavailable.",
    },
    {
      name: "invalid event JSON",
      fetchImpl: fakeFetch(["event: trace\ndata: {not-json}\n\n"]),
      code: "invalid_event",
      message: "Query stream contained invalid JSON.",
    },
    {
      name: "stream closed without final",
      fetchImpl: fakeFetch([
        'event: trace\ndata: {"step":"RetrieveSchema","status":"success","message":"ok","sequence":1}\n\n',
      ]),
      code: "missing_final",
      message: "Query stream closed before the final response.",
    },
  ])("uses a stable error for $name", async ({ fetchImpl, code, message }) => {
    await expect(queryStream(request, vi.fn(), fetchImpl)).rejects.toEqual(
      expect.objectContaining({ code, message }),
    );
  });
});
