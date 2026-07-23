import { useEffect, useRef, useState } from "react";
import type {
  ClarificationResolution,
  ClarificationResponse,
} from "../types/query";

interface ClarificationPromptProps {
  response: ClarificationResponse;
  onResolve: (
    clarificationId: string,
    resolution: Omit<ClarificationResolution, "clarification_id">,
  ) => void | Promise<void>;
}

export default function ClarificationPrompt({
  response,
  onResolve,
}: ClarificationPromptProps) {
  const [showCustom, setShowCustom] = useState(false);
  const [customText, setCustomText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const customInputRef = useRef<HTMLInputElement>(null);
  const submissionStarted = useRef(false);

  useEffect(() => {
    if (showCustom) customInputRef.current?.focus();
  }, [showCustom]);

  const submit = async (
    resolution: Omit<ClarificationResolution, "clarification_id">,
  ) => {
    if (submissionStarted.current) return;
    submissionStarted.current = true;
    setSubmitting(true);
    try {
      await onResolve(response.clarification_id, resolution);
    } catch {
      submissionStarted.current = false;
      setSubmitting(false);
    }
  };

  const revealCustom = () => {
    setShowCustom(true);
  };

  return (
    <section
      className="clarification-prompt"
      aria-labelledby={`clarification-${response.clarification_id}-question`}
    >
      <p
        className="clarification-prompt__question"
        id={`clarification-${response.clarification_id}-question`}
      >
        {response.question}
      </p>
      <div className="clarification-prompt__options">
        {response.options.map((option) => {
          const descriptionId = `clarification-${response.clarification_id}-${option.id}`;
          return (
            <button
              type="button"
              className="clarification-prompt__option"
              aria-label={option.label}
              aria-describedby={descriptionId}
              disabled={submitting}
              key={option.id}
              onClick={() => void submit({ option_id: option.id })}
            >
              <span>{option.label}</span>
              {option.id === response.recommended_option_id ? (
                <small className="clarification-prompt__recommended">推荐</small>
              ) : null}
              <small id={descriptionId}>
                {option.description || "选择此解释继续查询"}
              </small>
            </button>
          );
        })}
        {!showCustom ? (
          <button
            type="button"
            className="clarification-prompt__other"
            disabled={submitting}
            onClick={revealCustom}
          >
            其他
          </button>
        ) : (
          <form
            className="clarification-prompt__custom"
            onSubmit={(event) => {
              event.preventDefault();
              const text = customText.trim();
              if (text) void submit({ text });
            }}
          >
            <label htmlFor={`clarification-${response.clarification_id}-custom`}>
              补充说明
            </label>
            <div>
              <input
                ref={customInputRef}
                id={`clarification-${response.clarification_id}-custom`}
                value={customText}
                disabled={submitting}
                onChange={(event) => setCustomText(event.target.value)}
              />
              <button
                type="submit"
                disabled={submitting || !customText.trim()}
                aria-label="提交补充说明"
              >
                继续
              </button>
            </div>
          </form>
        )}
      </div>
    </section>
  );
}
