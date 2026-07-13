import {
  BookOpenText,
  ChartBar,
  CheckCircle,
  Circle,
  Cube,
  FolderOpen,
  House,
  PlayCircle,
  SidebarSimple,
  TestTube,
  WarningCircle,
  Wrench,
} from "@phosphor-icons/react";
import type { DockStartProject } from "../types";
import { appVersion } from "../navigation/pages";
import { navigationItems, resolveNavigationTarget, type NavigateHandler, type PageId } from "../navigation/pages";
import type { WorkflowStep } from "../components/WorkflowStepper";

type SidebarProps = {
  collapsed?: boolean;
  currentPage: PageId;
  project: DockStartProject | null;
  workflowSteps?: WorkflowStep[];
  onNavigate: NavigateHandler;
  onToggleCollapsed?: () => void;
};

function NavigationIcon({ page }: { page: PageId }) {
  const props = { "aria-hidden": true, size: 20, weight: "regular" as const };
  switch (page) {
    case "home":
      return <House {...props} />;
    case "project-create":
      return <FolderOpen {...props} />;
    case "structure-fetch":
      return <TestTube {...props} />;
    case "preparation":
      return <Cube {...props} />;
    case "box-setup":
      return <Cube {...props} weight="duotone" />;
    case "run-prepare":
      return <PlayCircle {...props} />;
    case "result":
      return <ChartBar {...props} />;
    case "report":
      return <BookOpenText {...props} />;
    case "toolchain-status":
    case "tool-check":
    case "settings":
      return <Wrench {...props} />;
    case "help":
      return <BookOpenText {...props} />;
    default:
      return <Circle {...props} />;
  }
}

export default function Sidebar({
  collapsed = false,
  currentPage,
  project,
  workflowSteps = [],
  onNavigate,
  onToggleCollapsed,
}: SidebarProps) {
  const hasProject = Boolean(project);
  const groups: Array<"Project" | "Workflow" | "Workbench" | "Support"> = ["Project", "Workflow", "Workbench", "Support"];
  const visibleGroups = groups.filter((group) => navigationItems.some((item) => item.group === group));
  const groupLabels: Record<(typeof groups)[number], string> = {
    Project: "导航",
    Workflow: "",
    Workbench: "工作台",
    Support: "支持",
  };
  function isActive(itemId: PageId): boolean {
    if (currentPage === itemId || (itemId === "home" && currentPage === "project-create")) {
      return true;
    }
    if (itemId === "preparation") {
      return currentPage === "structure-fetch" || currentPage === "preparation" || currentPage === "import-pdbqt";
    }
    if (itemId === "run-prepare") {
      return (
        currentPage === "box-setup" ||
        currentPage === "vina-param" ||
        currentPage === "vina-config" ||
        currentPage === "run-prepare" ||
        currentPage === "run-execute"
      );
    }
    if (itemId === "result") {
      return currentPage === "result" || currentPage === "report";
    }
    if (itemId === "toolchain-status") {
      return currentPage === "toolchain-status" || currentPage === "tool-check" || currentPage === "settings";
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

  function StateIcon({ state }: { state: "ready" | "blocked" | "idle" }) {
    if (state === "ready") return <CheckCircle aria-hidden="true" size={16} weight="fill" />;
    if (state === "blocked") return <WarningCircle aria-hidden="true" size={16} weight="fill" />;
    return <Circle aria-hidden="true" size={16} weight="regular" />;
  }

  return (
    <aside className="app-sidebar" aria-label="DockStart 主导航">
      <div className="sidebar-brand" data-tauri-drag-region>
        <img alt="" aria-hidden="true" className="sidebar-brand-mark" src="/dockstart-icon.png" />
        <span className="sidebar-brand-copy" data-tauri-drag-region>
          <strong>DockStart</strong>
          <small>分子对接工作台</small>
        </span>
      </div>
      <nav className="sidebar-nav">
        {visibleGroups.map((group) => (
          <div className="sidebar-group" key={group}>
            {groupLabels[group] ? <span className="sidebar-group-title">{groupLabels[group]}</span> : null}
            {navigationItems
              .filter((item) => item.group === group)
              .map((item) => {
                const target = resolveNavigationTarget(item, hasProject);
                const active = isActive(item.id);
                const requiresProjectBlocked = Boolean(item.requiresProject && !hasProject);
                const disabled = Boolean(item.disabled);
                const state = itemState(item.id, item.requiresProject);
                const itemLabel = item.label;
                return (
                  <button
                    aria-current={active ? "page" : undefined}
                    aria-label={itemLabel}
                    className={`sidebar-nav-item ${active ? "active" : ""} ${state} ${
                      requiresProjectBlocked ? "project-required" : ""
                    }`.trim()}
                    disabled={disabled}
                    key={item.id}
                    onClick={() => onNavigate(target)}
                    title={requiresProjectBlocked ? "创建项目后启用" : item.description}
                    type="button"
                  >
                    <span className="sidebar-nav-icon"><NavigationIcon page={item.id} /></span>
                    <span className="sidebar-nav-copy">
                      <strong>{itemLabel}</strong>
                      <small>{requiresProjectBlocked ? "创建项目后启用" : item.description}</small>
                    </span>
                    <span className={`sidebar-nav-state ${state}`}><StateIcon state={state} /></span>
                  </button>
                );
              })}
          </div>
        ))}
      </nav>
      <div className="sidebar-footer">
        <span className="sidebar-version">v{appVersion}</span>
        {onToggleCollapsed ? (
          <button
            aria-label={collapsed ? "展开侧边栏" : "收起侧边栏"}
            className="sidebar-collapse-button"
            onClick={onToggleCollapsed}
            title={collapsed ? "展开侧边栏" : "收起侧边栏"}
            type="button"
          >
            <SidebarSimple aria-hidden="true" mirrored={!collapsed} size={20} />
          </button>
        ) : null}
      </div>
    </aside>
  );
}
