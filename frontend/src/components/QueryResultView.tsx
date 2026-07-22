import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { executeSql } from "../api/query";
import type { ChatTurn, QueryResponse } from "../types/query";
import { buildChartFromRows } from "../utils/chartBuilder";
import AgentTrace from "./AgentTrace";
import { ResultChart } from "./ResultChart";
import { ResultTable } from "./ResultTable";
import SqlPanel from "./SqlPanel";

interface QueryResultViewProps {
  turn: ChatTurn;
  onRetry: (turnId: string) => void;
}

export function QueryResultView({ turn, onRetry }: QueryResultViewProps) {
  const [restored, setRestored] = useState<QueryResponse | null>(null);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const response = restored || turn.response;
  const needsRestore = Boolean(
    turn.status === "success" && turn.databaseId && response?.sql && response.columns === null && response.rows === null,
  );

  useEffect(() => {
    let active = true;
    if (!needsRestore || !turn.databaseId || !response?.sql) return undefined;
    setRestoreError(null);
    void executeSql({ database_id: turn.databaseId, sql: response.sql })
      .then((result) => {
        if (!active) return;
        setRestored({
          ...response,
          ...result,
          chart: result.chart ?? buildChartFromRows(result.columns, result.rows),
          sql: response.sql,
        });
      })
      .catch((error) => {
        if (active) setRestoreError(error instanceof Error ? error.message : String(error));
      });
    return () => { active = false; };
  }, [needsRestore, response, turn.databaseId]);

  const loading = turn.status === "loading" || needsRestore;
  const error = turn.error || response?.error || restoreError;
  return (
    <article className="chat-turn" aria-busy={loading}>
      <header className="chat-turn__question">{turn.question}</header>
      <div className="chat-turn__assistant">
        <div className="chat-turn__identity">
          <span className="chat-turn__mark">A</span>
          <span>AskData</span>
          {loading ? <small>Working</small> : null}
        </div>
      {loading ? (
        <div className="answer-loading" role="status">
          <span />
          <span />
          <span />
          <p>查询中...</p>
        </div>
      ) : null}
      {error ? (
        <div className="chat-turn__error" role="alert">
          <div>
            <strong>查询失败</strong>
            <p>{error}</p>
          </div>
          <button type="button" onClick={() => onRetry(turn.id)}>重试</button>
        </div>
      ) : null}
      {response?.answer ? (
        <div className="chat-turn__answer">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{response.answer}</ReactMarkdown>
        </div>
      ) : null}
      {response?.sql ? <SqlPanel sql={response.sql} /> : null}
      {response?.chart ? (
        <section className="chat-turn__result">
          <header><strong>可视化</strong></header>
          <ResultChart chart={response.chart} loading={loading} />
        </section>
      ) : null}
      {response ? (
        <section className="chat-turn__result">
          <header><strong>查询结果</strong></header>
          <ResultTable columns={response.columns} rows={response.rows} loading={loading} />
        </section>
      ) : null}
      {response?.trace?.length ? <AgentTrace steps={response.trace} /> : null}
      </div>
    </article>
  );
}
