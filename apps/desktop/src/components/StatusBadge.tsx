import { translate } from "../i18n/translate";
import { useLanguage } from "../i18n/useLanguage";

type StatusTone = "ok" | "warning" | "error" | "muted" | "info";

type StatusBadgeProps = {
  children: string;
  tone?: StatusTone;
};

export default function StatusBadge({ children, tone = "muted" }: StatusBadgeProps) {
  useLanguage();
  return <span className={`status-badge ui-status-${tone}`}>{translate(children)}</span>;
}
