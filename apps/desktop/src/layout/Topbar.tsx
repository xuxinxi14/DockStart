import {
  CheckCircle,
  CaretRight,
  FolderOpen,
  Question,
  Wrench,
} from "@phosphor-icons/react";
import type { DockStartProject } from "../types";
import { pageTitles, type NavigateHandler, type PageId } from "../navigation/pages";

type TopbarProps = {
  currentPage: PageId;
  project: DockStartProject | null;
  workflowSummary: string;
  onNavigate: NavigateHandler;
};

export default function Topbar({ currentPage, project, workflowSummary, onNavigate }: TopbarProps) {
  const hasProject = Boolean(project);
  return (
    <header className="app-topbar">
      <div className="topbar-context">
        <span className="topbar-workspace-label">项目工作台</span>
        <CaretRight aria-hidden="true" className="topbar-divider-icon" size={14} />
        <button className="topbar-project-button" onClick={() => onNavigate("home")} type="button">
          <span className="topbar-project-name">{project?.project_name || "未加载项目"}</span>
          <span className="topbar-project-stage">{pageTitles[currentPage]}</span>
        </button>
      </div>
      <div className="topbar-summary" title={workflowSummary}>
        {hasProject ? (
          <>
            <CheckCircle aria-hidden="true" size={17} weight="fill" />
            <span>{workflowSummary}</span>
          </>
        ) : (
          <span>选择一种开始方式，DockStart 会逐步引导。</span>
        )}
      </div>
      <div className="topbar-actions" aria-label="工作区快捷操作">
        <button onClick={() => onNavigate("project-create")} type="button">
          <FolderOpen aria-hidden="true" size={18} />
          <span>打开项目</span>
        </button>
        <button onClick={() => onNavigate("toolchain-status")} type="button">
          <Wrench aria-hidden="true" size={18} />
          <span>工具链</span>
        </button>
        <button onClick={() => onNavigate("help")} type="button">
          <Question aria-hidden="true" size={18} />
          <span>帮助</span>
        </button>
      </div>
    </header>
  );
}
