type ErrorLike = {
  code?: string;
  title?: string;
  message?: string;
  raw_error?: string;
  suggestion?: string;
} | null;

type ErrorPanelProps = {
  title?: string;
  error?: ErrorLike;
  message?: string;
};

export default function ErrorPanel({ title = "操作失败", error, message }: ErrorPanelProps) {
  if (!error && !message) {
    return null;
  }

  return (
    <div className="error-panel" role="alert">
      <strong>{error?.title ?? title}</strong>
      <p>{error?.message ?? message}</p>
      {error?.suggestion ? <p className="error-suggestion">{error.suggestion}</p> : null}
      {error?.code ? <code>{error.code}</code> : null}
      {error?.raw_error ? (
        <details>
          <summary>技术详情</summary>
          <pre>{error.raw_error}</pre>
        </details>
      ) : null}
    </div>
  );
}
