import { translate } from "../i18n/translate";
import { useLanguage } from "../i18n/useLanguage";
import type { WorkflowFileStatus } from "../types";
import StatusBadge from "./StatusBadge";

type PreparedFileStatusCardProps = {
  title: string;
  file?: WorkflowFileStatus | null;
};

export default function PreparedFileStatusCard({ title, file }: PreparedFileStatusCardProps) {
  useLanguage();
  const status = file?.status ?? "missing";
  return (
    <article className="unified-status-card">
      <div>
        <strong>{title}</strong>
        <StatusBadge tone={status === "ok" ? "ok" : "warning"}>{translate(status === "ok" ? "Vina 可用" : "未就绪")}</StatusBadge>
      </div>
      <p>{file?.path || translate("未记录 Vina 输入文件。")}</p>
      <code>{file?.size ? `${file.size} bytes` : "size: 0"}</code>
    </article>
  );
}
