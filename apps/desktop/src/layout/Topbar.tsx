import type { DockStartProject } from "../types";
import StatusBadge from "../components/StatusBadge";
import FilePathText from "../components/FilePathText";
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
        <span>当前页面</span>
        <strong>{pageTitles[currentPage]}</strong>
      </div>
      <div className="topbar-project">
        <span>当前项目</span>
        <FilePathText value={project?.project_dir} emptyText="尚未创建或加载项目" />
      </div>
      <div className="topbar-status">
        <StatusBadge tone={project ? "info" : "warning"}>{workflowSummary}</StatusBadge>
        <span className="version-pill">v{appVersion}</span>
      </div>
    </header>
  );
}
