type FilePathTextProps = {
  value?: string | null;
  emptyText?: string;
};

export default function FilePathText({ value, emptyText = "未设置" }: FilePathTextProps) {
  return <code className="file-path-text">{value && value.trim() ? value : emptyText}</code>;
}
