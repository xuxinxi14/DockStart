import { useEffect, useState, type ReactNode } from "react";
import { FolderSimple, Monitor } from "@phosphor-icons/react";
import { invoke } from "@tauri-apps/api/core";
import type { DockStartProject } from "../types";
import { appVersion, pageTitles, type NavigateHandler, type PageId } from "../navigation/pages";
import LayoutDebugOverlay from "../components/layout/LayoutDebugOverlay";
import Sidebar from "./Sidebar";
import type { DistributionProfileStatus } from "./Sidebar";
import Topbar from "./Topbar";
import type { WorkflowStep } from "../components/WorkflowStepper";

type AppShellProps = {
  currentPage: PageId;
  project: DockStartProject | null;
  workflowSummary: string;
  workflowSteps?: WorkflowStep[];
  onNavigate: NavigateHandler;
  onOpenProject: () => void;
  children: ReactNode;
};

export type ThemeMode = "dark" | "light";

function readInitialTheme(): ThemeMode {
  return window.localStorage.getItem("dockstart-theme") === "light" ? "light" : "dark";
}

export default function AppShell({
  currentPage,
  project,
  workflowSummary,
  workflowSteps = [],
  onNavigate,
  onOpenProject,
  children,
}: AppShellProps) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [theme, setTheme] = useState<ThemeMode>(readInitialTheme);
  const [distributionProfile, setDistributionProfile] = useState<DistributionProfileStatus>({
    releaseProfile: "unknown",
    displayName: "识别中",
    message: "正在读取当前安装包的发布 profile。",
  });

  useEffect(() => {
    let cancelled = false;
    void invoke<string>("get_distribution_profile")
      .then((rawPayload) => {
        if (cancelled) return;
        const payload = JSON.parse(rawPayload) as {
          release_profile?: unknown;
          display_name?: unknown;
          message?: unknown;
        };
        const releaseProfile = payload.release_profile === "basic_stable" || payload.release_profile === "assisted_stable"
          ? payload.release_profile
          : "unknown";
        setDistributionProfile({
          releaseProfile,
          displayName: typeof payload.display_name === "string" ? payload.display_name : "Profile 未知",
          message: typeof payload.message === "string" ? payload.message : "无法识别当前安装包的发布 profile。",
        });
      })
      .catch(() => {
        if (cancelled) return;
        setDistributionProfile({
          releaseProfile: "unknown",
          displayName: "Profile 未知",
          message: "无法读取当前安装包的发布清单。",
        });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const preventContextMenu = (event: MouseEvent) => event.preventDefault();

    document.addEventListener("contextmenu", preventContextMenu, true);
    return () => document.removeEventListener("contextmenu", preventContextMenu, true);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    window.localStorage.setItem("dockstart-theme", theme);
  }, [theme]);

  return (
    <div className={`dockstart-shell ${sidebarCollapsed ? "sidebar-collapsed" : ""}`.trim()}>
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <Sidebar
        collapsed={sidebarCollapsed}
        currentPage={currentPage}
        distributionProfile={distributionProfile}
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
          theme={theme}
          onToggleTheme={() => setTheme((current) => current === "dark" ? "light" : "dark")}
          onNavigate={onNavigate}
          onOpenProject={onOpenProject}
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
