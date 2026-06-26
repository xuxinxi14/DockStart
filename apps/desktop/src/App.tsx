import { useState } from "react";
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
import VinaConfigPage from "./pages/VinaConfigPage";
import VinaParamPage from "./pages/VinaParamPage";
import type { DockStartProject } from "./types";

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
  const [currentPage, setCurrentPage] = useState<
    | "home"
    | "tool-check"
    | "toolchain-status"
    | "settings"
    | "project-create"
    | "structure-fetch"
    | "preparation"
    | "import-pdbqt"
    | "box-setup"
    | "vina-param"
    | "vina-config"
    | "run-prepare"
    | "run-execute"
    | "result"
    | "report"
  >("home");
  const [currentProject, setCurrentProject] = useState<DockStartProject | null>(null);
  const [currentRunId, setCurrentRunId] = useState("");

  if (currentPage === "settings") {
    return (
      <main className="app-shell">
        <SettingsPage onBack={() => setCurrentPage("tool-check")} />
      </main>
    );
  }

  if (currentPage === "toolchain-status") {
    return (
      <main className="app-shell">
        <ToolchainStatusPage onBack={() => setCurrentPage("home")} />
      </main>
    );
  }

  if (currentPage === "tool-check") {
    return (
      <main className="app-shell">
        <button className="text-button" type="button" onClick={() => setCurrentPage("home")}>
          返回首页
        </button>
        <ToolCheckPage onOpenSettings={() => setCurrentPage("settings")} />
      </main>
    );
  }

  if (currentPage === "project-create") {
    return (
      <main className="app-shell">
        <ProjectCreatePage
          onBack={() => setCurrentPage("home")}
          onCreated={(project, nextPage) => {
            setCurrentProject(project);
            setCurrentPage(nextPage);
          }}
        />
      </main>
    );
  }

  if (currentPage === "structure-fetch" && currentProject) {
    return (
      <main className="app-shell">
        <StructureFetchPage
          project={currentProject}
          onBack={() => setCurrentPage("project-create")}
          onProjectChange={setCurrentProject}
          onOpenImportPdbqt={(project) => {
            setCurrentProject(project);
            setCurrentPage("import-pdbqt");
          }}
          onOpenPreparation={(project) => {
            setCurrentProject(project);
            setCurrentPage("preparation");
          }}
        />
      </main>
    );
  }

  if (currentPage === "preparation" && currentProject) {
    return (
      <main className="app-shell">
        <PreparationPage
          project={currentProject}
          onBack={() => setCurrentPage("structure-fetch")}
          onProjectChange={setCurrentProject}
          onOpenImportPdbqt={(project) => {
            setCurrentProject(project);
            setCurrentPage("import-pdbqt");
          }}
        />
      </main>
    );
  }

  if (currentPage === "import-pdbqt" && currentProject) {
    return (
      <main className="app-shell">
        <ImportPdbqtPage
          project={currentProject}
          onBack={() => setCurrentPage("project-create")}
          onOpenStructureFetch={(project) => {
            setCurrentProject(project);
            setCurrentPage("structure-fetch");
          }}
          onOpenBoxSetup={(project) => {
            setCurrentProject(project);
            setCurrentPage("box-setup");
          }}
          onProjectChange={setCurrentProject}
        />
      </main>
    );
  }

  if (currentPage === "box-setup" && currentProject) {
    return (
      <main className="app-shell">
        <BoxSetupPage
          project={currentProject}
          onBack={() => setCurrentPage("import-pdbqt")}
          onProjectChange={setCurrentProject}
          onOpenVinaParams={(project) => {
            setCurrentProject(project);
            setCurrentPage("vina-param");
          }}
        />
      </main>
    );
  }

  if (currentPage === "vina-param" && currentProject) {
    return (
      <main className="app-shell">
        <VinaParamPage
          project={currentProject}
          onBack={() => setCurrentPage("box-setup")}
          onProjectChange={setCurrentProject}
          onOpenVinaConfig={(project) => {
            setCurrentProject(project);
            setCurrentPage("vina-config");
          }}
        />
      </main>
    );
  }

  if (currentPage === "vina-config" && currentProject) {
    return (
      <main className="app-shell">
        <VinaConfigPage
          project={currentProject}
          onBack={() => setCurrentPage("vina-param")}
          onProjectChange={setCurrentProject}
          onOpenRunPrepare={(project) => {
            setCurrentProject(project);
            setCurrentPage("run-prepare");
          }}
        />
      </main>
    );
  }

  if (currentPage === "run-prepare" && currentProject) {
    return (
      <main className="app-shell">
        <RunPreparePage
          project={currentProject}
          onBack={() => setCurrentPage("vina-config")}
          onProjectChange={setCurrentProject}
          onOpenRunExecute={(project, runId) => {
            setCurrentProject(project);
            setCurrentRunId(runId);
            setCurrentPage("run-execute");
          }}
        />
      </main>
    );
  }

  if (currentPage === "run-execute" && currentProject && currentRunId) {
    return (
      <main className="app-shell">
        <RunExecutePage
          project={currentProject}
          runId={currentRunId}
          onBack={() => setCurrentPage("run-prepare")}
          onProjectChange={setCurrentProject}
          onOpenResultPage={(project, runId) => {
            setCurrentProject(project);
            setCurrentRunId(runId);
            setCurrentPage("result");
          }}
        />
      </main>
    );
  }

  if (currentPage === "result" && currentProject && currentRunId) {
    return (
      <main className="app-shell">
        <ResultPage
          project={currentProject}
          runId={currentRunId}
          onBack={() => setCurrentPage("run-execute")}
          onProjectChange={setCurrentProject}
          onOpenReportPage={(project, runId) => {
            setCurrentProject(project);
            setCurrentRunId(runId);
            setCurrentPage("report");
          }}
        />
      </main>
    );
  }

  if (currentPage === "report" && currentProject && currentRunId) {
    return (
      <main className="app-shell">
        <ReportPage
          project={currentProject}
          runId={currentRunId}
          onBack={() => setCurrentPage("result")}
          onProjectChange={setCurrentProject}
        />
      </main>
    );
  }

  return (
    <main className="app-shell">
      <section className="intro">
        <p className="eyebrow">DockStart MVP</p>
        <h1>DockStart</h1>
        <p>
          基于 AutoDock Vina 的第三方开源中文分子对接工作台。当前版本围绕
          最小闭环逐步实现，不修改对接算法，不直接内置复杂第三方工具。
        </p>
        <div className="hero-actions">
          <button className="primary-button" type="button" onClick={() => setCurrentPage("tool-check")}>
            进入工具检测
          </button>
          <button className="secondary-button" type="button" onClick={() => setCurrentPage("settings")}>
            配置工具路径
          </button>
          <button className="secondary-button" type="button" onClick={() => setCurrentPage("toolchain-status")}>
            内置工具链状态
          </button>
          <button className="secondary-button" type="button" onClick={() => setCurrentPage("project-create")}>
            创建项目
          </button>
        </div>
      </section>

      <section className="panel" aria-labelledby="raw-prepared-flow">
        <h2 id="raw-prepared-flow">当前推荐流程</h2>
        <ol className="step-list">
          <li>下载 raw 原始结构</li>
          <li>检查/准备 PDBQT</li>
          <li>导入 prepared PDBQT</li>
          <li>设置 Box 和 Vina 参数</li>
          <li>运行 Vina 并解析报告</li>
        </ol>
        <p className="placeholder-note">
          raw 文件只是 PDB/CIF/SDF 原始结构；prepared/receptor.pdbqt 和 prepared/ligand.pdbqt 才是 Vina 当前可用输入。
        </p>
      </section>

      <section className="panel" aria-labelledby="mvp-pages">
        <h2 id="mvp-pages">MVP 页面顺序</h2>
        <ol className="step-list">
          {nextPages.map((page) => (
            <li key={page}>{page}</li>
          ))}
        </ol>
      </section>
    </main>
  );
}
