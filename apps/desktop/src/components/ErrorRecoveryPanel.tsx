import { translate } from "../i18n/translate";
import { useLanguage } from "../i18n/useLanguage";
import type { ReactNode } from "react";
import AdvancedDetails from "./AdvancedDetails";
import DocumentationLink from "./DocumentationLink";
import { helpTopicForError, topicUrl } from "../utils/helpContent";

export type ErrorRecoveryData = {
  code?: string;
  title?: string;
  message?: string;
  raw_error?: string;
  suggestion?: string;
} | null;

type ErrorRecoveryPanelProps = {
  title?: string;
  message?: string;
  error?: ErrorRecoveryData;
  suggestion?: string;
  rawError?: string;
  action?: ReactNode;
  className?: string;
};

export default function ErrorRecoveryPanel({
  title = translate("操作失败"),
  message,
  error,
  suggestion,
  rawError,
  action,
  className = "",
}: ErrorRecoveryPanelProps) {
  useLanguage();
  const displayedTitle = error?.title || title;
  const displayedMessage = error?.message || message || "操作未完成，请根据建议检查后重试。";
  const displayedSuggestion = error?.suggestion || suggestion;
  const displayedRawError = error?.raw_error || rawError;
  const topic = error?.code ? helpTopicForError(error.code) : null;

  if (!error && !message && !suggestion && !rawError) return null;

  return (
    <section className={`error-recovery-panel ${className}`.trim()} role="alert">
      <strong>{translate(displayedTitle)}</strong>
      <p>{translate(displayedMessage)}</p>
      {displayedSuggestion ? <p>{translate(displayedSuggestion)}</p> : null}
      {error?.code ? <code>{error.code}</code> : null}
      {topic ? <AdvancedDetails summary={translate("排查说明：{0}", [translate(topic.title)])}>
        <ol>{topic.steps.map(step => <li key={step}>{translate(step)}</li>)}</ol>
        <DocumentationLink url={topicUrl(topic)}>{translate("查看对应排错文档")}</DocumentationLink>
      </AdvancedDetails> : null}
      {action ? <div>{translate(action)}</div> : null}
      {displayedRawError ? (
        <AdvancedDetails summary={translate("错误详情")}>
          <pre>{displayedRawError}</pre>
        </AdvancedDetails>
      ) : null}
    </section>
  );
}
