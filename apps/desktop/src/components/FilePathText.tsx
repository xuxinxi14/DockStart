import { translate } from "../i18n/translate";
import { useLanguage } from "../i18n/useLanguage";
type FilePathTextProps = {
  value?: string | null;
  emptyText?: string;
};

export default function FilePathText({ value, emptyText = translate("未设置") }: FilePathTextProps) {
  useLanguage();
  return <code className="file-path-text">{value && value.trim() ? value : emptyText}</code>;
}
