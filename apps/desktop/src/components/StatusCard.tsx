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
  return (
    <section className={`status-card ${status} ${className}`.trim()}>
      <StatusPill status={status}>{statusLabel ?? title}</StatusPill>
      <strong>{title}</strong>
      <p>{description}</p>
      {action ? <div>{action}</div> : null}
    </section>
  );
}
