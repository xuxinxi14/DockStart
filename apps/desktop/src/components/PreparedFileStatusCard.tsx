import type { WorkflowFileStatus } from "../types";
import StatusBadge from "./StatusBadge";

type PreparedFileStatusCardProps = {
  title: string;
  file?: WorkflowFileStatus | null;
};

export default function PreparedFileStatusCard({ title, file }: PreparedFileStatusCardProps) {
  const status = file?.status ?? "missing";
  return (
    <article className="unified-status-card">
      <div>
        <strong>{title}</strong>
        <StatusBadge tone={status === "ok" ? "ok" : "warning"}>{status === "ok" ? "Vina 可用" : "未就绪"}</StatusBadge>
      </div>
      <p>{file?.path || "未记录 Vina 输入文件。"}</p>
      <code>{file?.size ? `${file.size} bytes` : "size: 0"}</code>
    </article>
  );
}
