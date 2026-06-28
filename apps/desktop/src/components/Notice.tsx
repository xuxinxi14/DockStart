import type { ReactNode } from "react";

type NoticeTone = "info" | "warning" | "danger" | "scientific";

type NoticeProps = {
  title?: string;
  children: ReactNode;
  tone?: NoticeTone;
  className?: string;
};

export default function Notice({ title, children, tone = "info", className = "" }: NoticeProps) {
  return (
    <aside className={`notice ${tone} ${className}`.trim()}>
      {title ? <strong>{title}</strong> : null}
      {typeof children === "string" ? <p>{children}</p> : children}
    </aside>
  );
}
