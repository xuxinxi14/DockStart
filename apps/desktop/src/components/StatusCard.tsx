import { translate } from "../i18n/translate";
import { useLanguage } from "../i18n/useLanguage";
import type { ReactNode } from "react";
import type { WorkbenchStatus } from "./StatusPill";
import StatusPill from "./StatusPill";

type StatusCardProps = {
  title: string;
  description: string;
  status?: WorkbenchStatus;
  statusLabel?: string;
  action?: ReactNode;
  className?: string;
};

export default function StatusCard({
  title,
  description,
  status = "optional",
  statusLabel,
  action,
  className = "",
}: StatusCardProps) {
  useLanguage();
  return (
    <section className={`status-card ${status} ${className}`.trim()}>
      <StatusPill status={status}>{translate(statusLabel ?? title)}</StatusPill>
      <strong>{title}</strong>
      <p>{description}</p>
      {action ? <div>{translate(action)}</div> : null}
    </section>
  );
}
