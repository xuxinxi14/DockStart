import type { WorkflowStep, WorkflowStepState } from "../components/WorkflowStepper";
import type { PageId } from "../navigation/pages";
import type { DockStartProject, ProjectWorkflowStatusResponse } from "../types";

export type GuidedWorkflowStep = WorkflowStep & {
  targetPage: PageId;
  actionLabel: string;
};

function fileOk(status?: string): boolean {
  return status === "ok";
}

function failedPreparation(workflow: ProjectWorkflowStatusResponse | null): boolean {
  return (
    workflow?.preparation?.receptor?.status === "failed" ||
    workflow?.preparation?.ligand?.status === "failed"
  );
}

function step(
  title: string,
  description: string,
  status: WorkflowStepState,
  actionLabel: string,
  targetPage: PageId,
): GuidedWorkflowStep {
  return { title, description, status, actionLabel, targetPage };
}

export function buildWorkflowSteps(
  project: DockStartProject | null,
  workflow: ProjectWorkflowStatusResponse | null,
): GuidedWorkflowStep[] {
  const hasProject = Boolean(project);
  const receptorRaw = fileOk(workflow?.raw?.receptor?.status);
  const ligandRaw = fileOk(workflow?.raw?.ligand?.status);
  const receptorPrepared = fileOk(workflow?.prepared?.receptor?.status);
  const ligandPrepared = fileOk(workflow?.prepared?.ligand?.status);
  const configReady = fileOk(workflow?.config?.status);
  const boxReady = workflow?.box?.status === "ok";
  const vinaReady = workflow?.vina?.status === "ok";
  const latestRunStatus = String(workflow?.latest_run?.status ?? "");
  const hasRun = Boolean(workflow?.latest_run);
  const hasFinishedRun = latestRunStatus === "finished";
  const hasFailedRun = latestRunStatus === "failed";
  const canViewPose = Boolean(workflow?.viewer?.can_view_docking_output);

  const rawStatus: WorkflowStepState = !hasProject
    ? "blocked"
    : receptorRaw && ligandRaw
      ? "done"
      : receptorRaw || ligandRaw
        ? "warning"
        : "available";

  const importStatus: WorkflowStepState = !hasProject
    ? "blocked"
    : receptorPrepared && ligandPrepared
      ? "done"
      : "available";

  const preparedStatus: WorkflowStepState = !hasProject
    ? "blocked"
    : receptorPrepared && ligandPrepared
      ? "done"
      : failedPreparation(workflow)
        ? "failed"
        : receptorRaw || ligandRaw
          ? "available"
          : "blocked";

  const configStatus: WorkflowStepState = !hasProject
    ? "blocked"
    : configReady
      ? "done"
      : receptorPrepared && ligandPrepared && boxReady && vinaReady
        ? "available"
        : "blocked";

  const runPrepareStatus: WorkflowStepState = !hasProject
    ? "blocked"
    : hasRun
      ? "done"
      : configReady
        ? "available"
        : "blocked";

  const executeStatus: WorkflowStepState = !hasProject
    ? "blocked"
    : hasFinishedRun
      ? "done"
      : hasFailedRun
        ? "failed"
        : latestRunStatus === "prepared"
          ? "available"
          : hasRun
            ? "warning"
            : "blocked";

  const resultStatus: WorkflowStepState = !hasProject
    ? "blocked"
    : hasFinishedRun
      ? "available"
      : hasFailedRun
        ? "failed"
        : "blocked";

  return [
    step(
      "创建项目",
      "创建 project.json 和标准目录结构。",
      hasProject ? "done" : "available",
      hasProject ? "查看项目" : "创建项目",
      "project-create",
    ),
    step("获取原始结构", "可选：下载或管理受体 / 配体 raw 文件。Basic Mode 可以跳过。", rawStatus, "获取结构", "structure-fetch"),
    step("导入 PDBQT", "Basic Mode：直接导入已有 receptor.pdbqt 和 ligand.pdbqt。", importStatus, "导入 PDBQT", "import-pdbqt"),
    step("自动准备 PDBQT", "Assisted Mode：把 raw 文件准备为 Vina 可用的 PDBQT。", preparedStatus, "准备 Vina 输入", "preparation"),
    step(
      "设置搜索范围",
      "设置对接箱体中心与尺寸。",
      !hasProject ? "blocked" : boxReady ? "done" : "available",
      "设置搜索范围",
      "run-prepare",
    ),
    step(
      "设置 Vina 参数",
      "确认 exhaustiveness、num_modes、energy_range、cpu 和 seed。",
      !hasProject ? "blocked" : vinaReady ? "done" : "available",
      "设置参数",
      "run-prepare",
    ),
    step("生成运行配置", "生成 configs/vina_config.txt。", configStatus, "生成运行配置", "run-prepare"),
    step("创建运行记录", "保存运行编号、配置快照和命令预览。", runPrepareStatus, "创建运行记录", "run-prepare"),
    step("开始对接", "执行 AutoDock Vina 并保存 stdout/stderr/log/out。", executeStatus, "开始对接", "run-execute"),
    step("解析结果", "从 Vina log 解析 scores.csv。", resultStatus, "查看结果", "result"),
    step("导出实验记录", "导出 Markdown 实验记录。", resultStatus, "导出实验记录", "report"),
  ];
}
