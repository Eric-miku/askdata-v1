import { useMemo, useState } from "react";
import type { StructuredTraceItem, TraceItem } from "../types/query";
import { ChevronIcon } from "./Icons";

interface Props {
  steps: TraceItem[];
}

const stepLabels: Record<string, string> = {
  RetrieveSchema: "检索数据库结构",
  GenerateSql: "生成 SQL",
  ValidateSql: "校验 SQL",
  ExecuteSql: "执行 SQL",
  RepairSql: "修复 SQL",
  ReviewAnswerShape: "检查答案结构",
  AnalyzeResult: "生成回答",
  UnderstandQuestion: "理解问题结构",
  ResolveBusinessTerms: "匹配业务术语",
};

function normalizeStep(step: TraceItem, index: number): StructuredTraceItem {
  if (typeof step === "string") {
    return {
      step: `Log-${index + 1}`,
      status: /失败|error/i.test(step) ? "error" : "success",
      message: step,
    };
  }
  return step;
}

function stepLabel(step: string): string {
  if (/^Reason-/i.test(step)) {
    return "分析问题";
  }
  if (/^Log-/i.test(step)) {
    return "执行日志";
  }
  return stepLabels[step] || step;
}

export default function AgentTrace({ steps }: Props) {
  const [open, setOpen] = useState(false);
  const normalized = useMemo(
    () => steps.map((step, index) => normalizeStep(step, index)),
    [steps],
  );
  const hasError = normalized.some((step) => step.status === "error");
  const hasRetry = normalized.some((step) => step.status === "retry");
  const summary = hasError ? "执行失败" : hasRetry ? "已完成重试" : "已完成";

  if (!steps.length) {
    return null;
  }

  return (
    <section className="agent-trace">
      <button
        type="button"
        className="agent-trace__summary"
        aria-label={open ? "折叠思考过程" : "展开思考过程"}
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <ChevronIcon className={open ? "is-open" : ""} />
        <strong>思考过程</strong>
        <span className={`agent-trace__state is-${hasError ? "error" : "success"}`}>
          {summary}
        </span>
        <span className="agent-trace__count">{steps.length} 个步骤</span>
      </button>
      {open ? (
        <ol className="agent-trace__timeline">
          {normalized.map((step, index) => {
            const isSqlStep = /sql/i.test(step.step);
            return (
              <li className={`agent-trace__step is-${step.status}`} key={`${step.step}-${index}`}>
                <span className="agent-trace__dot" />
                <div>
                  <div className="agent-trace__step-title">
                    <strong>{stepLabel(step.step)}</strong>
                    <span>{step.status === "retry" ? "重试" : step.status === "error" ? "失败" : "完成"}</span>
                  </div>
                  {isSqlStep ? <code>{step.message}</code> : <p>{step.message}</p>}
                </div>
              </li>
            );
          })}
        </ol>
      ) : null}
    </section>
  );
}
