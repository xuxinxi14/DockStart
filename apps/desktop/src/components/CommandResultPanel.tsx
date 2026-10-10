import { translate } from "../i18n/translate";
import { useLanguage } from "../i18n/useLanguage";
type CommandResultPanelProps = {
  title?: string;
  message?: string;
  rawError?: string;
  suggestion?: string;
  announceAs?: "status" | "alert";
};

export default function CommandResultPanel({
  title = translate("命令结果"),
  message,
  rawError,
  suggestion,
  announceAs,
}: CommandResultPanelProps) {
  useLanguage();
  if (!message && !rawError && !suggestion) {
    return null;
  }

  const liveRole = announceAs ?? (rawError ? "alert" : "status");

  return (
    <section
      aria-atomic="true"
      aria-live={liveRole === "alert" ? "assertive" : "polite"}
      className="command-result-panel"
      role={liveRole}
    >
      <strong>{title}</strong>
      {message ? <p>{message}</p> : null}
      {suggestion ? (
        <div className="command-result-suggestion">
          <strong>{translate("建议怎么做")}</strong>
          <p>{suggestion}</p>
        </div>
      ) : null}
      {rawError ? (
        <details>
          <summary>{translate("技术详情")}</summary>
          <pre>{rawError}</pre>
        </details>
      ) : null}
    </section>
  );
}
