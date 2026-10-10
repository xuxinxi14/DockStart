import { translate } from "../i18n/translate";
import { useLanguage } from "../i18n/useLanguage";
import StatusBadge from "./StatusBadge";

type RunStatusCardProps = {
  runId?: string;
  status?: string;
  detail?: string;
};

export default function RunStatusCard({ runId, status = "missing", detail }: RunStatusCardProps) {
  useLanguage();
  const tone = status === "finished" ? "ok" : status === "failed" ? "error" : runId ? "info" : "warning";
  return (
    <article className="unified-status-card">
      <div>
        <strong>{runId || translate("尚无 run")}</strong>
        <StatusBadge tone={tone}>{translate(status)}</StatusBadge>
      </div>
      <p>{translate(detail || "准备运行后可查看最新状态。")}</p>
    </article>
  );
}
