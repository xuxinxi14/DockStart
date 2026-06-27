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
      <div className="topbar-main">
        <span className="topbar-label">工作流阶段</span>
        <strong>{pageTitles[currentPage]}</strong>
        <small>{workflowSummary}</small>
      </div>
      <div className="topbar-project">
        <span className="topbar-label">项目</span>
        <strong>{project?.project_name || "未加载项目"}</strong>
        <small>{project?.project_dir || "创建项目后会在这里显示项目目录"}</small>
      </div>
      <div className="topbar-status">
        <StatusBadge tone={project ? "info" : "warning"}>{project ? "项目已加载" : "等待项目"}</StatusBadge>
        <StatusBadge tone="muted">工具链按需检测</StatusBadge>
        <span className="version-pill">v{appVersion}</span>
      </div>
    </header>
  );
}
