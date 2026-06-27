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

function fileMissing(status?: string): boolean {
  return !status || status === "missing";
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
    step("获取 raw 结构", "下载或管理 receptor / ligand 原始结构。", rawStatus, "获取结构", "structure-fetch"),
    step("准备 PDBQT", "把 raw 文件准备为 Vina 可用的 prepared PDBQT。", preparedStatus, "准备 PDBQT", "preparation"),
    step(
      "设置 Box",
      "设置 center_x/y/z 与 size_x/y/z。",
      !hasProject ? "blocked" : boxReady ? "done" : "available",
      "设置 Box",
      "box-setup",
    ),
    step(
      "设置 Vina 参数",
      "确认 exhaustiveness、num_modes、energy_range、cpu 和 seed。",
      !hasProject ? "blocked" : vinaReady ? "done" : "available",
      "设置参数",
      "vina-param",
    ),
    step("生成 config", "生成 configs/vina_config.txt。", configStatus, "生成 config", "vina-config"),
    step("准备 run", "生成 run_id、metadata 和命令预览。", runPrepareStatus, "准备 run", "run-prepare"),
    step("执行 Vina", "执行 prepared run 并保存 stdout/stderr/log/out。", executeStatus, "执行 Vina", "run-execute"),
    step("解析结果", "从 Vina log 解析 scores.csv。", resultStatus, "查看结果", "result"),
    step("导出报告", "导出 Markdown docking report。", resultStatus, "导出报告", "report"),
    step(
      "3D 查看 / pose 查看",
      "查看 raw/prepared 结构、Box 或 docking pose。",
      !hasProject ? "blocked" : canViewPose ? "done" : receptorPrepared || ligandPrepared || receptorRaw || ligandRaw ? "available" : "blocked",
      "打开 Viewer",
      "viewer",
    ),
  ];
}
