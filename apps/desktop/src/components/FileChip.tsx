import { translate } from "../i18n/translate";
import { useLanguage } from "../i18n/useLanguage";
type FileChipProps = {
  label: string;
  status?: "ready" | "missing" | "optional";
  className?: string;
  title?: string;
};

export default function FileChip({ label, status = "optional", className = "", title }: FileChipProps) {
  useLanguage();
  return (
    <span className={`file-chip ${status} ${className}`.trim()} title={translate(title ?? label)}>
      {label}
    </span>
  );
}
