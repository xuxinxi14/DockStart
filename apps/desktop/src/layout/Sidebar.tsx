import type { DockStartProject } from "../types";
import { navigationItems, resolveNavigationTarget, type PageId } from "../navigation/pages";
import type { WorkflowStep } from "../components/WorkflowStepper";

type SidebarProps = {
  currentPage: PageId;
  project: DockStartProject | null;
  workflowSteps?: WorkflowStep[];
  onNavigate: (page: PageId) => void;
};

export default function Sidebar({ currentPage, project, workflowSteps = [], onNavigate }: SidebarProps) {
  const hasProject = Boolean(project);
  const groups: Array<"Project" | "Workflow" | "Workbench" | "Support"> = ["Project", "Workflow", "Workbench", "Support"];
  const groupLabels: Record<(typeof groups)[number], string> = {
    Project: "项目",
    Workflow: "工作流",
    Workbench: "工作台",
    Support: "支持",
  };

  function isActive(itemId: PageId): boolean {
    if (currentPage === itemId) {
      return true;
    }
    if (itemId === "vina-config") {
      return currentPage === "vina-config" || currentPage === "run-prepare" || currentPage === "run-execute";
    }
    return false;
  }

  function itemState(itemId: PageId, requiresProject?: boolean): "ready" | "blocked" | "idle" {
    if (requiresProject && !hasProject) {
      return "blocked";
    }
    const matchedStep = workflowSteps.find((step) => step.targetPage === itemId);
    if (!matchedStep) {
      return hasProject ? "ready" : "idle";
    }
    if (matchedStep.status === "done") {
      return "ready";
    }
    if (matchedStep.status === "blocked" || matchedStep.status === "failed" || matchedStep.status === "warning") {
      return "blocked";
    }
    return "idle";
  }

  return (
    <aside className="app-sidebar" aria-label="DockStart 主导航">
      <div className="sidebar-brand">
        <strong>DockStart</strong>
        <span>Molecular Workbench / 分子工作台</span>
      </div>
      <nav className="sidebar-nav">
        {groups.map((group) => (
          <div className="sidebar-group" key={group}>
            <span className="sidebar-group-title">{groupLabels[group]}</span>
            {navigationItems
              .filter((item) => item.group === group)
              .map((item) => {
                const target = resolveNavigationTarget(item, hasProject);
                const active = isActive(item.id);
                const disabled = item.disabled;
                const state = itemState(item.id, item.requiresProject);
                return (
                  <button
                    className={`sidebar-nav-item ${active ? "active" : ""} ${state}`.trim()}
                    disabled={disabled}
                    key={item.id}
                    onClick={() => onNavigate(target)}
                    title={item.requiresProject && !hasProject ? "需要先创建或打开项目" : item.description}
                    type="button"
                  >
                    <span className="sidebar-nav-dot" aria-hidden="true" />
                    <span>
                      <strong>{item.label}</strong>
                      <small>{item.requiresProject && !hasProject ? "需先创建或打开项目" : item.description}</small>
                    </span>
                  </button>
                );
              })}
          </div>
        ))}
      </nav>
    </aside>
  );
}
