import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { executeSql, type ExecuteSqlResponse } from "../api/query";
import type { ChatTurn } from "../types/query";
import AgentTrace from "./AgentTrace";
import { ResultChart } from "./ResultChart";
import { ResultTable } from "./ResultTable";
import SqlPanel from "./SqlPanel";

interface QueryResultViewProps {
  turn: ChatTurn;
  onRetry: (turnId: string) => void;
}

export function QueryResultView({ turn, onRetry }: QueryResultViewProps) {
  const response = turn.response;
  const sql = response?.sql?.trim();
  const shouldReadExecute = Boolean(
    turn.status === "success" &&
      sql &&
      (response?.rows === null || response?.rows === undefined),
  );
  const [readExecution, setReadExecution] =
    useState<ExecuteSqlResponse | null>(null);
  const [readStatus, setReadStatus] = useState<
    "idle" | "loading" | "success" | "error"
  >("idle");
  const [readError, setReadError] = useState<string | null>(null);
  const hydratedResponse = useMemo(() => {
    if (!response || !readExecution) {
      return response;
    }

    return {
      ...response,
      columns: readExecution.columns ?? response.columns,
      rows: readExecution.rows ?? response.rows,
      chart: readExecution.chart ?? response.chart,
      trace: readExecution.trace?.length
        ? [...(response.trace ?? []), ...readExecution.trace]
        : response.trace,
      error: readExecution.error ?? response.error,
    };
  }, [readExecution, response]);
  const hasTable = Boolean(
    hydratedResponse?.columns?.length &&
      hydratedResponse.rows !== null &&
      hydratedResponse.rows !== undefined,
  );
  const hasChart = Boolean(hydratedResponse?.chart);

  useEffect(() => {
    setReadExecution(null);
    setReadError(null);

    if (!shouldReadExecute || !sql) {
      setReadStatus("idle");
      return;
    }

    let cancelled = false;
    setReadStatus("loading");

    executeSql({
      database_id: turn.databaseId,
      sql,
    })
      .then((result) => {
        if (cancelled) {
          return;
        }
        setReadExecution(result);
        setReadError(result.error ?? null);
        setReadStatus(result.error ? "error" : "success");
      })
      .catch((error: unknown) => {
        if (cancelled) {
          return;
        }
        setReadError(error instanceof Error ? error.message : String(error));
        setReadStatus("error");
      });

    return () => {
      cancelled = true;
    };
  }, [shouldReadExecute, sql, turn.databaseId, turn.id]);

  return (
    <article className="chat-turn">
      <div className="chat-turn__question">{turn.question}</div>
      <div className="chat-turn__assistant">
        <div className="chat-turn__identity">
          <span className="chat-turn__mark">✦</span>
          <span>AskData</span>
          <small>AI 生成</small>
        </div>

        {turn.status === "loading" ? (
          <div className="answer-loading" role="status" aria-label="AskData 正在分析">
            <span />
            <span />
            <span />
            <p>正在分析数据并生成 SQL…</p>
          </div>
        ) : null}

        {hydratedResponse?.answer ? (
          <div className="chat-turn__answer">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {hydratedResponse.answer}
            </ReactMarkdown>
          </div>
        ) : null}

        {turn.status === "error" ? (
          <div className="chat-turn__error" role="alert">
            <div>
              <strong>这次查询没有完成</strong>
              <p>{turn.error || "请稍后重试或换一种问法。"}</p>
            </div>
            <button type="button" onClick={() => onRetry(turn.id)}>
              重试
            </button>
          </div>
        ) : null}

        {hydratedResponse?.trace?.length ? (
          <AgentTrace steps={hydratedResponse.trace} />
        ) : null}
        {hydratedResponse?.sql ? <SqlPanel sql={hydratedResponse.sql} /> : null}

        {readStatus === "loading" ? (
          <div className="chat-turn__read-execution" role="status">
            正在根据历史 SQL 恢复表格和图表…
          </div>
        ) : null}

        {readStatus === "error" ? (
          <div className="chat-turn__read-execution is-error" role="alert">
            历史结果恢复失败：{readError || "请稍后重试。"}
          </div>
        ) : null}

        {hasChart ? (
          <section className="chat-turn__result">
            <header>
              <strong>可视化</strong>
              <span>读时渲染</span>
            </header>
            <ResultChart chart={hydratedResponse?.chart} />
          </section>
        ) : null}

        {hasTable ? (
          <section className="chat-turn__result">
            <header>
              <strong>查询结果</strong>
              <span>{hydratedResponse?.rows?.length ?? 0} 行</span>
            </header>
            <ResultTable
              columns={hydratedResponse?.columns}
              rows={hydratedResponse?.rows}
            />
          </section>
        ) : null}
      </div>
    </article>
  );
}
