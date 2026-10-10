import { translate } from "../i18n/translate";
import { useLanguage } from "../i18n/useLanguage";
import type { ReactNode } from "react";

type PageHeaderProps = {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
};

export default function PageHeader({ eyebrow, title, description, actions }: PageHeaderProps) {
  useLanguage();
  const visibleEyebrow = eyebrow && !/Page$/.test(eyebrow) ? eyebrow : "";

  return (
    <header className="page-header">
      <div>
        {visibleEyebrow ? <p className="eyebrow">{translate(visibleEyebrow)}</p> : null}
        <h1>{title}</h1>
        {description ? <p>{description}</p> : null}
      </div>
      {actions ? <div className="page-header-actions">{translate(actions)}</div> : null}
    </header>
  );
}
