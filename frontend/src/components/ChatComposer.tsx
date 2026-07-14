import { useState, type KeyboardEvent } from "react";
import type { DatabaseInfo } from "../types/query";
import DatabasePicker from "./DatabasePicker";
import { ArrowUpIcon } from "./Icons";

interface ChatComposerProps {
  database: string;
  databases: DatabaseInfo[];
  loading: boolean;
  validationError?: string | null;
  onDatabaseChange: (databaseId: string) => void;
  onSubmit: (text: string) => void | Promise<void>;
}

export default function ChatComposer({
  database,
  databases,
  loading,
  validationError,
  onDatabaseChange,
  onSubmit,
}: ChatComposerProps) {
  const [value, setValue] = useState("");
  const canSubmit = Boolean(database && value.trim() && !loading);

  const submit = () => {
    if (!canSubmit) {
      return;
    }
    const question = value.trim();
    setValue("");
    void onSubmit(question);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className="chat-composer">
      <textarea
        className="chat-composer__input"
        aria-label="向 AskData 提问"
        placeholder="询问你的数据，例如：哪个学校的学生人数最多？"
        rows={2}
        value={value}
        disabled={loading}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={handleKeyDown}
      />
      <div className="chat-composer__footer">
        <DatabasePicker
          value={database}
          databases={databases}
          disabled={loading}
          onChange={onDatabaseChange}
        />
        <span className="chat-composer__hint">Enter 发送 · Shift+Enter 换行</span>
        <button
          type="button"
          className="chat-composer__send"
          aria-label="发送问题"
          disabled={!canSubmit}
          onClick={submit}
        >
          <ArrowUpIcon />
        </button>
      </div>
      {validationError ? (
        <p className="chat-composer__error" role="alert">
          {validationError}
        </p>
      ) : null}
    </div>
  );
}
