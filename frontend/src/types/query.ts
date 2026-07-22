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

export interface KnowledgeEntry {
  id: string;
  kind: "term" | "metric";
  standard_name: string;
  definition: string;
  category: string;
  scope: string;
  status: "draft" | "published" | "disabled";
  aliases: string[];
  mappings: Array<Record<string, unknown>>;
  formula: string;
  aggregation: string;
  unit: string;
  time_field: string;
  examples: string[];
  version: number;
  changelog: string;
  updated_by: string;
  updated_at: number;
}

export type KnowledgeEntryInput = Omit<
  KnowledgeEntry,
  "id" | "version" | "updated_by" | "updated_at"
>;

export interface ManagedDataSource {
  id: string;
  name: string;
  kind: "sqlite";
  path: string;
  enabled: boolean;
  health: "unknown" | "healthy" | "unhealthy";
  last_error?: string | null;
  table_count: number;
  last_tested_at?: number | null;
  last_synced_at?: number | null;
  schema_fingerprint?: string | null;
  schema_changed: boolean;
  schema_change_summary?: SchemaChangeSummary | null;
  index_count: number;
  created_at: number;
  updated_at: number;
}

export interface SchemaChangeSummary {
  changed: boolean;
  initial_sync: boolean;
  tables_added: string[];
  tables_removed: string[];
  tables_changed: string[];
}

export interface SchemaColumn {
  name: string;
  type: string;
  nullable: boolean;
  default: string | number | null;
  primary_key_position: number;
}

export interface SchemaCatalogSnapshot {
  source_id: string;
  fingerprint: string;
  previous_fingerprint?: string | null;
  synced_at: number;
  change_summary: SchemaChangeSummary;
  catalog: {
    dialect: "sqlite";
    fingerprint: string;
    table_count: number;
    column_count: number;
    index_count: number;
    tables: Array<{
      name: string;
      ddl: string;
      columns: SchemaColumn[];
      primary_key: string[];
      foreign_keys: Array<Record<string, unknown>>;
      indexes: Array<{ name: string; unique: boolean; columns: string[] }>;
    }>;
  };
}

export interface QueryPlanResult {
  success: true;
  normalized_sql: string;
  plan: Array<{ id: number; parent: number; detail: string }>;
  suggestions: Array<{
    type: "index_candidate" | "temporary_sort";
    table?: string;
    columns?: string[];
    reason: string;
    sql?: string;
    automatic: false;
  }>;
  warnings: string[];
}

export interface ManagedDataSourceInput {
  id: string;
  name: string;
  path: string;
  enabled: boolean;
}

export interface PermissionPolicy {
  id: string;
  user_id: string;
  database_id: string;
  table_name?: string | null;
  field_name?: string | null;
  can_query: boolean;
  can_export: boolean;
  row_filter?: string | null;
  created_at: number;
}

export type PermissionPolicyInput = Omit<PermissionPolicy, "id" | "created_at">;

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
  analysis?: {
    summary: string;
    insights: Array<{
      type: string;
      title: string;
      statement: string;
      method?: string;
      evidence: Array<{ column: string; row_index: number; value: QueryCellValue }>;
    }>;
    insufficient_reason?: string | null;
  } | null;
  suggestions?: string[];
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
