import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { SpinnerGap } from "@phosphor-icons/react";
import AppShell from "./layout/AppShell";
import { normalizeNavigationPage, type NavigateOptions, type PageId, type StartMode } from "./navigation/pages";
import BoxSetupPage from "./pages/BoxSetupPage";
import HelpPage from "./pages/HelpPage";
import ImportPdbqtPage from "./pages/ImportPdbqtPage";
import PreparationPage from "./pages/PreparationPage";
import ProjectCreatePage from "./pages/ProjectCreatePage";
import ProjectDashboardPage from "./pages/ProjectDashboardPage";
import ReportPage from "./pages/ReportPage";
import ResultPage from "./pages/ResultPage";
import RunRequiredPage from "./pages/RunRequiredPage";
import RunExecutePage from "./pages/RunExecutePage";
import RunPreparePage from "./pages/RunPreparePage";
import SettingsPage from "./pages/SettingsPage";
import StructureFetchPage from "./pages/StructureFetchPage";
import ToolCheckPage from "./pages/ToolCheckPage";
const ToolchainStatusPage = lazy(() => import("./pages/ToolchainStatusPage"));

import VinaConfigPage from "./pages/VinaConfigPage";
import VinaParamPage from "./pages/VinaParamPage";
import type { DockStartProject, ProjectWorkflowStatusResponse } from "./types";
import { listenForBackgroundTaskUpdates } from "./utils/backgroundTasks";
import {
  DebouncedProjectRefresh,
  isSameProjectDir,
  shouldRefreshProjectAfterTask,
} from "./utils/backgroundProjectRefresh";
import { getWorkflowSummary } from "./utils/workflowSummary";
import { buildWorkflowSteps } from "./utils/workflowSteps";

function projectStateKey(project: DockStartProject): string {
  return `${project.project_dir}\u0000${project.updated_at ?? ""}`;
}

export default function App() {
  const [currentPage, setCurrentPage] = useState<PageId>("home");
  const [currentProject, setCurrentProject] = useState<DockStartProject | null>(null);
  const [currentRunId, setCurrentRunId] = useState("");
  const [workflowStatus, setWorkflowStatus] = useState<ProjectWorkflowStatusResponse | null>(null);
  const [projectStartMode, setProjectStartMode] = useState<StartMode>("basic");
  const [projectRevision, setProjectRevision] = useState(0);
  const committedProjectKeyRef = useRef("");

  const commitProject = useCallback((project: DockStartProject) => {
    setCurrentProject(project);
    const nextKey = projectStateKey(project);
    if (committedProjectKeyRef.current === nextKey) return;
    committedProjectKeyRef.current = nextKey;
    setProjectRevision((revision) => revision + 1);
  }, []);

  const commitProjectSnapshot = useCallback((project: DockStartProject) => {
    // This snapshot already came from a workflow-status read. Updating the
    // revision here would immediately schedule the same Python read again.
    committedProjectKeyRef.current = projectStateKey(project);
    setCurrentProject(project);
  }, []);

  const commitWorkflowStatus = useCallback((status: ProjectWorkflowStatusResponse | null) => {
    setWorkflowStatus(status);
    const latestRunId = status?.latest_run?.run_id;
    if (typeof latestRunId === "string" && latestRunId) {
      setCurrentRunId(latestRunId);
    }
  }, []);

  const commitWorkflowSnapshot = useCallback((status: ProjectWorkflowStatusResponse) => {
    setWorkflowStatus(status);
    const latestRunId = status.latest_run?.run_id;
    if (typeof latestRunId === "string" && latestRunId) {
      setCurrentRunId((runId) => runId || latestRunId);
    }
  }, []);

  useEffect(() => {
    const projectDir = currentProject?.project_dir;
    if (!projectDir) return;

    let disposed = false;
    let unlisten: (() => void) | null = null;
    const refresh = new DebouncedProjectRefresh(async () => {
      try {
        const rawPayload = await invoke<string>("get_project_workflow_status", {
          projectDir,
        });
        if (disposed) return;
        const parsed = JSON.parse(rawPayload) as ProjectWorkflowStatusResponse;
        if (
          !parsed.ok
          || !parsed.project
          || !isSameProjectDir(parsed.project_dir, projectDir)
          || !isSameProjectDir(parsed.project.project_dir, projectDir)
        ) return;
        commitProjectSnapshot(parsed.project);
        commitWorkflowSnapshot(parsed);
      } catch {
        // Pages keep their own actionable error states. The application-level
        // listener is best-effort synchronization and must never interrupt
        // navigation when a refresh cannot be read.
      }
    });

    void listenForBackgroundTaskUpdates((status) => {
      if (shouldRefreshProjectAfterTask(status, projectDir)) refresh.request();
    }).then((stopListening) => {
      if (disposed) stopListening();
      else unlisten = stopListening;
    }).catch(() => {
      // A failed global subscription must not make the workbench unusable;
      // page-level task observers and manual refresh remain available.
    });

    return () => {
      disposed = true;
      refresh.dispose();
      unlisten?.();
    };
  }, [commitProjectSnapshot, commitWorkflowSnapshot, currentProject?.project_dir]);

  useEffect(() => {
    const projectDir = currentProject?.project_dir;
    if (!projectDir) {
      setWorkflowStatus(null);
      return;
    }

    // Home owns its workflow request and publishes it through
    // onWorkflowChange. Starting the same Python module here as well made one
    // home mount fan out into duplicate backend processes (doubled again by
    // StrictMode during development).
    if (
      currentPage === "home"
      || currentPage === "structure-fetch"
      || currentPage === "preparation"
      || currentPage === "import-pdbqt"
    ) return;

    let cancelled = false;
    async function refreshWorkflowStatus() {
      try {
        const rawPayload = await invoke<string>("get_project_workflow_status", {
          projectDir,
        });
        const parsed = JSON.parse(rawPayload) as ProjectWorkflowStatusResponse;
        if (cancelled) return;
        commitWorkflowSnapshot(parsed);
        if (parsed.project) {
          commitProjectSnapshot(parsed.project);
        }
      } catch {
        if (!cancelled) setWorkflowStatus(null);
      }
    }

    // Let the destination page paint first and collapse multiple project
    // commits from one user action into a single refresh.
    const refreshTimer = window.setTimeout(() => {
      void refreshWorkflowStatus();
    }, 120);
    return () => {
      cancelled = true;
      window.clearTimeout(refreshTimer);
    };
    // currentPage is intentionally not a dependency: navigation alone does
    // not make project data stale. A project revision (or directory change)
    // is the refresh signal; Home and structure-input pages refresh themselves
    // so they do not fan one action out into duplicate Python status scripts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [commitProjectSnapshot, commitWorkflowSnapshot, currentProject?.project_dir, projectRevision]);

  function navigateTo(page: PageId, options?: NavigateOptions) {
    const destination = normalizeNavigationPage(page);
    if (destination === "project-create") {
      setProjectStartMode(options?.startMode ?? "basic");
    }
    setCurrentPage(destination);
  }

  function renderPage() {
    if (currentPage === "home") {
      return (
        <ProjectDashboardPage
          project={currentProject}
          onNavigate={navigateTo}
          onProjectChange={commitProject}
          onWorkflowChange={commitWorkflowStatus}
        />
      );
    }

    if (currentPage === "settings") {
      return <SettingsPage onBack={() => navigateTo("toolchain-status")} />;
    }

    if (currentPage === "toolchain-status") {
      return (
        <ToolchainStatusPage
          onBack={() => navigateTo("home")}
          onOpenHelp={() => navigateTo("help")}
          onOpenSettings={() => navigateTo("settings")}
        />
      );
    }

    if (currentPage === "tool-check") {
      return <ToolCheckPage onOpenSettings={() => navigateTo("settings")} />;
    }

    if (currentPage === "project-create") {
      return (
        <ProjectCreatePage
          startMode={projectStartMode}
          onBack={() => navigateTo("home")}
          onStartModeChange={setProjectStartMode}
          onCreated={(project, nextPage = "structure-fetch", runId = "") => {
            commitProject(project);
            setCurrentRunId(runId);
            setWorkflowStatus(null);
            navigateTo(nextPage);
          }}
        />
      );
    }

    if (currentPage === "help") {
      return <HelpPage project={currentProject} onNavigate={navigateTo} />;
    }

    if (currentPage === "structure-fetch" && currentProject) {
      return (
        <StructureFetchPage
          project={currentProject}
          onBack={() => navigateTo("preparation")}
          onProjectChange={commitProject}
          onOpenImportPdbqt={(project) => {
            commitProject(project);
            navigateTo("import-pdbqt");
          }}
          onOpenPreparation={(project) => {
            commitProject(project);
            navigateTo("preparation");
          }}
        />
      );
    }

    if (currentPage === "preparation" && currentProject) {
      return (
        <PreparationPage
          project={currentProject}
          onBack={() => navigateTo("structure-fetch")}
          onProjectChange={commitProject}
          onOpenImportPdbqt={(project) => {
            commitProject(project);
            navigateTo("import-pdbqt");
          }}
          onOpenBoxSetup={(project) => {
            commitProject(project);
            navigateTo("run-prepare");
          }}
        />
      );
    }

    if (currentPage === "import-pdbqt" && currentProject) {
      return (
        <ImportPdbqtPage
          project={currentProject}
          onBack={() => navigateTo("preparation")}
          onOpenStructureFetch={(project) => {
            commitProject(project);
            navigateTo("structure-fetch");
          }}
          onOpenBoxSetup={(project) => {
            commitProject(project);
            navigateTo("run-prepare");
          }}
          onProjectChange={commitProject}
        />
      );
    }

    if (currentPage === "box-setup" && currentProject) {
      return (
        <BoxSetupPage
          project={currentProject}
          onBack={() => navigateTo("import-pdbqt")}
          onProjectChange={commitProject}
          onOpenVinaParams={(project) => {
            commitProject(project);
            navigateTo("vina-param");
          }}
        />
      );
    }

    if (currentPage === "vina-param" && currentProject) {
      return (
        <VinaParamPage
          project={currentProject}
          onBack={() => navigateTo("box-setup")}
          onProjectChange={commitProject}
          onOpenVinaConfig={(project) => {
            commitProject(project);
            navigateTo("vina-config");
          }}
        />
      );
    }

    if (currentPage === "vina-config" && currentProject) {
      return (
        <VinaConfigPage
          project={currentProject}
          onBack={() => navigateTo("vina-param")}
          onProjectChange={commitProject}
          onOpenRunPrepare={(project) => {
            commitProject(project);
            navigateTo("run-prepare");
          }}
        />
      );
    }

    if (currentPage === "run-prepare" && currentProject) {
      return (
        <RunPreparePage
          project={currentProject}
          onBack={() => navigateTo("preparation")}
          onNavigate={navigateTo}
          onProjectChange={commitProject}
          onOpenRunExecute={(project, runId) => {
            commitProject(project);
            setCurrentRunId(runId);
            navigateTo("run-execute");
          }}
          onOpenResultPage={(project, runId) => {
            commitProject(project);
            setCurrentRunId(runId);
            navigateTo("result");
          }}
        />
      );
    }

    if (currentPage === "run-execute" && currentProject && currentRunId) {
      return (
        <RunExecutePage
          project={currentProject}
          runId={currentRunId}
          onBack={() => navigateTo("run-prepare")}
          onProjectChange={commitProject}
          onOpenResultPage={(project, runId) => {
            commitProject(project);
            setCurrentRunId(runId);
            navigateTo("result");
          }}
        />
      );
    }

    if (currentPage === "run-execute" && currentProject && !currentRunId) {
      return <RunRequiredPage project={currentProject} requestedPage="run-execute" onNavigate={navigateTo} />;
    }

    if (currentPage === "result" && currentProject && currentRunId) {
      return (
        <ResultPage
          project={currentProject}
          runId={currentRunId}
          onBack={() => navigateTo("run-execute")}
          onProjectChange={commitProject}
          onOpenReportPage={(project, runId) => {
            commitProject(project);
            setCurrentRunId(runId);
            navigateTo("report");
          }}
        />
      );
    }

    if (currentPage === "result" && currentProject && !currentRunId) {
      return <RunRequiredPage project={currentProject} requestedPage="result" onNavigate={navigateTo} />;
    }



    if (currentPage === "report" && currentProject && currentRunId) {
      return (
        <ReportPage
          project={currentProject}
          runId={currentRunId}
          onBack={() => navigateTo("result")}
          onProjectChange={commitProject}
        />
      );
    }

    if (currentPage === "report" && currentProject && !currentRunId) {
      return <RunRequiredPage project={currentProject} requestedPage="report" onNavigate={navigateTo} />;
    }

    return (
      <ProjectDashboardPage
        project={currentProject}
        onNavigate={navigateTo}
        onProjectChange={commitProject}
        onWorkflowChange={commitWorkflowStatus}
      />
    );
  }

  return (
    <AppShell
      currentPage={currentPage}
      project={currentProject}
      workflowSummary={getWorkflowSummary(currentProject, currentPage)}
      workflowSteps={buildWorkflowSteps(currentProject, workflowStatus)}
      onNavigate={navigateTo}
    >
      <Suspense
        fallback={(
          <section aria-live="polite" className="page-loading-state" role="status">
            <SpinnerGap aria-hidden="true" className="page-loading-indicator" size={24} weight="bold" />
            <div>
              <strong>正在打开工作区</strong>
              <p>正在加载当前页面所需的本地组件。</p>
            </div>
          </section>
        )}
      >
        {renderPage()}
      </Suspense>
    </AppShell>
  );
}
