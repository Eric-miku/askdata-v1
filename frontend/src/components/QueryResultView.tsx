import type { ChatTurn } from "../types/query";
import AgentTrace from "./AgentTrace";
import { ResultTable } from "./ResultTable";
import SqlPanel from "./SqlPanel";

interface QueryResultViewProps {
  turn: ChatTurn;
  onRetry: (turnId: string) => void;
}

export function QueryResultView({ turn, onRetry }: QueryResultViewProps) {
  const response = turn.response;
  const hasTable = Boolean(
    response?.columns?.length &&
      response.rows !== null &&
      response.rows !== undefined,
  );

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

        {response?.answer ? <p className="chat-turn__answer">{response.answer}</p> : null}

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

        {response?.trace?.length ? <AgentTrace steps={response.trace} /> : null}
        {response?.sql ? <SqlPanel sql={response.sql} /> : null}

        {hasTable ? (
          <section className="chat-turn__result">
            <header>
              <strong>查询结果</strong>
              <span>{response?.rows?.length ?? 0} 行</span>
            </header>
            <ResultTable columns={response?.columns} rows={response?.rows} />
          </section>
        ) : null}
      </div>
    </article>
  );
}
