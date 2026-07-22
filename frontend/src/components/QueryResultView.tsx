import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { downloadQueryExport, executeSql } from "../api/query";
import type { ChatTurn, QueryResponse } from "../types/query";
import AgentTrace from "./AgentTrace";
import { ResultChart } from "./ResultChart";
import { ResultTable } from "./ResultTable";
import SqlPanel from "./SqlPanel";

interface QueryResultViewProps {
  turn: ChatTurn;
  onRetry: (turnId: string) => void;
  onSuggestion?: (question: string) => void;
}

export function QueryResultView({ turn, onRetry, onSuggestion }: QueryResultViewProps) {
  const [restored, setRestored] = useState<QueryResponse | null>(null);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const [exporting, setExporting] = useState<"csv" | "xlsx" | null>(null);
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
          chart: result.chart,
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
  const exportResult = async (format: "csv" | "xlsx") => {
    if (!turn.databaseId || !response?.sql) return;
    setExporting(format);
    try {
      await downloadQueryExport({ database_id: turn.databaseId, sql: response.sql, question: turn.question, format });
    } finally {
      setExporting(null);
    }
  };
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
      {response?.sql ? <SqlPanel sql={response.sql} databaseId={turn.databaseId} /> : null}
      {response?.chart ? (
        <section className="chat-turn__result">
          <header><strong>可视化</strong></header>
          <ResultChart chart={response.chart} loading={loading} />
        </section>
      ) : null}
      {response?.analysis ? (
        <section className="chat-turn__analysis">
          <header><strong>数据洞察</strong><span>{response.analysis.summary}</span></header>
          {response.analysis.insights.map((insight, index) => (
            <div className="analysis-insight" key={`${insight.type}-${index}`}>
              <strong>{insight.title}</strong>
              <p>{insight.statement}</p>
              <small>证据：{insight.evidence.map((item) => `${item.column} 第 ${item.row_index + 1} 行 = ${String(item.value)}`).join("；")}</small>
            </div>
          ))}
          {response.analysis.insufficient_reason ? <p className="analysis-empty">{response.analysis.insufficient_reason}</p> : null}
        </section>
      ) : null}
      {response ? (
        <section className="chat-turn__result">
          <header>
            <strong>查询结果</strong>
            {response.sql && turn.databaseId ? <span className="result-export-actions">
              <button type="button" disabled={Boolean(exporting)} onClick={() => void exportResult("csv")}>{exporting === "csv" ? "导出中" : "CSV"}</button>
              <button type="button" disabled={Boolean(exporting)} onClick={() => void exportResult("xlsx")}>{exporting === "xlsx" ? "导出中" : "Excel"}</button>
            </span> : null}
          </header>
          <ResultTable columns={response.columns} rows={response.rows} loading={loading} />
        </section>
      ) : null}
      {response?.suggestions?.length ? (
        <section className="chat-turn__suggestions">
          <strong>继续分析</strong>
          <div>{response.suggestions.map((suggestion) => <button type="button" key={suggestion} onClick={() => onSuggestion?.(suggestion)}>{suggestion}</button>)}</div>
        </section>
      ) : null}
      {response?.trace?.length ? <AgentTrace steps={response.trace} /> : null}
      </div>
    </article>
  );
}
