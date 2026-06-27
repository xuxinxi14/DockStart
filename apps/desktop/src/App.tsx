import { useState } from "react";
import AppShell from "./layout/AppShell";
import type { PageId } from "./navigation/pages";
import BoxSetupPage from "./pages/BoxSetupPage";
import ImportPdbqtPage from "./pages/ImportPdbqtPage";
import PreparationPage from "./pages/PreparationPage";
import ProjectCreatePage from "./pages/ProjectCreatePage";
import ProjectDashboardPage from "./pages/ProjectDashboardPage";
import ReportPage from "./pages/ReportPage";
import ResultPage from "./pages/ResultPage";
import RunExecutePage from "./pages/RunExecutePage";
import RunPreparePage from "./pages/RunPreparePage";
import SettingsPage from "./pages/SettingsPage";
import StructureFetchPage from "./pages/StructureFetchPage";
import ToolCheckPage from "./pages/ToolCheckPage";
import ToolchainStatusPage from "./pages/ToolchainStatusPage";
import ViewerPage from "./pages/ViewerPage";
import VinaConfigPage from "./pages/VinaConfigPage";
import VinaParamPage from "./pages/VinaParamPage";
import type { DockStartProject } from "./types";
import { getWorkflowSummary } from "./utils/workflowSummary";

export default function App() {
  const [currentPage, setCurrentPage] = useState<PageId>("home");
  const [currentProject, setCurrentProject] = useState<DockStartProject | null>(null);
  const [currentRunId, setCurrentRunId] = useState("");

  function navigateTo(page: PageId) {
    setCurrentPage(page);
  }

  function renderPage() {
    if (currentPage === "home") {
      return (
        <ProjectDashboardPage
          project={currentProject}
          onNavigate={navigateTo}
          onProjectChange={setCurrentProject}
        />
      );
    }

    if (currentPage === "settings") {
      return <SettingsPage onBack={() => navigateTo("tool-check")} />;
    }

    if (currentPage === "toolchain-status") {
      return <ToolchainStatusPage onBack={() => navigateTo("home")} />;
    }

    if (currentPage === "tool-check") {
      return <ToolCheckPage onOpenSettings={() => navigateTo("settings")} />;
    }

    if (currentPage === "project-create") {
      return (
        <ProjectCreatePage
          onBack={() => navigateTo("home")}
          onCreated={(project) => {
            setCurrentProject(project);
            navigateTo("home");
          }}
        />
      );
    }

    if (currentPage === "structure-fetch" && currentProject) {
      return (
        <StructureFetchPage
          project={currentProject}
          onBack={() => navigateTo("project-create")}
          onProjectChange={setCurrentProject}
          onOpenImportPdbqt={(project) => {
            setCurrentProject(project);
            navigateTo("import-pdbqt");
          }}
          onOpenPreparation={(project) => {
            setCurrentProject(project);
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
          onProjectChange={setCurrentProject}
          onOpenImportPdbqt={(project) => {
            setCurrentProject(project);
            navigateTo("import-pdbqt");
          }}
          onOpenViewer={(project) => {
            setCurrentProject(project);
            navigateTo("viewer");
          }}
        />
      );
    }

    if (currentPage === "import-pdbqt" && currentProject) {
      return (
        <ImportPdbqtPage
          project={currentProject}
          onBack={() => navigateTo("project-create")}
          onOpenStructureFetch={(project) => {
            setCurrentProject(project);
            navigateTo("structure-fetch");
          }}
          onOpenBoxSetup={(project) => {
            setCurrentProject(project);
            navigateTo("box-setup");
          }}
          onOpenViewer={(project) => {
            setCurrentProject(project);
            navigateTo("viewer");
          }}
          onProjectChange={setCurrentProject}
        />
      );
    }

    if (currentPage === "box-setup" && currentProject) {
      return (
        <BoxSetupPage
          project={currentProject}
          onBack={() => navigateTo("import-pdbqt")}
          onProjectChange={setCurrentProject}
          onOpenViewer={(project) => {
            setCurrentProject(project);
            navigateTo("viewer");
          }}
          onOpenVinaParams={(project) => {
            setCurrentProject(project);
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
          onProjectChange={setCurrentProject}
          onOpenVinaConfig={(project) => {
            setCurrentProject(project);
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
          onProjectChange={setCurrentProject}
          onOpenRunPrepare={(project) => {
            setCurrentProject(project);
            navigateTo("run-prepare");
          }}
        />
      );
    }

    if (currentPage === "run-prepare" && currentProject) {
      return (
        <RunPreparePage
          project={currentProject}
          onBack={() => navigateTo("vina-config")}
          onProjectChange={setCurrentProject}
          onOpenRunExecute={(project, runId) => {
            setCurrentProject(project);
            setCurrentRunId(runId);
            navigateTo("run-execute");
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
          onProjectChange={setCurrentProject}
          onOpenResultPage={(project, runId) => {
            setCurrentProject(project);
            setCurrentRunId(runId);
            navigateTo("result");
          }}
        />
      );
    }

    if (currentPage === "result" && currentProject && currentRunId) {
      return (
        <ResultPage
          project={currentProject}
          runId={currentRunId}
          onBack={() => navigateTo("run-execute")}
          onProjectChange={setCurrentProject}
          onOpenViewer={(project) => {
            setCurrentProject(project);
            navigateTo("viewer");
          }}
          onOpenReportPage={(project, runId) => {
            setCurrentProject(project);
            setCurrentRunId(runId);
            navigateTo("report");
          }}
        />
      );
    }

    if (currentPage === "viewer" && currentProject) {
      return (
        <ViewerPage
          project={currentProject}
          onBack={() => navigateTo("preparation")}
          onProjectChange={setCurrentProject}
        />
      );
    }

    if (currentPage === "report" && currentProject && currentRunId) {
      return (
        <ReportPage
          project={currentProject}
          runId={currentRunId}
          onBack={() => navigateTo("result")}
          onProjectChange={setCurrentProject}
        />
      );
    }

    return (
      <ProjectDashboardPage
        project={currentProject}
        onNavigate={navigateTo}
        onProjectChange={setCurrentProject}
      />
    );
  }

  return (
    <AppShell
      currentPage={currentPage}
      project={currentProject}
      workflowSummary={getWorkflowSummary(currentProject, currentPage)}
      onNavigate={navigateTo}
    >
      {renderPage()}
    </AppShell>
  );
}
