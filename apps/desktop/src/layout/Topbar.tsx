import {
  CaretRight,
  FolderOpen,
  ListChecks,
  Moon,
  Question,
  Sun,
  Wrench,
} from "@phosphor-icons/react";
import type { DockStartProject } from "../types";
import { pageTitles, type NavigateHandler, type PageId } from "../navigation/pages";
import Tooltip from "../components/Tooltip";
import type { ResolvedTheme, ThemeMode } from "../utils/themePreference";
import WindowControls from "./WindowControls";

type TopbarProps = {
  currentPage: PageId;
  project: DockStartProject | null;
  workflowSummary: string;
  /** What is actually painted right now (never `system`). */
  theme: ResolvedTheme;
  /** The stored preference, which may be `system`. */
  themeMode: ThemeMode;
  onToggleTheme: () => void;
  onNavigate: NavigateHandler;
  onOpenProject: () => void;
};

function themeToggleLabel(theme: ResolvedTheme, themeMode: ThemeMode): string {
  const next = theme === "dark" ? "亮色" : "暗色";
  if (themeMode === "system") {
    return `当前跟随系统（${theme === "dark" ? "深色" : "浅色"}），点击固定为${next}主题`;
  }
  return `切换到${next}主题`;
}

function formatSavedAt(value: string | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export default function Topbar({
  currentPage,
  project,
  workflowSummary,
  theme,
  themeMode,
  onToggleTheme,
  onNavigate,
  onOpenProject,
}: TopbarProps) {
  const hasProject = Boolean(project);
  return (
    <header className="app-topbar" data-tauri-drag-region>
      <div className="topbar-context" data-tauri-drag-region>
        <span className="topbar-workspace-label">项目工作台</span>
        <CaretRight aria-hidden="true" className="topbar-divider-icon" size={14} />
        <button className="topbar-project-button" onClick={() => onNavigate("home")} type="button">
          <span className="topbar-project-name">{project?.project_name || "未加载项目"}</span>
          <span className="topbar-project-stage">{pageTitles[currentPage]}</span>
        </button>
      </div>
      <Tooltip className="topbar-summary-tooltip" disabled={!hasProject} label={workflowSummary}>
        <div className="topbar-summary" data-tauri-drag-region>
          {hasProject ? (
            <>
              <ListChecks aria-hidden="true" size={17} weight="duotone" />
              <span>{workflowSummary}</span>
              <span className="topbar-save-indicator">
                项目记录更新于 {formatSavedAt(project?.updated_at) || "当前会话"}
              </span>
            </>
          ) : (
            <span>选择一种开始方式，DockStart 会逐步引导。</span>
          )}
        </div>
      </Tooltip>
      <div className="topbar-end">
        <div className="topbar-actions" aria-label="工作区快捷操作">
          <Tooltip className="topbar-action-tooltip" label="打开项目">
            <button aria-label="打开项目" onClick={onOpenProject} type="button">
              <FolderOpen aria-hidden="true" size={18} />
              <span>打开项目</span>
            </button>
          </Tooltip>
          <Tooltip className="topbar-action-tooltip" label="查看工具链状态">
            <button aria-label="工具链" onClick={() => onNavigate("toolchain-status")} type="button">
              <Wrench aria-hidden="true" size={18} />
              <span>工具链</span>
            </button>
          </Tooltip>
          <Tooltip className="topbar-action-tooltip" label="打开帮助中心">
            <button aria-label="帮助" onClick={() => onNavigate("help")} type="button">
              <Question aria-hidden="true" size={18} />
              <span>帮助</span>
            </button>
          </Tooltip>
        </div>
        <Tooltip label={themeToggleLabel(theme, themeMode)}>
          <button
            className="topbar-theme-toggle"
            type="button"
            onClick={onToggleTheme}
            aria-label="切换亮色或暗色主题"
            aria-pressed={theme === "light"}
          >
            {theme === "dark" ? <Sun aria-hidden="true" size={18} /> : <Moon aria-hidden="true" size={18} />}
          </button>
        </Tooltip>
        <WindowControls />
      </div>
    </header>
  );
}
