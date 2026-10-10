import { translate } from "../i18n/translate";
import { useLanguage } from "../i18n/useLanguage";
type LogPreviewPanelProps = {
  title?: string;
  content?: string;
  emptyText?: string;
};

export default function LogPreviewPanel({
  title = translate("日志预览"),
  content = "",
  emptyText = translate("暂无日志内容。"),
}: LogPreviewPanelProps) {
  useLanguage();
  return (
    <section className="log-preview-panel">
      <strong>{title}</strong>
      <pre>{content.trim() ? content : emptyText}</pre>
    </section>
  );
}
