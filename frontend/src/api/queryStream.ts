import type {
  ClarificationResponse,
  ErrorResponse,
  QueryStreamEvent,
  TraceEvent,
  V2QueryRequest,
  V2QueryResponse,
} from "../types/query";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000/api";

export type QueryStreamErrorCode =
  | "http_error"
  | "missing_body"
  | "invalid_event"
  | "missing_final";

export class QueryStreamError extends Error {
  readonly code: QueryStreamErrorCode;

  constructor(code: QueryStreamErrorCode, message: string) {
    super(message);
    this.name = "QueryStreamError";
    this.code = code;
  }
}

export type QueryStreamFetch = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

interface ParsedFrame {
  event: string;
  payload: unknown;
}

function parseFrame(rawFrame: string): ParsedFrame | null {
  const lines = rawFrame.replace(/\r\n/g, "\n").split("\n");
  let event = "message";
  const data: string[] = [];

  for (const line of lines) {
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    let value = separator === -1 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);

    if (field === "event") event = value;
    if (field === "data") data.push(value);
  }

  if (data.length === 0) return null;
  try {
    return { event, payload: JSON.parse(data.join("\n")) };
  } catch {
    throw new QueryStreamError(
      "invalid_event",
      "Query stream contained invalid JSON.",
    );
  }
}

function isV2Response(payload: unknown): payload is V2QueryResponse {
  if (typeof payload !== "object" || payload === null) return false;
  const kind = (payload as { kind?: unknown }).kind;
  return (
    kind === "answer" ||
    kind === "clarification" ||
    kind === "partial" ||
    kind === "error"
  );
}

function lifecycleEvent(frame: ParsedFrame): QueryStreamEvent | null {
  if (frame.event === "trace") {
    return { type: "trace", data: frame.payload as TraceEvent };
  }
  if (frame.event === "clarification") {
    return {
      type: "clarification",
      data: frame.payload as ClarificationResponse,
    };
  }
  if (frame.event === "error") {
    return { type: "error", data: frame.payload as ErrorResponse };
  }
  return null;
}

export async function queryStream(
  request: V2QueryRequest,
  onEvent: (event: QueryStreamEvent) => void,
  fetchImpl: QueryStreamFetch = fetch,
  signal?: AbortSignal,
): Promise<V2QueryResponse> {
  const response = await fetchImpl(`${API_BASE_URL}/query/stream`, {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
    signal,
  });

  if (!response.ok) {
    throw new QueryStreamError(
      "http_error",
      `Query stream request failed with HTTP ${response.status}.`,
    );
  }
  if (!response.body) {
    throw new QueryStreamError(
      "missing_body",
      "Query stream response body is unavailable.",
    );
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResponse: V2QueryResponse | null = null;

  const consumeFrame = (rawFrame: string) => {
    const frame = parseFrame(rawFrame);
    if (!frame) return;

    if (frame.event === "final") {
      if (!isV2Response(frame.payload)) {
        throw new QueryStreamError(
          "invalid_event",
          "Query stream final response is invalid.",
        );
      }
      finalResponse = frame.payload;
      return;
    }

    const lifecycle = lifecycleEvent(frame);
    if (lifecycle) onEvent(lifecycle);
  };

  const consumeCompleteFrames = () => {
    while (true) {
      const boundary = /\r?\n\r?\n/.exec(buffer);
      if (!boundary || boundary.index === undefined) return;
      const rawFrame = buffer.slice(0, boundary.index);
      buffer = buffer.slice(boundary.index + boundary[0].length);
      consumeFrame(rawFrame);
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    consumeCompleteFrames();
  }
  buffer += decoder.decode();
  consumeCompleteFrames();
  if (buffer.trim()) consumeFrame(buffer);

  if (!finalResponse) {
    throw new QueryStreamError(
      "missing_final",
      "Query stream closed before the final response.",
    );
  }
  return finalResponse;
}
