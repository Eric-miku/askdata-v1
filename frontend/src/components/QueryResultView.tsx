import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type {
  ChartSpec,
  ChatTurn,
  ClarificationResolution,
  ClarificationResponse,
  PartialResponse,
  QueryCellValue,
  QueryResponse,
} from "../types/query";
import AgentTrace from "./AgentTrace";
import ChartPanel from "./ChartPanel";
import ClarificationPrompt from "./ClarificationPrompt";
import { ResultTable } from "./ResultTable";
import SqlPanel from "./SqlPanel";

interface QueryResultViewProps {
  turn: ChatTurn;
  onRetry: (turnId: string) => void;
  onCancel?: () => void;
  onResolveClarification?: (
    turnId: string,
    clarificationId: string,
    resolution: Omit<ClarificationResolution, "clarification_id">,
  ) => void | Promise<void>;
}

function responseKind(response?: QueryResponse): string | undefined {
  return response && "kind" in response ? response.kind : undefined;
}

function isClarification(
  response?: QueryResponse,
): response is QueryResponse & ClarificationResponse {
  return responseKind(response) === "clarification";
}

function isPartial(response?: QueryResponse): response is QueryResponse & PartialResponse {
  return responseKind(response) === "partial";
}

function isChartSpec(value: unknown): value is ChartSpec {
  if (!value || typeof value !== "object") return false;
  const type = (value as { type?: unknown }).type;
  return ["line", "vertical_bar", "horizontal_bar", "pie", "scatter"].includes(
    String(type),
  );
}

function chartRows(
  rows: QueryResponse["rows"],
): Record<string, QueryCellValue>[] | null {
  if (!Array.isArray(rows) || rows.some((row) => Array.isArray(row))) return null;
  return rows as Record<string, QueryCellValue>[];
}

export function QueryResultView({
  turn,
  onRetry,
  onCancel,
  onResolveClarification,
}: QueryResultViewProps) {
  const response = turn.response;
  const clarification = isClarification(response) ? response : null;
  const partial = isPartial(response) ? response : null;
  const kind = responseKind(response);
  const errorMessage =
    kind === "error" && response && "message" in response
      ? String(response.message)
      : turn.error || "请稍后重试或换一种问法。";
  const safeChartRows = chartRows(response?.rows);
  const chart = isChartSpec(response?.chart) ? response.chart : null;
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
            {onCancel ? (
              <button type="button" onClick={onCancel}>
                取消
              </button>
            ) : null}
          </div>
        ) : null}

        {clarification && onResolveClarification ? (
          <ClarificationPrompt
            response={clarification}
            onResolve={(clarificationId, resolution) =>
              onResolveClarification(turn.id, clarificationId, resolution)
            }
          />
        ) : null}

        {response?.answer ? (
          <div className="chat-turn__answer">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{response.answer}</ReactMarkdown>
          </div>
        ) : null}

        {turn.status === "error" ? (
          <div className="chat-turn__error" role="alert">
            <div>
              <strong>这次查询没有完成</strong>
              <p>{errorMessage}</p>
            </div>
            <button type="button" onClick={() => onRetry(turn.id)}>
              重试
            </button>
          </div>
        ) : null}

        {partial ? (
          <section className="chat-turn__partial" role="status" aria-label="部分结果">
            <strong>这是目前可靠的部分结果</strong>
            {partial.limitations.length ? (
              <ul>
                {partial.limitations.map((limitation) => (
                  <li key={limitation}>{limitation}</li>
                ))}
              </ul>
            ) : null}
            {partial.suggestions.length ? (
              <p>建议：{partial.suggestions.join("；")}</p>
            ) : null}
          </section>
        ) : null}

        {response?.trace?.length ? <AgentTrace steps={response.trace} /> : null}
        {response?.sql ? <SqlPanel sql={response.sql} /> : null}
        {chart && safeChartRows ? <ChartPanel spec={chart} rows={safeChartRows} /> : null}

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
