import type { DockStartProject } from "../types";
import { navigationItems, resolveNavigationTarget, type NavigateHandler, type PageId } from "../navigation/pages";
import type { WorkflowStep } from "../components/WorkflowStepper";

type SidebarProps = {
  currentPage: PageId;
  project: DockStartProject | null;
  workflowSteps?: WorkflowStep[];
  onNavigate: NavigateHandler;
};

export default function Sidebar({ currentPage, project, workflowSteps = [], onNavigate }: SidebarProps) {
  const hasProject = Boolean(project);
  const groups: Array<"Project" | "Workflow" | "Workbench" | "Support"> = ["Project", "Workflow", "Workbench", "Support"];
  const visibleGroups = hasProject ? groups : groups.filter((group) => group !== "Workbench");
  const groupLabels: Record<(typeof groups)[number], string> = {
    Project: "项目",
    Workflow: "对接流程",
    Workbench: "工作台",
    Support: "支持",
  };
  const noProjectWorkflowLabels: Partial<Record<PageId, string>> = {
    "structure-fetch": "1 准备结构",
    "box-setup": "2 设置搜索范围",
    "vina-config": "3 运行对接",
    result: "4 查看结果",
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
        {visibleGroups.map((group) => (
          <div className="sidebar-group" key={group}>
            <span className="sidebar-group-title">{groupLabels[group]}</span>
            {!hasProject && group === "Workflow" ? <span className="sidebar-group-note">创建项目后启用</span> : null}
            {navigationItems
              .filter((item) => item.group === group)
              .filter((item) => hasProject || item.id !== "preparation")
              .map((item) => {
                const target = resolveNavigationTarget(item, hasProject);
                const active = isActive(item.id);
                const requiresProjectBlocked = Boolean(item.requiresProject && !hasProject);
                const disabled = Boolean(item.disabled || requiresProjectBlocked);
                const state = itemState(item.id, item.requiresProject);
                const itemLabel = !hasProject ? noProjectWorkflowLabels[item.id] ?? item.label : item.label;
                return (
                  <button
                    className={`sidebar-nav-item ${active ? "active" : ""} ${state} ${
                      requiresProjectBlocked ? "project-required" : ""
                    } ${requiresProjectBlocked ? "compact-disabled" : ""} descriptionless`.trim()}
                    disabled={disabled}
                    key={item.id}
                    onClick={() => onNavigate(target)}
                    title={requiresProjectBlocked ? "创建项目后启用" : item.description}
                    type="button"
                  >
                    <span className="sidebar-nav-dot" aria-hidden="true" />
                    <span>
                      <strong>{itemLabel}</strong>
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
