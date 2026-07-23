import { useState } from "react";

interface LongTextCellProps {
  text: string;
}

export default function LongTextCell({ text }: LongTextCellProps) {
  const [expanded, setExpanded] = useState(false);
  const [copyStatus, setCopyStatus] = useState("");

  const copyText = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopyStatus("已复制");
    } catch {
      setCopyStatus("复制失败");
    }
  };

  return (
    <div className="long-text-cell">
      <div
        className={`long-text-cell__content ${
          expanded ? "is-expanded" : "is-collapsed"
        }`}
      >
        {text}
      </div>
      <div className="long-text-cell__actions">
        <button
          type="button"
          aria-label={expanded ? "收起长文本" : "展开长文本"}
          onClick={() => setExpanded((current) => !current)}
        >
          {expanded ? "收起" : "展开"}
        </button>
        <button type="button" aria-label="复制长文本" onClick={copyText}>
          {copyStatus || "复制"}
        </button>
      </div>
      <span className="sr-status" role="status" aria-live="polite">
        {copyStatus}
      </span>
    </div>
  );
}
