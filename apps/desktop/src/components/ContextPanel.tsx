import type { ReactNode } from "react";
import SectionHeader from "./SectionHeader";

type ContextPanelProps = {
  title?: string;
  description?: string;
  children: ReactNode;
  className?: string;
};

export default function ContextPanel({
  title = "下一步与状态",
  description,
  children,
  className = "",
}: ContextPanelProps) {
  return (
    <aside className={`context-panel-shell ${className}`.trim()}>
      <SectionHeader title={title} description={description} level={3} />
      {children}
    </aside>
  );
}
