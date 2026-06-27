type StatusTone = "ok" | "warning" | "error" | "muted" | "info";

type StatusBadgeProps = {
  children: string;
  tone?: StatusTone;
};

export default function StatusBadge({ children, tone = "muted" }: StatusBadgeProps) {
  return <span className={`status-badge ui-status-${tone}`}>{children}</span>;
}
