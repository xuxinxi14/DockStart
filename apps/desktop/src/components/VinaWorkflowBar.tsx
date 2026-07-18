import StatusBadge from "./StatusBadge";

export type VinaWorkflowStepId = "config" | "prepare" | "execute" | "result" | "report";

type VinaWorkflowBarProps = {
  current: VinaWorkflowStepId;
  runId?: string;
};

const steps: Array<{ id: VinaWorkflowStepId; label: string; description: string }> = [
  { id: "config", label: "生成运行配置", description: "写入 configs/vina_config.txt" },
  { id: "prepare", label: "创建运行记录", description: "保存命令预览与配置快照" },
  { id: "execute", label: "开始对接", description: "保存 stdout / stderr / log / out" },
  { id: "result", label: "解析结果", description: "从 log.txt 生成 scores.csv" },
  { id: "report", label: "结果分析报告", description: "生成详细 Markdown 分析记录" },
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
      {runId ? <p className="vina-workflow-run">运行记录：{runId}</p> : null}
    </nav>
  );
}
