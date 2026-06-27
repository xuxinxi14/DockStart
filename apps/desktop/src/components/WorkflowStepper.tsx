export type WorkflowStepState = "not_started" | "available" | "warning" | "done" | "blocked" | "failed";

export type WorkflowStep = {
  title: string;
  description: string;
  status: WorkflowStepState;
  actionLabel?: string;
  targetPage?: string;
};

type WorkflowStepperProps = {
  steps: WorkflowStep[];
  compact?: boolean;
  onAction?: (step: WorkflowStep) => void;
};

const statusLabel: Record<WorkflowStepState, string> = {
  not_started: "未开始",
  available: "可进行",
  warning: "需确认",
  done: "已完成",
  blocked: "未就绪",
  failed: "失败",
};

export default function WorkflowStepper({ steps, compact = false, onAction }: WorkflowStepperProps) {
  return (
    <ol className={compact ? "workflow-stepper compact" : "workflow-stepper"}>
      {steps.map((step) => (
        <li className={`workflow-step workflow-${step.status}`} key={step.title}>
          <span>{statusLabel[step.status]}</span>
          <strong>{step.title}</strong>
          {!compact ? <p>{step.description}</p> : null}
          {!compact && step.actionLabel && onAction ? (
            <button className="text-button inline" type="button" onClick={() => onAction(step)}>
              {step.actionLabel}
            </button>
          ) : null}
        </li>
      ))}
    </ol>
  );
}
