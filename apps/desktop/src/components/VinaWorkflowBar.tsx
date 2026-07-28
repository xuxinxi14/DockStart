import StatusBadge from "./StatusBadge";
import type { VinaRunMode } from "../types";

export type VinaWorkflowStepId = "config" | "prepare" | "execute" | "result" | "report";

type VinaWorkflowBarProps = {
  current: VinaWorkflowStepId;
  runId?: string;
  runMode?: VinaRunMode;
};

type WorkflowStep = {
  id: VinaWorkflowStepId;
  label: string;
  description: string;
};

function stepsFor(runMode: VinaRunMode): WorkflowStep[] {
  const common: WorkflowStep[] = [
    { id: "config", label: "生成运行配置", description: "写入 configs/vina_config.txt" },
    { id: "prepare", label: "创建运行记录", description: "保存命令预览与配置快照" },
  ];
  if (runMode === "score_only") {
    return [
      ...common,
      { id: "execute", label: "评价当前姿势", description: "保存 stdout / stderr / log" },
      { id: "result", label: "解析评价结果", description: "从 log.txt 生成 evaluation.json" },
      { id: "report", label: "评价报告", description: "生成 Markdown 评价记录" },
    ];
  }
  if (runMode === "local_only") {
    return [
      ...common,
      { id: "execute", label: "开始局部优化", description: "保存优化后 PDBQT 与运行日志" },
      { id: "result", label: "解析评价结果", description: "从 log.txt 生成 evaluation.json" },
      { id: "report", label: "评价报告", description: "生成 Markdown 评价记录" },
    ];
  }
  return [
    ...common,
    { id: "execute", label: "开始对接", description: "保存 stdout / stderr / log / out.pdbqt" },
    { id: "result", label: "解析结果", description: "从 log.txt 生成 scores.csv" },
    { id: "report", label: "结果分析报告", description: "生成 Markdown 分析记录" },
  ];
}

export default function VinaWorkflowBar({ current, runId, runMode = "dock" }: VinaWorkflowBarProps) {
  const steps = stepsFor(runMode);
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
