export type QueryCellValue =
  | string
  | number
  | boolean
  | null
  | undefined
  | Record<string, unknown>
  | unknown[];

export type Confidence = "high" | "medium" | "low";
export type ResponseKind = "answer" | "clarification" | "partial" | "error";
export type TraceStatus = "started" | "success" | "retry" | "warning" | "error";

/** The exact operational trace contract returned by the V2 backend. */
export interface TraceEvent {
  step: string;
  status: TraceStatus;
  message: string;
  sequence: number;
}

/** Transitional V1 trace input retained until all result views use TraceEvent. */
export interface StructuredTraceItem {
  step: string;
  status: string;
  message: string;
  sequence?: number;
}

export type TraceItem = StructuredTraceItem | string;

export interface ChartSpec {
  type: "line" | "vertical_bar" | "horizontal_bar" | "pie" | "scatter";
  title: string;
  category_field: string | null;
  category_label: string | null;
  value_fields: string[];
  value_labels: Record<string, string>;
  reason: "time_series" | "comparison" | "ranking" | "proportion" | "correlation";
}

export interface ResponseBase {
  session_id: string;
  turn_id: string;
  trace: TraceEvent[];
}

export interface AnswerResponse extends ResponseBase {
  kind: "answer";
  answer: string;
  sql: string;
  columns: string[];
  rows: Record<string, QueryCellValue>[];
  chart: ChartSpec | null;
  confidence: Confidence;
}

export interface ClarificationOption {
  id: string;
  label: string;
  description: string | null;
}

export interface ClarificationResponse extends ResponseBase {
  kind: "clarification";
  clarification_id: string;
  question: string;
  options: ClarificationOption[];
  recommended_option_id: string | null;
}

export interface PartialResponse extends ResponseBase {
  kind: "partial";
  answer: string;
  limitations: string[];
  suggestions: string[];
  confidence: Confidence;
  sql: string | null;
  columns: string[];
  rows: Record<string, QueryCellValue>[];
  chart: ChartSpec | null;
}

export interface ErrorResponse extends ResponseBase {
  kind: "error";
  code: string;
  message: string;
  retryable: boolean;
  suggestions: string[];
}

/** The exact discriminated V2 response union. */
export type V2QueryResponse =
  | AnswerResponse
  | ClarificationResponse
  | PartialResponse
  | ErrorResponse;

/**
 * V1 response retained temporarily so the existing, user-designed result view
 * remains buildable while its state migration lands in Task 13.
 */
export interface LegacyQueryResponse {
  answer: string;
  sql?: string | null;
  columns?: string[] | null;
  rows?: Record<string, QueryCellValue>[] | QueryCellValue[][] | null;
  chart?: Record<string, unknown> | null;
  trace?: TraceItem[] | null;
  error?: string | null;
}

interface QueryResponseViewFields {
  answer?: string;
  sql?: string | null;
  columns?: string[] | null;
  rows?: Record<string, QueryCellValue>[] | QueryCellValue[][] | null;
  chart?: ChartSpec | Record<string, unknown> | null;
  trace?: TraceItem[] | null;
  error?: string | null;
}

/** Transitional API/UI response; narrow `kind` for V2 behavior. */
export type QueryResponse = (V2QueryResponse | LegacyQueryResponse) &
  QueryResponseViewFields;

export interface ClarificationResolution {
  clarification_id: string;
  option_id?: string;
  text?: string;
}

export interface QuestionQueryRequest {
  database_id: string;
  session_id?: string;
  question: string;
  clarification?: never;
}

export interface ClarificationQueryRequest {
  database_id: string;
  session_id: string;
  question?: never;
  clarification: ClarificationResolution;
}

export type V2QueryRequest = QuestionQueryRequest | ClarificationQueryRequest;

export type QueryStreamEvent =
  | { type: "trace"; data: TraceEvent }
  | { type: "clarification"; data: ClarificationResponse }
  | { type: "error"; data: ErrorResponse };

export interface DatabaseInfo {
  id: string;
  name: string;
  tables_count?: number;
}

/** Response from the transitional POST /sessions endpoint. */
export interface SessionInfo {
  session_id: string;
  created_at: number;
}

export interface SessionSummary {
  id: string;
  database_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export type ClarificationStatus = "pending" | "resolved";

export interface RestoredClarification {
  id: string;
  turn_id: string;
  prompt: string;
  options: ClarificationOption[];
  resolution: Omit<ClarificationResolution, "clarification_id"> | null;
  status: ClarificationStatus;
  created_at: string;
  resolved_at: string | null;
}

export interface RestoredTurn {
  id: string;
  question: string;
  response_kind: ResponseKind;
  answer: string | null;
  sql: string | null;
  result_preview: Record<string, QueryCellValue>[] | null;
  chart: ChartSpec | null;
  confidence: Confidence | null;
  error: Record<string, unknown> | null;
  trace: TraceEvent[];
  created_at: string;
  clarification: RestoredClarification | null;
}

export interface RestoredSession extends SessionSummary {
  turns: RestoredTurn[];
}

export type ChatTurnStatus =
  | "loading"
  | "awaiting_clarification"
  | "success"
  | "partial"
  | "error";

export interface ChatTurn {
  id: string;
  question: string;
  databaseId: string;
  status: ChatTurnStatus;
  response?: QueryResponse;
  error?: string;
}

export type ThemeMode = "dark" | "light";
