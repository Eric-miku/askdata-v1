import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import type { DatabaseInfo } from "../types/query";
import { ChevronIcon, DatabaseIcon } from "./Icons";

interface DatabasePickerProps {
  value: string;
  databases: DatabaseInfo[];
  disabled?: boolean;
  onChange: (databaseId: string) => void;
}

export default function DatabasePicker({
  value,
  databases,
  disabled = false,
  onChange,
}: DatabasePickerProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const selected = databases.find((database) => database.id === value);

  const closePicker = (restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) {
      queueMicrotask(() => triggerRef.current?.focus());
    }
  };

  useEffect(() => {
    const handleDocumentMouseDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        closePicker();
      }
    };
    document.addEventListener("mousedown", handleDocumentMouseDown);
    return () => document.removeEventListener("mousedown", handleDocumentMouseDown);
  }, []);

  useEffect(() => {
    if (!open) {
      return;
    }
    const selectedIndex = Math.max(
      0,
      databases.findIndex((database) => database.id === value),
    );
    optionRefs.current[selectedIndex]?.focus();
  }, [databases, open, value]);

  const handleMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const options = optionRefs.current.filter(
      (option): option is HTMLButtonElement => Boolean(option),
    );
    const currentIndex = options.findIndex(
      (option) => option === document.activeElement,
    );
    if (event.key === "Escape") {
      event.preventDefault();
      closePicker(true);
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      const nextIndex = (currentIndex + direction + options.length) % options.length;
      options[nextIndex]?.focus();
    }
  };

  return (
    <div className="database-picker" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="database-picker__trigger"
        aria-label="选择数据库"
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            setOpen(true);
          }
        }}
      >
        <DatabaseIcon />
        <span>{selected?.name || "选择数据库"}</span>
        <ChevronIcon />
      </button>
      {open ? (
        <div
          className="database-picker__menu"
          role="listbox"
          aria-label="数据库"
          onKeyDown={handleMenuKeyDown}
        >
          {databases.length ? (
            databases.map((database, index) => (
              <button
                ref={(element) => {
                  optionRefs.current[index] = element;
                }}
                type="button"
                role="option"
                aria-selected={database.id === value}
                tabIndex={database.id === value ? 0 : -1}
                className="database-picker__option"
                key={database.id}
                onClick={() => {
                  onChange(database.id);
                  closePicker(true);
                }}
              >
                <span>{database.name || database.id}</span>
                <small>{database.tables_count ?? "-"} 张表</small>
              </button>
            ))
          ) : (
            <span className="database-picker__empty">暂无可用数据库</span>
          )}
        </div>
      ) : null}
    </div>
  );
}
