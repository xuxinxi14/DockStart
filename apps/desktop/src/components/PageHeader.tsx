import type { ReactNode } from "react";

type PageHeaderProps = {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
};

export default function PageHeader({ eyebrow, title, description, actions }: PageHeaderProps) {
  const visibleEyebrow = eyebrow && !/Page$/.test(eyebrow) ? eyebrow : "";

  return (
    <header className="page-header">
      <div>
        {visibleEyebrow ? <p className="eyebrow">{visibleEyebrow}</p> : null}
        <h1>{title}</h1>
        {description ? <p>{description}</p> : null}
      </div>
      {actions ? <div className="page-header-actions">{actions}</div> : null}
    </header>
  );
}
