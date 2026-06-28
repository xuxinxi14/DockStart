import type { ReactNode } from "react";

type SectionHeaderProps = {
  title: string;
  description?: string;
  level?: 2 | 3;
  action?: ReactNode;
  className?: string;
};

export default function SectionHeader({ title, description, level = 2, action, className = "" }: SectionHeaderProps) {
  const Heading = level === 2 ? "h2" : "h3";
  return (
    <header className={`section-header${action ? " with-action" : ""} ${className}`.trim()}>
      <div>
        <Heading>{title}</Heading>
        {description ? <p>{description}</p> : null}
      </div>
      {action ? <div>{action}</div> : null}
    </header>
  );
}
