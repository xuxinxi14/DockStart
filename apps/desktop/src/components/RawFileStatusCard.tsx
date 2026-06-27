import type { WorkflowFileStatus } from "../types";
import StatusBadge from "./StatusBadge";

type RawFileStatusCardProps = {
  title: string;
  file?: WorkflowFileStatus | null;
};

export default function RawFileStatusCard({ title, file }: RawFileStatusCardProps) {
  const status = file?.status ?? "missing";
  return (
    <article className="unified-status-card">
      <div>
        <strong>{title}</strong>
        <StatusBadge tone={status === "ok" ? "ok" : "warning"}>{status === "ok" ? "已下载" : "未就绪"}</StatusBadge>
      </div>
      <p>{file?.path || "未记录 raw 文件。"}</p>
      <code>{file?.size ? `${file.size} bytes` : "size: 0"}</code>
    </article>
  );
}
