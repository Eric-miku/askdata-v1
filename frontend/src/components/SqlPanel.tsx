import { useState } from "react";
import { ChevronIcon, CopyIcon } from "./Icons";

interface SqlPanelProps {
  sql: string;
}

export default function SqlPanel({ sql }: SqlPanelProps) {
  const [open, setOpen] = useState(true);
  const [copyStatus, setCopyStatus] = useState("");

  const copySql = async () => {
    try {
      await navigator.clipboard.writeText(sql);
      setCopyStatus("已复制");
    } catch {
      setCopyStatus("复制失败");
    }
  };

  return (
    <section className="sql-panel">
      <header className="sql-panel__header">
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
      <span className="sr-status" role="status" aria-live="polite">
        {copyStatus}
      </span>
    </section>
  );
}
