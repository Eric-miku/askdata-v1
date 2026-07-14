export type QueryCellValue =
  | string
  | number
  | boolean
  | null
  | undefined
  | Record<string, unknown>
  | unknown[];

export interface StructuredTraceItem {
  step: string;
  status: string;
  message: string;
}

export type TraceItem = StructuredTraceItem | string;

export interface QueryResponse {
  answer: string;
  sql?: string | null;
  columns?: string[] | null;
  rows?: Record<string, QueryCellValue>[] | QueryCellValue[][] | null;
  chart?: Record<string, unknown> | null;
  trace?: TraceItem[] | null;
  error?: string | null;
}

export interface DatabaseInfo {
  id: string;
  name: string;
  tables_count?: number;
}

export interface SessionInfo {
  session_id: string;
  created_at: number;
}

export type ChatTurnStatus = "loading" | "success" | "error";

export interface ChatTurn {
  id: string;
  question: string;
  databaseId: string;
  status: ChatTurnStatus;
  response?: QueryResponse;
  error?: string;
}

export type ThemeMode = "dark" | "light";
