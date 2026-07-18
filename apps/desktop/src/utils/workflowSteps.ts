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
  const preparedInputsReady = receptorPrepared && ligandPrepared;
  const rawInputsReady = receptorRaw && ligandRaw;
  const rawStageSkipped = preparedInputsReady && !rawInputsReady;
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
    : preparedInputsReady || rawInputsReady
      ? "done"
      : receptorRaw || ligandRaw
        ? "warning"
        : "available";

  const importStatus: WorkflowStepState = !hasProject
    ? "blocked"
    : preparedInputsReady
      ? "done"
      : "available";

  const preparedStatus: WorkflowStepState = !hasProject
    ? "blocked"
    : preparedInputsReady
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
    step(
      "获取或导入原始结构",
      rawStageSkipped
        ? "有效的受体与配体 PDBQT 已就绪，此步骤无需执行。"
        : "在线搜索 RCSB / PubChem，或导入本地 PDB/CIF、SDF/MOL；已有 PDBQT 可跳过。",
      rawStatus,
      rawStageSkipped ? "无需获取" : "选择结构来源",
      "structure-fetch",
    ),
    step("导入已有 PDBQT", "已有 PDBQT：直接导入 receptor.pdbqt 和 ligand.pdbqt。", importStatus, "导入已有 PDBQT", "import-pdbqt"),
    step(
      "转换为 PDBQT",
      rawStageSkipped
        ? "有效的受体与配体 PDBQT 已就绪，无需重复格式转换。"
        : "Assisted：把原始结构准备并转换为 Vina 可用的 PDBQT。",
      preparedStatus,
      rawStageSkipped ? "无需转换" : "开始格式转换",
      "preparation",
    ),
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
    step("结果分析报告", "生成包含评分统计与可复现记录的 Markdown 报告。", resultStatus, "生成分析报告", "report"),
  ];
}
