import type { ReactNode } from "react";
import type { DockStartProject } from "../types";
import type { NavigateHandler, PageId } from "../navigation/pages";
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
  return (
    <div className="dockstart-shell">
      <Sidebar currentPage={currentPage} project={project} workflowSteps={workflowSteps} onNavigate={onNavigate} />
      <div className="dockstart-workspace">
        <Topbar currentPage={currentPage} project={project} workflowSummary={workflowSummary} />
        <main className="app-content" data-layout="app-content">{children}</main>
      </div>
      <LayoutDebugOverlay />
    </div>
  );
}
