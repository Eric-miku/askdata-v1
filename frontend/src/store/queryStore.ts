import { create } from "zustand";
import type { QueryResponse } from "../types/query";
import { queryData } from "../api/query";

interface QueryState {
  database: string;
  question: string;
  loading: boolean;
  error: string | null;
  result: QueryResponse | null;

  setDatabase: (db: string) => void;
  setQuestion: (q: string) => void;
  setError: (e: string | null) => void;
  setResult: (r: QueryResponse | null) => void;
  executeQuery: (questionOverride?: string) => Promise<void>;
}

export const useQueryStore = create<QueryState>((set, get) => ({
  database: "",
  question: "",
  loading: false,
  error: null,
  result: null,

  setDatabase: (db) => set({ database: db }),
  setQuestion: (q) => set({ question: q }),
  setError: (e) => set({ error: e }),
  setResult: (r) => set({ result: r }),

  executeQuery: async (questionOverride) => {
    const database = get().database;
    const question = (questionOverride ?? get().question).trim();

    if (!database) {
      set({ error: "请先选择数据库。" });
      return;
    }
    if (!question) {
      set({ error: "请输入问题。" });
      return;
    }

    try {
      set({
        loading: true,
        error: null,
      });

      const result = await queryData({
        database_id: database,
        question,
      });

      set({
        question,
        result,
        loading: false,
      });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : String(err),
        loading: false,
      });
    }
  },
}));
