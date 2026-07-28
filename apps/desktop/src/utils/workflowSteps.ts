import type { WorkflowStep, WorkflowStepState } from "../components/WorkflowStepper";
import type { PageId } from "../navigation/pages";
import type { DockStartProject, ProjectWorkflowStatusResponse, VinaRunMode } from "../types";
import { workflowRunForTask } from "./vinaTask";

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

function runModeFor(
  project: DockStartProject | null,
  workflow: ProjectWorkflowStatusResponse | null,
): VinaRunMode {
  const projectMode = project?.docking_protocol?.run_mode;
  const latestMode =
    workflow?.latest_run_for_current_mode?.run_mode
    ?? workflow?.latest_run?.run_mode;
  const candidate =
    projectMode === "score_only" || projectMode === "local_only" || projectMode === "dock"
      ? projectMode
      : latestMode;
  return candidate === "score_only" || candidate === "local_only" ? candidate : "dock";
}

function runAutoboxFor(
  project: DockStartProject | null,
  workflow: ProjectWorkflowStatusResponse | null,
  runMode: VinaRunMode,
): boolean {
  const projectAutobox = project?.docking_protocol?.autobox;
  const latestAutobox = workflowRunForTask(workflow, runMode)?.autobox;
  return typeof projectAutobox === "boolean"
    ? projectAutobox
    : latestAutobox === true;
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
  const runMode = runModeFor(project, workflow);
  const latestRunForMode = workflowRunForTask(workflow, runMode);
  const latestRunStatus = String(latestRunForMode?.status ?? "");
  const hasRun = Boolean(latestRunForMode);
  const hasFinishedRun = latestRunStatus === "finished";
  const hasFailedRun = latestRunStatus === "failed";
  const isEvaluation = runMode !== "dock";
  const isAd4Maps = project?.docking_protocol?.engine === "ad4_maps";
  const autobox =
    isEvaluation
    && !isAd4Maps
    && runAutoboxFor(project, workflow, runMode);
  const effectiveBoxReady = autobox || boxReady;
  const modeText =
    runMode === "score_only"
      ? {
          rangeTitle: "选择评价范围",
          rangeDescription: isAd4Maps
            ? "当前使用已校验的 AutoDock4 maps 网格；该协议不启用 autobox。"
            : autobox
              ? "当前使用自动范围，由 Vina 围绕输入配体姿势建立评分网格。"
              : "使用项目 Box 作为当前姿势的评价范围。",
          rangeAction: autobox ? "查看自动范围" : "设置评价范围",
          paramsDescription: "确认评分函数与 CPU；当前姿势评分不执行构象搜索。",
          executeTitle: "评价当前姿势",
          executeDescription: "评价当前输入姿势，并保存 stdout、stderr 与 log。",
          executeAction: "开始姿势评分",
          analyzeTitle: "解析评价结果",
          analyzeDescription: "从 Vina log 提取能量项并生成 evaluation.json。",
          reportDescription: "生成包含当前姿势评分、能量项与可复现记录的 Markdown 报告。",
        }
      : runMode === "local_only"
        ? {
            rangeTitle: "选择优化范围",
            rangeDescription: isAd4Maps
              ? "当前使用已校验的 AutoDock4 maps 网格；该协议不启用 autobox。"
              : autobox
                ? "当前使用自动范围，由 Vina 围绕输入配体姿势建立局部优化网格。"
                : "使用项目 Box 作为局部优化范围。",
            rangeAction: autobox ? "查看自动范围" : "设置优化范围",
            paramsDescription: "确认评分函数与 CPU；局部优化不执行全局构象搜索。",
            executeTitle: "开始局部优化",
            executeDescription: "优化当前输入姿势，并保存优化后 PDBQT 与运行日志。",
            executeAction: "开始局部优化",
            analyzeTitle: "解析评价结果",
            analyzeDescription: "从 Vina log 提取能量项并生成 evaluation.json。",
            reportDescription: "生成包含局部优化评分、能量项与可复现记录的 Markdown 报告。",
          }
        : {
            rangeTitle: "设置搜索范围",
            rangeDescription: "设置对接箱体中心与尺寸。",
            rangeAction: "设置搜索范围",
            paramsDescription: "确认 exhaustiveness、num_modes、energy_range、cpu 和 seed。",
            executeTitle: "开始对接",
            executeDescription: "执行 AutoDock Vina 并保存 stdout、stderr、log 与 out.pdbqt。",
            executeAction: "开始对接",
            analyzeTitle: "解析结果",
            analyzeDescription: "从 Vina log 解析构象评分并生成 scores.csv。",
            reportDescription: "生成包含评分统计与可复现记录的 Markdown 报告。",
          };

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
      : receptorPrepared && ligandPrepared && effectiveBoxReady && vinaReady
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
      modeText.rangeTitle,
      modeText.rangeDescription,
      !hasProject ? "blocked" : effectiveBoxReady ? "done" : "available",
      modeText.rangeAction,
      "run-prepare",
    ),
    step(
      "设置 Vina 参数",
      modeText.paramsDescription,
      !hasProject ? "blocked" : vinaReady ? "done" : "available",
      "设置参数",
      "run-prepare",
    ),
    step(
      "生成运行配置",
      isEvaluation
        ? "生成与当前评价模式匹配的 configs/vina_config.txt。"
        : "生成 configs/vina_config.txt。",
      configStatus,
      "生成运行配置",
      "run-prepare",
    ),
    step("创建运行记录", "保存运行编号、配置快照和命令预览。", runPrepareStatus, "创建运行记录", "run-prepare"),
    step(
      modeText.executeTitle,
      modeText.executeDescription,
      executeStatus,
      modeText.executeAction,
      "run-execute",
    ),
    step(modeText.analyzeTitle, modeText.analyzeDescription, resultStatus, "查看结果", "result"),
    step("结果分析报告", modeText.reportDescription, resultStatus, "生成分析报告", "report"),
  ];
}
