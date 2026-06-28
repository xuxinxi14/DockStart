type FileChipProps = {
  label: string;
  status?: "ready" | "missing" | "optional";
  className?: string;
  title?: string;
};

export default function FileChip({ label, status = "optional", className = "", title }: FileChipProps) {
  return (
    <span className={`file-chip ${status} ${className}`.trim()} title={title ?? label}>
      {label}
    </span>
  );
}
