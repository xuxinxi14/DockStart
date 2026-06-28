import type { DockStartProject } from "../types";
import StatusBadge from "../components/StatusBadge";
import { appVersion, pageTitles, type PageId } from "../navigation/pages";

type TopbarProps = {
  currentPage: PageId;
  project: DockStartProject | null;
  workflowSummary: string;
};

export default function Topbar({ currentPage, project, workflowSummary }: TopbarProps) {
  return (
    <header className="app-topbar">
      <div className="topbar-project">
        <span className="topbar-label">项目</span>
        <strong>{project?.project_name || "未加载项目"}</strong>
        <small>{project?.project_dir || "先创建或打开一个 DockStart 项目"}</small>
      </div>
      <div className="topbar-main">
        <span className="topbar-label">当前阶段</span>
        <strong>{pageTitles[currentPage]}</strong>
        <small>{workflowSummary}</small>
      </div>
      <div className="topbar-status">
        <StatusBadge tone={project ? "ok" : "warning"}>{project ? "已加载" : "未加载"}</StatusBadge>
        <StatusBadge tone="muted">工具链</StatusBadge>
        <span className="version-pill">v{appVersion}</span>
      </div>
    </header>
  );
}
