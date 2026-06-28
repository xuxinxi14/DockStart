type PathDisplayProps = {
  label?: string;
  path?: string;
  emptyText?: string;
  className?: string;
};

export default function PathDisplay({ label = "路径", path, emptyText = "尚未生成", className = "" }: PathDisplayProps) {
  return (
    <div className={`path-display ${className}`.trim()}>
      <span>{label}</span>
      <code>{path || emptyText}</code>
    </div>
  );
}
