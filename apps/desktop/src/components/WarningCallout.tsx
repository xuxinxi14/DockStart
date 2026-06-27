import type { ReactNode } from "react";

type WarningCalloutProps = {
  title?: string;
  children: ReactNode;
};

export default function WarningCallout({ title = "需要注意", children }: WarningCalloutProps) {
  return (
    <div className="warning-callout">
      <strong>{title}</strong>
      <div>{children}</div>
    </div>
  );
}
