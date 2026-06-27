type CommandResultPanelProps = {
  title?: string;
  message?: string;
  rawError?: string;
};

export default function CommandResultPanel({ title = "命令结果", message, rawError }: CommandResultPanelProps) {
  if (!message && !rawError) {
    return null;
  }

  return (
    <section className="command-result-panel">
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
