import type { ReactNode } from "react";

type PanelProps = {
  children: ReactNode;
  className?: string;
  compact?: boolean;
};

export default function Panel({ children, className = "", compact = false }: PanelProps) {
  return <div className={`ds-panel${compact ? " compact" : ""} ${className}`.trim()}>{children}</div>;
}
