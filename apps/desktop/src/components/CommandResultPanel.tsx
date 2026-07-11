type CommandResultPanelProps = {
  title?: string;
  message?: string;
  rawError?: string;
  announceAs?: "status" | "alert";
};

export default function CommandResultPanel({
  title = "命令结果",
  message,
  rawError,
  announceAs,
}: CommandResultPanelProps) {
  if (!message && !rawError) {
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
      {rawError ? (
        <details>
          <summary>技术详情</summary>
          <pre>{rawError}</pre>
        </details>
      ) : null}
    </section>
  );
}
