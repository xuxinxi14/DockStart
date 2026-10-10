import { translate } from "../i18n/translate";
import { useLanguage } from "../i18n/useLanguage";
import type { WorkbenchStatus } from "./StatusPill";
import StatusPill from "./StatusPill";

export type WorkflowTimelineStep = {
  id: string;
  title: string;
  description: string;
  status?: WorkbenchStatus;
  statusLabel?: string;
};

type WorkflowTimelineProps = {
  steps: WorkflowTimelineStep[];
  className?: string;
};

export default function WorkflowTimeline({ steps, className = "" }: WorkflowTimelineProps) {
  useLanguage();
  return (
    <ol className={`workflow-timeline ${className}`.trim()}>
      {translate(steps.map((step) => (
        <li key={step.id} className={`workflow-timeline-step ${step.status ?? "optional"}`}>
          <span className="workflow-timeline-dot" aria-hidden="true" />
          <div>
            <strong>{translate(step.title)}</strong>
            <p>{translate(step.description)}</p>
          </div>
          <StatusPill status={step.status}>{translate(step.statusLabel ?? "待确认")}</StatusPill>
        </li>
      )))}
    </ol>
  );
}
