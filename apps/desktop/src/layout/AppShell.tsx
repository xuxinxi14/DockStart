import { useEffect, useRef, useState, type ReactNode } from "react";
import { FolderSimple, Monitor } from "@phosphor-icons/react";
import { invoke } from "@tauri-apps/api/core";
import type { DockStartProject } from "../types";
import { appVersion, pageTitles, type NavigateHandler, type PageId } from "../navigation/pages";
import LayoutDebugOverlay from "../components/layout/LayoutDebugOverlay";
import Tooltip from "../components/Tooltip";
import Sidebar from "./Sidebar";
import type { DistributionProfileStatus } from "./Sidebar";
import Topbar from "./Topbar";
import type { WorkflowStep } from "../components/WorkflowStepper";
import { isTerminalBackgroundTask, listenForBackgroundTaskUpdates } from "../utils/backgroundTasks";
import { isSameProjectDir } from "../utils/backgroundProjectRefresh";
import {
  APPEARANCE_CHANGE_EVENT,
  SYSTEM_DARK_MEDIA_QUERY,
  applyAppearanceState,
  normalizeThemeMode,
  prefersDarkColorScheme,
  readAppearanceState,
  resolveThemeMode,
  setThemePreference,
  type AppearanceState,
  type ResolvedTheme,
} from "../utils/themePreference";

type AppShellProps = {
  currentPage: PageId;
  project: DockStartProject | null;
  workflowSummary: string;
  workflowSteps?: WorkflowStep[];
  onNavigate: NavigateHandler;
  onOpenProject: () => void;
  children: ReactNode;
};

function readInitialSidebarState(): boolean {
  return window.localStorage.getItem("dockstart-sidebar-collapsed") === "true";
}

function readInitialCompactViewport(): boolean {
  return window.matchMedia("(max-width: 980px)").matches;
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
  const [sidebarCollapsed, setSidebarCollapsed] = useState(readInitialSidebarState);
  const [compactViewport, setCompactViewport] = useState(readInitialCompactViewport);
  const [appearance, setAppearance] = useState<AppearanceState>(readAppearanceState);
  const [systemPrefersDark, setSystemPrefersDark] = useState(prefersDarkColorScheme);
  const [batchScreeningCompleted, setBatchScreeningCompleted] = useState(false);
  const [distributionProfile, setDistributionProfile] = useState<DistributionProfileStatus>({
    releaseProfile: "unknown",
    displayName: "识别中",
    message: "正在识别当前安装包类型。",
  });
  const mainContentRef = useRef<HTMLElement>(null);

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
          displayName: typeof payload.display_name === "string" ? payload.display_name : "版本类型未知",
          message: typeof payload.message === "string" ? payload.message : "无法识别当前安装包类型。",
        });
      })
      .catch(() => {
        if (cancelled) return;
        setDistributionProfile({
          releaseProfile: "unknown",
          displayName: "版本类型未知",
          message: "无法读取当前安装包的发布清单。",
        });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    let unlisten: (() => void) | null = null;
    const projectDir = project?.project_dir;
    setBatchScreeningCompleted(false);
    if (!projectDir) return () => {
      cancelled = true;
    };

    const refreshScreeningStatus = async () => {
      try {
        const rawPayload = await invoke<string>("get_screening_status", { projectDir });
        if (cancelled) return;
        const payload = JSON.parse(rawPayload) as {
          screening?: { status?: unknown } | null;
        };
        setBatchScreeningCompleted(payload.screening?.status === "completed");
      } catch {
        if (!cancelled) setBatchScreeningCompleted(false);
      }
    };

    void refreshScreeningStatus();
    void listenForBackgroundTaskUpdates((status) => {
      if (
        status.kind === "screening"
        && isTerminalBackgroundTask(status)
        && isSameProjectDir(status.project_dir, projectDir)
      ) {
        void refreshScreeningStatus();
      }
    }).then((stopListening) => {
      if (cancelled) stopListening();
      else unlisten = stopListening;
    }).catch(() => {
      // Initial status remains authoritative when native event listening is unavailable.
    });

    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [project?.project_dir]);

  useEffect(() => {
    const mediaQuery = window.matchMedia("(max-width: 980px)");
    const handleChange = (event: MediaQueryListEvent) => setCompactViewport(event.matches);
    setCompactViewport(mediaQuery.matches);
    mediaQuery.addEventListener("change", handleChange);
    return () => mediaQuery.removeEventListener("change", handleChange);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const media = window.matchMedia(SYSTEM_DARK_MEDIA_QUERY);
    const handleChange = (event: MediaQueryListEvent) => setSystemPrefersDark(event.matches);
    setSystemPrefersDark(media.matches);
    media.addEventListener("change", handleChange);
    return () => media.removeEventListener("change", handleChange);
  }, []);

  useEffect(() => {
    // Shared with the Settings page: applying through the helper keeps every
    // document attribute, the stored preferences and other listeners in sync.
    applyAppearanceState(appearance, systemPrefersDark);
  }, [appearance, systemPrefersDark]);

  useEffect(() => {
    const handleAppearanceChange = (event: Event) => {
      const detail = (event as CustomEvent<AppearanceState>).detail;
      setAppearance(detail ? { mode: normalizeThemeMode(detail.mode), appearance: detail.appearance, accent: detail.accent } : readAppearanceState());
    };
    window.addEventListener(APPEARANCE_CHANGE_EVENT, handleAppearanceChange as EventListener);
    return () => window.removeEventListener(APPEARANCE_CHANGE_EVENT, handleAppearanceChange as EventListener);
  }, []);

  useEffect(() => {
    window.localStorage.setItem("dockstart-sidebar-collapsed", String(sidebarCollapsed));
  }, [sidebarCollapsed]);

  useEffect(() => {
    const animationFrame = window.requestAnimationFrame(() => {
      const main = mainContentRef.current;
      if (!main) return;
      main.scrollTo({ top: 0, behavior: "auto" });
      main.focus({ preventScroll: true });
      document.title = `DockStart · ${pageTitles[currentPage]}`;
    });
    return () => window.cancelAnimationFrame(animationFrame);
  }, [currentPage]);

  const effectiveSidebarCollapsed = sidebarCollapsed || compactViewport;
  const resolvedTheme: ResolvedTheme = resolveThemeMode(appearance.mode, systemPrefersDark);

  return (
    <div className={`dockstart-shell ${effectiveSidebarCollapsed ? "sidebar-collapsed" : ""}`.trim()}>
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <Sidebar
        collapsed={effectiveSidebarCollapsed}
        currentPage={currentPage}
        distributionProfile={distributionProfile}
        project={project}
        workflowSteps={workflowSteps}
        batchScreeningCompleted={batchScreeningCompleted}
        onNavigate={onNavigate}
        onToggleCollapsed={compactViewport ? undefined : () => setSidebarCollapsed((value) => !value)}
      />
      <div className="dockstart-workspace">
        <Topbar
          currentPage={currentPage}
          project={project}
          workflowSummary={workflowSummary}
          theme={resolvedTheme}
          themeMode={appearance.mode}
          onToggleTheme={() => setThemePreference(resolvedTheme === "dark" ? "light" : "dark")}
          onNavigate={onNavigate}
          onOpenProject={onOpenProject}
        />
        <main className="app-content" data-layout="app-content" id="main-content" ref={mainContentRef} tabIndex={-1}>
          <span aria-live="polite" className="ds-visually-hidden">已进入{pageTitles[currentPage]}</span>
          <div className="app-page-frame" key={currentPage}>{children}</div>
        </main>
        <footer className="app-statusbar" aria-label="当前工作区状态">
          <Tooltip className="statusbar-project-tooltip" label={project?.project_dir || "尚未加载项目"}>
            <span className="statusbar-project">
              <FolderSimple aria-hidden="true" size={15} weight="duotone" />
              <span>{project?.project_dir || "尚未加载项目"}</span>
            </span>
          </Tooltip>
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
