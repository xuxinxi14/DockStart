import { translate } from "../i18n/translate";
import { useLanguage } from "../i18n/useLanguage";
import StatusBadge from "./StatusBadge";

type ReportStatusCardProps = {
  status: string;
  path?: string;
};

export default function ReportStatusCard({ status, path }: ReportStatusCardProps) {
  useLanguage();
  return (
    <article className="unified-status-card report-status-card">
      <div className="report-status-card-copy">
        <strong>{translate("Markdown 报告")}</strong>
        <p>{path || translate("报告导出后会显示路径。")}</p>
      </div>
      <StatusBadge tone={status === "exported" ? "ok" : "warning"}>{translate(status === "exported" ? "已导出" : "待导出")}</StatusBadge>
    </article>
  );
}
