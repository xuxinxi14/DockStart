import StatusBadge from "./StatusBadge";

type ToolStatusCardProps = {
  title: string;
  status: string;
  message?: string;
  version?: string;
  path?: string;
};

function toneForStatus(status: string) {
  if (status === "ok" || status === "ready" || status === "finished") {
    return "ok";
  }
  if (status === "missing" || status === "not_started" || status === "unknown") {
    return "warning";
  }
  return "error";
}

export default function ToolStatusCard({ title, status, message, version, path }: ToolStatusCardProps) {
  return (
    <article className="unified-status-card">
      <div>
        <strong>{title}</strong>
        <StatusBadge tone={toneForStatus(status)}>{status || "unknown"}</StatusBadge>
      </div>
      {message ? <p>{message}</p> : null}
      {version ? <code>version: {version}</code> : null}
      {path ? <code>{path}</code> : null}
    </article>
  );
}
