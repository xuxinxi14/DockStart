import type { ReactNode } from "react";
import AdvancedDetails from "./AdvancedDetails";

type ErrorRecoveryPanelProps = {
  title: string;
  message: string;
  suggestion?: string;
  rawError?: string;
  action?: ReactNode;
  className?: string;
};

export default function ErrorRecoveryPanel({
  title,
  message,
  suggestion,
  rawError,
  action,
  className = "",
}: ErrorRecoveryPanelProps) {
  return (
    <section className={`error-recovery-panel ${className}`.trim()}>
      <strong>{title}</strong>
      <p>{message}</p>
      {suggestion ? <p>{suggestion}</p> : null}
      {action ? <div>{action}</div> : null}
      {rawError ? (
        <AdvancedDetails summary="错误详情">
          <pre>{rawError}</pre>
        </AdvancedDetails>
      ) : null}
    </section>
  );
}
