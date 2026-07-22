export type ThemeMode = "light" | "dark";

export type QueryCellValue =
  | string
  | number
  | boolean
  | null
  | undefined
  | Record<string, unknown>
  | unknown[];

export interface DatabaseInfo {
  id: string;
  name: string;
  tables_count?: number;
}

export interface StructuredTraceItem {
  step: string;
  status: string;
  message: string;
}

export type TraceItem = StructuredTraceItem | string;

export interface QueryResponse {
  answer: string;
  sql: string | null;
  columns: string[] | null;
  rows: Record<string, QueryCellValue>[] | QueryCellValue[][] | null;
  chart?: Record<string, unknown> | null;
  trace?: TraceItem[];
  error?: string | null;
}

export interface SessionInfo {
  session_id: string;
  thread_id?: string;
  created_at: number;
  updated_at?: number;
  database_id?: string | null;
  question_count?: number;
}

export interface SessionHistoryItem {
  question: string;
  sql: string | null;
  answer: string;
  timestamp: number;
}

export interface SessionDetail extends SessionInfo {
  history: SessionHistoryItem[];
}

export interface ChatTurn {
  id: string;
  question: string;
  databaseId?: string;
  status: "loading" | "success" | "error";
  response?: QueryResponse;
  error?: string;
}
