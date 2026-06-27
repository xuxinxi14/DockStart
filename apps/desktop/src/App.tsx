import { useState } from "react";
import ActionButton from "./components/ActionButton";
import PageHeader from "./components/PageHeader";
import SectionCard from "./components/SectionCard";
import AppShell from "./layout/AppShell";
import type { PageId } from "./navigation/pages";
import BoxSetupPage from "./pages/BoxSetupPage";
import ImportPdbqtPage from "./pages/ImportPdbqtPage";
import PreparationPage from "./pages/PreparationPage";
import ProjectCreatePage from "./pages/ProjectCreatePage";
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

const nextPages = [
  "工具检测",
  "内置工具链状态",
  "创建项目",
  "下载原始结构",
  "自动准备 PDBQT",
  "导入 PDBQT",
  "设置对接箱体",
  "设置 Vina 参数",
  "生成配置文件",
  "运行前检查",
  "执行 Vina",
  "查看结果",
  "导出报告",
];

export default function App() {
  const [currentPage, setCurrentPage] = useState<PageId>("home");
  const [currentProject, setCurrentProject] = useState<DockStartProject | null>(null);
  const [currentRunId, setCurrentRunId] = useState("");

  function navigateTo(page: PageId) {
    setCurrentPage(page);
  }

  function renderHome() {
    return (
      <>
        <PageHeader
          eyebrow="DockStart Workflow"
          title="项目总览"
          description="DockStart 是基于 AutoDock Vina 的第三方开源中文分子对接工作台。V0.5 开始把割裂页面整理成更清晰的工作流入口。"
          actions={
            <>
              <ActionButton variant="primary" onClick={() => navigateTo("project-create")}>
                创建项目
              </ActionButton>
              <ActionButton onClick={() => navigateTo("tool-check")}>工具检测</ActionButton>
              <ActionButton onClick={() => navigateTo("toolchain-status")}>工具链状态</ActionButton>
            </>
          }
        />

        <SectionCard title="当前项目" description="V0.5.1 会把这里升级为完整 Project Dashboard。">
          {currentProject ? (
            <div className="project-summary">
              <span>项目名称</span>
              <strong>{currentProject.project_name}</strong>
              <span>项目目录</span>
              <code>{currentProject.project_dir}</code>
            </div>
          ) : (
            <p className="placeholder-note">尚未加载项目。请先创建项目，或从后续版本的 Dashboard 加载已有项目。</p>
          )}
          <div className="hero-actions project-toolbar">
            <ActionButton disabled={!currentProject} onClick={() => navigateTo("structure-fetch")}>
              获取结构
            </ActionButton>
            <ActionButton disabled={!currentProject} onClick={() => navigateTo("preparation")}>
              准备 PDBQT
            </ActionButton>
            <ActionButton disabled={!currentProject} onClick={() => navigateTo("viewer")}>
              3D 查看 / Box
            </ActionButton>
            <ActionButton disabled={!currentProject} onClick={() => navigateTo("vina-config")}>
              Vina 运行
            </ActionButton>
          </div>
        </SectionCard>

        <SectionCard title="当前推荐流程">
          <ol className="step-list">
            <li>下载 raw 原始结构</li>
            <li>检查/准备 PDBQT</li>
            <li>导入或确认 prepared PDBQT</li>
            <li>设置 Box 和 Vina 参数</li>
            <li>运行 Vina 并解析报告</li>
          </ol>
          <p className="placeholder-note">
            raw 文件只是 PDB/CIF/SDF 原始结构；prepared/receptor.pdbqt 和 prepared/ligand.pdbqt 才是 Vina 当前可用输入。
          </p>
        </SectionCard>

        <SectionCard title="现有页面仍可访问">
          <ol className="step-list">
            {nextPages.map((page) => (
              <li key={page}>{page}</li>
            ))}
          </ol>
        </SectionCard>
      </>
    );
  }

  function renderPage() {
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
          onCreated={(project, nextPage) => {
            setCurrentProject(project);
            navigateTo(nextPage);
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

    return renderHome();
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
