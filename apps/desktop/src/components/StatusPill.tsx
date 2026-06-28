export type WorkbenchStatus =
  | "ready"
  | "missing"
  | "partial"
  | "blocked"
  | "running"
  | "finished"
  | "failed"
  | "warning"
  | "optional";

type StatusPillProps = {
  children: string;
  status?: WorkbenchStatus;
  className?: string;
};

export default function StatusPill({ children, status = "optional", className = "" }: StatusPillProps) {
  return <span className={`status-pill ${status} ${className}`.trim()}>{children}</span>;
}
