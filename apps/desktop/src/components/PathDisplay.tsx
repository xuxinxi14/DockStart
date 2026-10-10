import { translate } from "../i18n/translate";
import { useLanguage } from "../i18n/useLanguage";
type PathDisplayProps = {
  label?: string;
  path?: string;
  emptyText?: string;
  className?: string;
};

export default function PathDisplay({ label = translate("路径"), path, emptyText = translate("尚未生成"), className = "" }: PathDisplayProps) {
  useLanguage();
  return (
    <div className={`path-display ${className}`.trim()}>
      <span>{label}</span>
      <code>{path || emptyText}</code>
    </div>
  );
}
