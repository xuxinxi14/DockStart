import { translate } from "../i18n/translate";
import { useLanguage } from "../i18n/useLanguage";
import type { ReactNode } from "react";

type WarningCalloutProps = {
  title?: string;
  children: ReactNode;
};

export default function WarningCallout({ title = translate("需要注意"), children }: WarningCalloutProps) {
  useLanguage();
  return (
    <div className="warning-callout">
      <strong>{title}</strong>
      <div>{children}</div>
    </div>
  );
}
