type LogPreviewPanelProps = {
  title?: string;
  content?: string;
  emptyText?: string;
};

export default function LogPreviewPanel({
  title = "日志预览",
  content = "",
  emptyText = "暂无日志内容。",
}: LogPreviewPanelProps) {
  return (
    <section className="log-preview-panel">
      <strong>{title}</strong>
      <pre>{content.trim() ? content : emptyText}</pre>
    </section>
  );
}
