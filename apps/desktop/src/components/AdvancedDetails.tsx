import { translate } from "../i18n/translate";
import { useLanguage } from "../i18n/useLanguage";
import type { ReactNode } from "react";
import { CaretRight } from "@phosphor-icons/react";

type AdvancedDetailsProps = {
  summary?: string;
  children: ReactNode;
  className?: string;
  open?: boolean;
};

export default function AdvancedDetails({
  summary = translate("技术详情"),
  children,
  className = "",
  open = false,
}: AdvancedDetailsProps) {
  useLanguage();
  return (
    <details className={`advanced-details ${className}`.trim()} open={open}>
      <summary><CaretRight aria-hidden="true" className="advanced-details-caret" size={14} weight="bold" />{summary}</summary>
      <div className="advanced-details-content">{children}</div>
    </details>
  );
}
