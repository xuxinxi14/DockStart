import StatusBadge from "./StatusBadge";

type RunStatusCardProps = {
  runId?: string;
  status?: string;
  detail?: string;
};

export default function RunStatusCard({ runId, status = "missing", detail }: RunStatusCardProps) {
  const tone = status === "finished" ? "ok" : status === "failed" ? "error" : runId ? "info" : "warning";
  return (
    <article className="unified-status-card">
      <div>
        <strong>{runId || "尚无 run"}</strong>
        <StatusBadge tone={tone}>{status}</StatusBadge>
      </div>
      <p>{detail || "准备 run 后这里会显示最新状态。"}</p>
    </article>
  );
}
