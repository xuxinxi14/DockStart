import type { DockStartProject } from "../types";
import StatusBadge from "../components/StatusBadge";
import { appVersion, pageTitles, type PageId } from "../navigation/pages";

type TopbarProps = {
  currentPage: PageId;
  project: DockStartProject | null;
  workflowSummary: string;
};

export default function Topbar({ currentPage, project, workflowSummary }: TopbarProps) {
  const hasProject = Boolean(project);
  return (
    <header className="app-topbar">
      <div className="topbar-project">
        <span className="topbar-label">项目</span>
        <strong>{project?.project_name || "未加载项目"}</strong>
        {project?.project_dir ? <small>{project.project_dir}</small> : null}
      </div>
      <div className="topbar-main">
        <span className="topbar-label">当前阶段</span>
        <strong>{pageTitles[currentPage]}</strong>
        {hasProject ? <small>{workflowSummary}</small> : null}
      </div>
      <div className="topbar-status">
        {hasProject ? <StatusBadge tone="ok">已加载</StatusBadge> : null}
        <StatusBadge tone="muted">工具链</StatusBadge>
        <span className="version-pill">v{appVersion}</span>
      </div>
    </header>
  );
}
