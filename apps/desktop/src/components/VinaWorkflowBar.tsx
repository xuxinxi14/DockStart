import StatusBadge from "./StatusBadge";

export type VinaWorkflowStepId = "config" | "prepare" | "execute" | "result" | "report";

type VinaWorkflowBarProps = {
  current: VinaWorkflowStepId;
  runId?: string;
};

const steps: Array<{ id: VinaWorkflowStepId; label: string; description: string }> = [
  { id: "config", label: "生成 config", description: "写入 configs/vina_config.txt" },
  { id: "prepare", label: "准备 run", description: "创建 metadata 与命令快照" },
  { id: "execute", label: "执行 Vina", description: "保存 stdout / stderr / log / out" },
  { id: "result", label: "解析结果", description: "从 log.txt 生成 scores.csv" },
  { id: "report", label: "导出报告", description: "生成 Markdown 报告" },
];

export default function VinaWorkflowBar({ current, runId }: VinaWorkflowBarProps) {
  const currentIndex = steps.findIndex((step) => step.id === current);

  return (
    <nav className="vina-workflow-bar" aria-label="Vina workflow">
      <ol>
        {steps.map((step, index) => {
          const isCurrent = step.id === current;
          const isDone = currentIndex > index;
          const tone = isCurrent ? "info" : isDone ? "ok" : "muted";
          return (
            <li key={step.id} className={isCurrent ? "current" : ""}>
              <div>
                <span>{index + 1}</span>
                <strong>{step.label}</strong>
              </div>
              <p>{step.description}</p>
              <StatusBadge tone={tone}>{isCurrent ? "当前步骤" : isDone ? "已通过" : "待进行"}</StatusBadge>
            </li>
          );
        })}
      </ol>
      {runId ? <p className="vina-workflow-run">当前 run_id：{runId}</p> : null}
    </nav>
  );
}
