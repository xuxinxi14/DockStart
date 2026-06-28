import type { ReactNode } from "react";

type AdvancedDetailsProps = {
  summary?: string;
  children: ReactNode;
  className?: string;
  open?: boolean;
};

export default function AdvancedDetails({
  summary = "技术详情",
  children,
  className = "",
  open = false,
}: AdvancedDetailsProps) {
  return (
    <details className={`advanced-details ${className}`.trim()} open={open}>
      <summary>{summary}</summary>
      <div className="advanced-details-content">{children}</div>
    </details>
  );
}
