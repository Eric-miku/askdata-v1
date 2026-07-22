import { useState } from "react";
import { explainSql } from "../api/query";
import type { QueryPlanResult } from "../types/query";
import { ChevronIcon, CopyIcon } from "./Icons";

interface SqlPanelProps {
  sql: string;
  databaseId?: string;
}

export default function SqlPanel({ sql, databaseId }: SqlPanelProps) {
  const [open, setOpen] = useState(true);
  const [copyStatus, setCopyStatus] = useState("");
  const [plan, setPlan] = useState<QueryPlanResult | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [planLoading, setPlanLoading] = useState(false);

  const copySql = async () => {
    try {
      await navigator.clipboard.writeText(sql);
      setCopyStatus("已复制");
    } catch {
      setCopyStatus("复制失败");
    }
  };

  const inspectPlan = async () => {
    if (!databaseId) return;
    if (plan) {
      setPlan(null);
      return;
    }
    setPlanLoading(true);
    setPlanError(null);
    try {
      setPlan(await explainSql({ database_id: databaseId, sql }));
    } catch (reason) {
      setPlanError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setPlanLoading(false);
    }
  };

  return (
    <section className="sql-panel">
      <header className="sql-panel__header">
        <div className="sql-panel__actions">
        {databaseId ? <button type="button" className="sql-panel__explain" onClick={() => void inspectPlan()} disabled={planLoading}>
          {planLoading ? "分析中" : plan ? "收起计划" : "执行计划"}
        </button> : null}
        <button
          type="button"
          className="sql-panel__toggle"
          aria-label={open ? "折叠 SQL" : "展开 SQL"}
          aria-expanded={open}
          onClick={() => setOpen((current) => !current)}
        >
          <ChevronIcon className={open ? "is-open" : ""} />
          <span>GENERATED SQL</span>
        </button>
        </div>
        <button
          type="button"
          className="sql-panel__copy"
          aria-label="复制 SQL"
          onClick={copySql}
        >
          <CopyIcon />
          <span>{copyStatus || "复制"}</span>
        </button>
      </header>
      {open ? (
        <pre className="sql-panel__code">
          <code>{sql}</code>
        </pre>
      ) : null}
      {planError ? <p className="sql-panel__plan-error" role="alert">{planError}</p> : null}
      {plan ? <section className="sql-panel__plan" aria-label="SQL 执行计划">
        <ol>{plan.plan.map((item) => <li key={`${item.id}-${item.parent}-${item.detail}`}><code>{item.detail}</code></li>)}</ol>
        {plan.suggestions.length ? <div className="sql-panel__suggestions">
          {plan.suggestions.map((item, index) => <article key={`${item.type}-${index}`}>
            <strong>{item.type === "index_candidate" ? "候选索引" : "临时排序"}</strong>
            <span>{item.reason}</span>
            {item.sql ? <code>{item.sql}</code> : null}
          </article>)}
        </div> : <p className="sql-panel__plan-empty">当前计划未发现明确的索引候选。</p>}
        <small>{plan.warnings.join("；")}</small>
      </section> : null}
      <span className="sr-status" role="status" aria-live="polite">
        {copyStatus}
      </span>
    </section>
  );
}
