import type { WorkflowStep, WorkflowStepState } from "../components/WorkflowStepper";
import type { PageId } from "../navigation/pages";

export type SidebarNavigationState = "ready" | "blocked" | "idle";

const groupedTargets: Partial<Record<PageId, PageId[]>> = {
  home: ["project-create"],
  preparation: ["structure-fetch", "import-pdbqt", "preparation"],
  "run-prepare": ["run-prepare", "run-execute"],
  result: ["result", "report"],
};

const blockingStates = new Set<WorkflowStepState>(["blocked", "failed", "warning"]);

function stepsForNavigationItem(itemId: PageId, workflowSteps: WorkflowStep[]): WorkflowStep[] {
  const targets: readonly string[] = groupedTargets[itemId] ?? [itemId];
  return workflowSteps.filter((step) => step.targetPage && targets.includes(step.targetPage));
}

/**
 * Sidebar checks describe workflow completion, not merely whether a project exists.
 * A grouped entry is successful only after every step in that section is done.
 */
export function resolveSidebarNavigationState(
  itemId: PageId,
  hasProject: boolean,
  requiresProject: boolean,
  workflowSteps: WorkflowStep[],
  hydratedRunFinished = false,
  batchScreeningCompleted = false,
): SidebarNavigationState {
  if (requiresProject && !hasProject) {
    return "blocked";
  }

  if (itemId === "hydrated-ad4") {
    if (!hasProject) return "blocked";
    if (hydratedRunFinished) return "ready";

    // Before a hydrated run finishes, surface failed/missing structure
    // prerequisites without claiming success merely because a project exists.
    const inputSteps = stepsForNavigationItem("preparation", workflowSteps);
    return inputSteps.some((step) => blockingStates.has(step.status)) ? "blocked" : "idle";
  }

  if (itemId === "result" && batchScreeningCompleted) {
    return "ready";
  }

  const matchedSteps = stepsForNavigationItem(itemId, workflowSteps);
  if (!matchedSteps.length) {
    return "idle";
  }
  if (matchedSteps.some((step) => blockingStates.has(step.status))) {
    return "blocked";
  }
  if (matchedSteps.every((step) => step.status === "done")) {
    return "ready";
  }
  return "idle";
}
