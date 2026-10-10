import { translate } from "../i18n/translate";
import { useLanguage } from "../i18n/useLanguage";
import type { ReactNode } from "react";
import { Cube } from "@phosphor-icons/react";

type EmptyStateProps = {
  title: string;
  description: string;
  action?: ReactNode;
  icon?: ReactNode;
};

export default function EmptyState({ title, description, action, icon }: EmptyStateProps) {
  useLanguage();
  return (
    <div className="empty-state">
      <div className="empty-state-icon" aria-hidden="true">
        {translate(icon || <Cube size={26} weight="duotone" />)}
      </div>
      <h2>{title}</h2>
      <p>{description}</p>
      {action ? <div className="empty-state-action">{translate(action)}</div> : null}
    </div>
  );
}
