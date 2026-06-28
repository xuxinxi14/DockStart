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
  return (
    <ol className={`workflow-timeline ${className}`.trim()}>
      {steps.map((step) => (
        <li key={step.id} className={`workflow-timeline-step ${step.status ?? "optional"}`}>
          <span className="workflow-timeline-dot" aria-hidden="true" />
          <div>
            <strong>{step.title}</strong>
            <p>{step.description}</p>
          </div>
          <StatusPill status={step.status}>{step.statusLabel ?? "待确认"}</StatusPill>
        </li>
      ))}
    </ol>
  );
}
