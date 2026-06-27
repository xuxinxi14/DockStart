import type { ReactNode } from "react";
import type { DockStartProject } from "../types";
import type { PageId } from "../navigation/pages";
import Sidebar from "./Sidebar";
import Topbar from "./Topbar";
import type { WorkflowStep } from "../components/WorkflowStepper";

type AppShellProps = {
  currentPage: PageId;
  project: DockStartProject | null;
  workflowSummary: string;
  workflowSteps?: WorkflowStep[];
  onNavigate: (page: PageId) => void;
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
        <main className="app-content">{children}</main>
      </div>
    </div>
  );
}
