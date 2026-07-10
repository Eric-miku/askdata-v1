export type QueryCellValue =
  | string
  | number
  | boolean
  | null
  | undefined
  | Record<string, unknown>
  | unknown[];

export interface QueryResponse {
  answer: string;
  sql?: string | null;
  columns?: string[] | null;
  rows?: Record<string, QueryCellValue>[] | QueryCellValue[][] | null;
  chart?: unknown;
  trace: string[];
  error?: string | null;
}
