import { useState, type ReactNode } from "react";
import { FolderSimple, Monitor } from "@phosphor-icons/react";
import type { DockStartProject } from "../types";
import { appVersion, pageTitles, type NavigateHandler, type PageId } from "../navigation/pages";
import LayoutDebugOverlay from "../components/layout/LayoutDebugOverlay";
import Sidebar from "./Sidebar";
import Topbar from "./Topbar";
import type { WorkflowStep } from "../components/WorkflowStepper";

type AppShellProps = {
  currentPage: PageId;
  project: DockStartProject | null;
  workflowSummary: string;
  workflowSteps?: WorkflowStep[];
  onNavigate: NavigateHandler;
  children: ReactNode;
};

export default function AppShell({
  currentPage,
  project,
  workflowSummary,
  workflowSteps = [],
  onNavigate,
  children,
}: AppShellProps) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  return (
    <div className={`dockstart-shell ${sidebarCollapsed ? "sidebar-collapsed" : ""}`.trim()}>
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <Sidebar
        collapsed={sidebarCollapsed}
        currentPage={currentPage}
        project={project}
        workflowSteps={workflowSteps}
        onNavigate={onNavigate}
        onToggleCollapsed={() => setSidebarCollapsed((value) => !value)}
      />
      <div className="dockstart-workspace">
        <Topbar
          currentPage={currentPage}
          project={project}
          workflowSummary={workflowSummary}
          onNavigate={onNavigate}
        />
        <main className="app-content" data-layout="app-content" id="main-content" tabIndex={-1}>{children}</main>
        <footer className="app-statusbar" aria-label="当前工作区状态">
          <span className="statusbar-project" title={project?.project_dir || "尚未加载项目"}>
            <FolderSimple aria-hidden="true" size={15} weight="duotone" />
            <span>{project?.project_dir || "尚未加载项目"}</span>
          </span>
          <span className="statusbar-stage">当前阶段：{pageTitles[currentPage]}</span>
          <span className="statusbar-local">
            <Monitor aria-hidden="true" size={15} />
            仅在本机运行
          </span>
          <span className="statusbar-version">DockStart v{appVersion}</span>
        </footer>
      </div>
      {import.meta.env.DEV ? <LayoutDebugOverlay /> : null}
    </div>
  );
}
