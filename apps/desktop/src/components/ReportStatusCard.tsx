import StatusBadge from "./StatusBadge";

type ReportStatusCardProps = {
  status: string;
  path?: string;
};

export default function ReportStatusCard({ status, path }: ReportStatusCardProps) {
  return (
    <article className="unified-status-card report-status-card">
      <div className="report-status-card-copy">
        <strong>Markdown 报告</strong>
        <p>{path || "报告导出后会显示路径。"}</p>
      </div>
      <StatusBadge tone={status === "exported" ? "ok" : "warning"}>{status === "exported" ? "已导出" : "待导出"}</StatusBadge>
    </article>
  );
}
