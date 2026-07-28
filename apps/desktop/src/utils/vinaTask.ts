import type { ProjectTaskIntent, StartMode } from "../navigation/pages";
import type {
  ProjectWorkflowStatusResponse,
  WorkflowRunSummary,
} from "../types";

export type VinaTaskProtocolSelection = {
  runMode: ProjectTaskIntent;
  autobox: boolean;
  confirmPoseContext: false;
  workspaceMode: "single" | null;
};

export const projectTaskOptions: Array<{
  id: ProjectTaskIntent;
  label: string;
  description: string;
}> = [
  {
    id: "dock",
    label: "全局对接",
    description: "在指定 Box 内搜索配体构象与位置。",
  },
  {
    id: "score_only",
    label: "姿势评分",
    description: "保留输入坐标，只计算当前姿势的能量项。",
  },
  {
    id: "local_only",
    label: "局部优化",
    description: "从输入姿势附近开始优化，不执行全局位点搜索。",
  },
];

export function normalizeProjectTaskIntent(
  intent: ProjectTaskIntent | string | null | undefined,
): ProjectTaskIntent {
  return intent === "score_only" || intent === "local_only" ? intent : "dock";
}

export function effectiveProjectTaskIntent(
  startMode: StartMode,
  requestedIntent: ProjectTaskIntent | string | null | undefined,
): ProjectTaskIntent {
  return startMode === "demo" ? "dock" : normalizeProjectTaskIntent(requestedIntent);
}

export function projectCreateProtocol(
  startMode: StartMode,
  requestedIntent: ProjectTaskIntent | string | null | undefined,
): VinaTaskProtocolSelection {
  const runMode = effectiveProjectTaskIntent(startMode, requestedIntent);
  return {
    runMode,
    autobox: runMode !== "dock",
    confirmPoseContext: false,
    workspaceMode: runMode === "dock" ? null : "single",
  };
}

export function projectTaskSwitch(
  currentIntent: ProjectTaskIntent | string | null | undefined,
  currentAutobox: boolean | null | undefined,
  requestedIntent: ProjectTaskIntent | string | null | undefined,
): VinaTaskProtocolSelection {
  const current = normalizeProjectTaskIntent(currentIntent);
  const runMode = normalizeProjectTaskIntent(requestedIntent);
  const autobox =
    runMode === "dock"
      ? false
      : current === "dock"
        ? true
        : Boolean(currentAutobox);
  return {
    runMode,
    autobox,
    confirmPoseContext: false,
    workspaceMode: runMode === "dock" ? null : "single",
  };
}

export function taskIntentFromProject(project: {
  docking_protocol?: { run_mode?: string };
} | null | undefined): ProjectTaskIntent {
  return normalizeProjectTaskIntent(project?.docking_protocol?.run_mode);
}

export function taskIntentLabel(intent: ProjectTaskIntent | string | null | undefined): string {
  const normalized = normalizeProjectTaskIntent(intent);
  if (normalized === "score_only") return "姿势评分";
  if (normalized === "local_only") return "局部优化";
  return "全局对接";
}

export function workflowRunForTask(
  workflow: ProjectWorkflowStatusResponse | null | undefined,
  intent: ProjectTaskIntent | string | null | undefined,
): WorkflowRunSummary | null {
  const normalizedIntent = normalizeProjectTaskIntent(intent);
  const currentModeRun = workflow?.latest_run_for_current_mode;
  if (
    currentModeRun
    && normalizeProjectTaskIntent(currentModeRun.run_mode) === normalizedIntent
  ) {
    return currentModeRun;
  }
  const compatibilityRun = workflow?.latest_run;
  return compatibilityRun
    && normalizeProjectTaskIntent(compatibilityRun.run_mode) === normalizedIntent
    ? compatibilityRun
    : null;
}
