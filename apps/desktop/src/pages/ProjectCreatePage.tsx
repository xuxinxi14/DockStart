import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import { BodyGrid, MainPanel, ModeTabs, PageHero, PageShell, RightRail, RightRailSection } from "../components/layout/PageLayout";
import PathInput from "../components/PathInput";
import type { PageId, StartMode } from "../navigation/pages";
import type { DemoProjectSummary, DemoProjectsResponse, DockStartProject, ProjectResponse, SettingsResponse } from "../types";
import { writeDockingWorkspaceMode } from "../utils/dockingMode";

type ProjectCreatePageProps = {
  openExistingRequestKey?: number;
  startMode: StartMode;
  onBack: () => void;
  onCreated: (project: DockStartProject, nextPage: PageId, runId?: string) => void;
  onStartModeChange: (mode: StartMode) => void;
};

type ModeConfig = {
  title: string;
  subtitle: string;
  primaryLabel: string;
  currentPath: string;
  nextStep: string;
  requirement: string;
};

type AssistedSource = "online" | "local";

function projectModePanelId(mode: StartMode) {
  return `project-mode-panel-${mode}`;
}

const modeOptions: Array<{ id: StartMode; label: string; controlsId: string }> = [
  { id: "basic", label: "已有 PDBQT（直接对接）", controlsId: projectModePanelId("basic") },
  { id: "assisted", label: "PDB/CIF + SDF/MOL（准备并转换）", controlsId: projectModePanelId("assisted") },
  { id: "demo", label: "示例项目（快速体验）", controlsId: projectModePanelId("demo") },
];

const modeConfig: Record<StartMode, ModeConfig> = {
  basic: {
    title: "使用已有 PDBQT 直接开始对接",
    subtitle: "适合已经准备好受体与配体 PDBQT 的用户；导入后直接设置搜索范围。",
    primaryLabel: "创建项目并导入 PDBQT",
    currentPath: "已有 PDBQT",
    nextStep: "复核两个 PDBQT 文件",
    requirement: "AutoDock Vina",
  },
  assisted: {
    title: "将原始结构转换为 PDBQT",
    subtitle: "从在线数据库获取，或从电脑导入受体 PDB/CIF 与配体 SDF/MOL，再准备为 Vina 输入。",
    primaryLabel: "创建项目并进入格式转换",
    currentPath: "原始结构 → PDBQT",
    nextStep: "获取结构并完成格式转换",
    requirement: "Python、RDKit、Meeko",
  },
  demo: {
    title: "复制内置示例并快速体验",
    subtitle: "选择一个内置示例，复制到工作区后体验完整流程。",
    primaryLabel: "",
    currentPath: "内置示例",
    nextStep: "选择一个示例并复制到工作区",
    requirement: "示例项目资源",
  },
};

function parseProjectResponse(rawPayload: string): ProjectResponse {
  const parsed = JSON.parse(rawPayload) as Partial<ProjectResponse>;
  return {
    ok: Boolean(parsed.ok),
    project_dir: parsed.project_dir,
    project: parsed.project ?? null,
    demo_type: parsed.demo_type,
    entry_step: parsed.entry_step,
    entry_page: parsed.entry_page,
    entry_run_id: parsed.entry_run_id,
    target_name: parsed.target_name,
    disclaimer: parsed.disclaimer,
    message: parsed.message,
    error: parsed.error,
  };
}

function parseSettingsResponse(rawPayload: string): SettingsResponse {
  const parsed = JSON.parse(rawPayload) as Partial<SettingsResponse>;
  return {
    ok: Boolean(parsed.ok),
    settings_path: parsed.settings_path ?? "",
    settings: parsed.settings ?? null,
    error: parsed.error,
  };
}

function parseDemoProjectsResponse(rawPayload: string): DemoProjectsResponse {
  const parsed = JSON.parse(rawPayload) as Partial<DemoProjectsResponse>;
  return {
    ok: Boolean(parsed.ok),
    examples_root: parsed.examples_root ?? "",
    demos: parsed.demos ?? [],
    message: parsed.message ?? "",
    error: parsed.error ?? null,
  };
}

const pageIds: PageId[] = [
  "home",
  "tool-check",
  "toolchain-status",
  "settings",
  "project-create",
  "structure-fetch",
  "preparation",
  "import-pdbqt",
  "box-setup",
  "vina-param",
  "vina-config",
  "run-prepare",
  "run-execute",
  "result",
  "report",
  "help",
];

function isPageId(value: string | undefined): value is PageId {
  return Boolean(value && pageIds.includes(value as PageId));
}

function nextPageForDemo(response: ProjectResponse, demo: DemoProjectSummary): PageId {
  if (isPageId(response.entry_page)) return response.entry_page;
  if (demo.entry_step === "results") return "result";
  if (demo.mode === "assisted") return "preparation";
  return "import-pdbqt";
}

function demoToolHint(demo: DemoProjectSummary): string {
  const tools = demo.required_tools.map((tool) => tool.toLowerCase());
  if (tools.length === 0) {
    return "无需工具链，可直接查看示例结果。";
  }
  if (tools.includes("rdkit") || tools.includes("meeko") || tools.includes("python")) {
    return "Assisted Stable 已随附 Python、RDKit / Meeko；检测失败时仍可使用参考 PDBQT 继续。";
  }
  if (tools.includes("vina")) {
    return "运行对接前需要配置 AutoDock Vina。";
  }
  return "复制后可在对应步骤继续检查工具链。";
}

function projectFromResponse(response: ProjectResponse, fallbackMessage: string): DockStartProject {
  if (response.ok && response.project) {
    return response.project;
  }
  throw new Error(response.error?.message ?? fallbackMessage);
}

export default function ProjectCreatePage({
  openExistingRequestKey = 0,
  startMode,
  onBack,
  onCreated,
  onStartModeChange,
}: ProjectCreatePageProps) {
  const [projectName, setProjectName] = useState("demo_project");
  const [baseDir, setBaseDir] = useState("");
  const [existingProjectDir, setExistingProjectDir] = useState("");
  const [receptorPdbqtPath, setReceptorPdbqtPath] = useState("");
  const [ligandPdbqtPaths, setLigandPdbqtPaths] = useState<string[]>([]);
  const [receptorRawPath, setReceptorRawPath] = useState("");
  const [ligandRawPaths, setLigandRawPaths] = useState<string[]>([]);
  const [assistedSource, setAssistedSource] = useState<AssistedSource>("online");
  const [showExistingProject, setShowExistingProject] = useState(false);
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [demos, setDemos] = useState<DemoProjectsResponse["demos"]>([]);

  const currentConfig = modeConfig[startMode];
  const activeModeIndex = Math.max(0, modeOptions.findIndex((option) => option.id === startMode));

  useEffect(() => {
    async function loadDefaultProjectDir() {
      try {
        const rawPayload = await invoke<string>("get_settings");
        const response = parseSettingsResponse(rawPayload);
        if (response.ok && response.settings?.project.default_project_dir) {
          setBaseDir(response.settings.project.default_project_dir);
        }
      } catch {
        // Default directory is optional.
      }
    }

    void loadDefaultProjectDir();
  }, []);

  useEffect(() => {
    async function loadDemos() {
      try {
        const rawPayload = await invoke<string>("list_available_demo_projects");
        const response = parseDemoProjectsResponse(rawPayload);
        if (response.ok) {
          setDemos(response.demos);
        }
      } catch {
        setDemos([]);
      }
    }

    void loadDemos();
  }, []);

  const resetFeedback = () => {
    setMessage("");
    setRawError("");
  };

  const runProjectCommand = async (
    command: string,
    payload: Record<string, string>,
    fallbackMessage: string,
  ): Promise<DockStartProject> => {
    const rawPayload = await invoke<string>(command, payload);
    const response = parseProjectResponse(rawPayload);
    return projectFromResponse(response, fallbackMessage);
  };

  const createProject = useCallback(async () => {
    setIsBusy(true);
    resetFeedback();
    let createdProjectDir = "";
    try {
      let project = await runProjectCommand(
        "create_project",
        { projectName, baseDir },
        "项目创建失败。",
      );
      createdProjectDir = project.project_dir;

      if (startMode === "basic") {
        project = await runProjectCommand(
          "import_receptor_pdbqt",
          { projectDir: project.project_dir, sourcePath: receptorPdbqtPath },
          "受体 PDBQT 导入失败。",
        );
        project = await runProjectCommand(
          "import_ligand_pdbqt",
          { projectDir: project.project_dir, sourcePath: ligandPdbqtPaths[0] ?? "" },
          "配体 PDBQT 导入失败。",
        );
        if (ligandPdbqtPaths.length > 1) {
          const staged = JSON.parse(await invoke<string>("stage_screening_inputs", {
            projectDir: project.project_dir,
            files: ligandPdbqtPaths,
          })) as { ok?: boolean; error?: { message?: string } };
          if (!staged.ok) throw new Error(staged.error?.message || "多配体导入失败。");
          writeDockingWorkspaceMode(project.project_dir, "batch");
        }
        onCreated(project, "import-pdbqt");
        return;
      }

      if (startMode === "assisted") {
        if (assistedSource === "online") {
          onCreated(project, "structure-fetch");
          return;
        }
        project = await runProjectCommand(
          "import_receptor_raw_file",
          { projectDir: project.project_dir, sourcePath: receptorRawPath },
          "受体结构文件导入失败。",
        );
        if (ligandRawPaths.length > 1) {
          const staged = JSON.parse(await invoke<string>("stage_screening_inputs", {
            projectDir: project.project_dir,
            files: ligandRawPaths,
          })) as { ok?: boolean; staged?: Array<{ file?: string }>; error?: { message?: string } };
          if (!staged.ok || !staged.staged?.length) throw new Error(staged.error?.message || "多个配体自动准备失败。");
          project = await runProjectCommand(
            "import_ligand_pdbqt",
            { projectDir: project.project_dir, sourcePath: `${project.project_dir}\\${String(staged.staged[0].file || "").replace(/\//g, "\\")}` },
            "首个配体预览文件导入失败。",
          );
          writeDockingWorkspaceMode(project.project_dir, "batch");
        } else {
          project = await runProjectCommand(
            "import_ligand_raw_file",
            { projectDir: project.project_dir, sourcePath: ligandRawPaths[0] ?? "" },
            "配体结构文件导入失败。",
          );
        }
        onCreated(project, "preparation");
        return;
      }
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : "无法创建项目。";
      if (createdProjectDir) {
        setExistingProjectDir(createdProjectDir);
        setShowExistingProject(true);
        setMessage(`${errorMessage} 项目目录已安全保留；请点击“打开已有项目”继续补充缺失文件。`);
      } else {
        setMessage(errorMessage);
      }
      setRawError(error instanceof Error ? error.stack ?? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  }, [
    baseDir,
    assistedSource,
    ligandPdbqtPaths,
    ligandRawPaths,
    onCreated,
    projectName,
    receptorPdbqtPath,
    receptorRawPath,
    startMode,
  ]);

  const handledOpenRequestRef = useRef(0);

  const loadExistingProject = useCallback(async (projectDir = existingProjectDir) => {
    setIsBusy(true);
    resetFeedback();
    try {
      const rawPayload = await invoke<string>("load_project", {
        projectDir,
      });
      const response = parseProjectResponse(rawPayload);
      if (response.ok && response.project) {
        onCreated(response.project, "home");
        return;
      }
      setMessage(response.error?.message ?? "项目加载失败。");
      setRawError(response.error?.raw_error ?? "");
    } catch (error) {
      setMessage("无法加载项目。请确认当前运行环境是 DockStart 桌面端。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  }, [existingProjectDir, onCreated]);

  const pickAndLoadExistingProject = useCallback(async () => {
    setShowExistingProject(true);
    resetFeedback();
    try {
      const selected = await open({
        directory: true,
        multiple: false,
        title: "选择已有 DockStart 项目目录",
      });
      const projectDir = Array.isArray(selected) ? selected[0] ?? "" : selected ?? "";
      if (!projectDir) return;
      setExistingProjectDir(projectDir);
      await loadExistingProject(projectDir);
    } catch (error) {
      setMessage("无法打开项目目录选择器。");
      setRawError(error instanceof Error ? error.message : String(error));
    }
  }, [loadExistingProject]);

  useEffect(() => {
    if (
      openExistingRequestKey <= 0
      || handledOpenRequestRef.current === openExistingRequestKey
    ) return;
    handledOpenRequestRef.current = openExistingRequestKey;
    void pickAndLoadExistingProject();
  }, [openExistingRequestKey, pickAndLoadExistingProject]);

  const pickDemoDestinationDir = useCallback(async (): Promise<string> => {
    const currentDir = baseDir.trim();
    if (currentDir) return currentDir;
    try {
      const selected = await open({
        directory: true,
        multiple: false,
        title: "选择示例保存目录",
      });
      const nextDir = Array.isArray(selected) ? selected[0] ?? "" : selected ?? "";
      if (nextDir) {
        setBaseDir(nextDir);
        return nextDir;
      }
      setMessage("请选择一个工作区目录后再复制示例。");
      return "";
    } catch (error) {
      setMessage("无法打开目录选择器，请手动填写工作区目录。");
      setRawError(error instanceof Error ? error.message : String(error));
      return "";
    }
  }, [baseDir]);

  const pickLigandFiles = useCallback(async (kind: "pdbqt" | "raw") => {
    const selected = await open({
      directory: false,
      multiple: true,
      title: kind === "pdbqt" ? "选择一个或多个配体 PDBQT" : "选择一个或多个配体 SDF / MOL",
      filters: [kind === "pdbqt"
        ? { name: "AutoDock PDBQT", extensions: ["pdbqt"] }
        : { name: "Ligand structure", extensions: ["sdf", "mol"] }],
    });
    const files = Array.isArray(selected) ? selected : selected ? [selected] : [];
    if (kind === "pdbqt") setLigandPdbqtPaths(files);
    else setLigandRawPaths(files);
  }, []);

  const createDemo = useCallback(
    async (demo: DemoProjectSummary) => {
      setIsBusy(true);
      resetFeedback();
      try {
        const destinationDir = await pickDemoDestinationDir();
        if (!destinationDir) {
          return;
        }
        const rawPayload = await invoke<string>("create_demo_project", {
          destinationDir,
          demoType: demo.demo_type,
        });
        const response = parseProjectResponse(rawPayload);
        if (response.ok && response.project) {
          onCreated(response.project, nextPageForDemo(response, demo), response.entry_run_id || demo.entry_run_id || "");
          return;
        }
        setMessage(response.error?.message ?? "示例项目创建失败。");
        setRawError(response.error?.raw_error ?? "");
      } catch (error) {
        setMessage("无法创建示例项目。请确认当前运行环境是 DockStart 桌面端。");
        setRawError(error instanceof Error ? error.message : String(error));
      } finally {
        setIsBusy(false);
      }
    },
    [onCreated, pickDemoDestinationDir],
  );

  const canCreateBasic = Boolean(
    projectName.trim() && baseDir.trim() && receptorPdbqtPath.trim() && ligandPdbqtPaths.length,
  );
  const canCreateAssisted = Boolean(
    projectName.trim()
      && baseDir.trim()
      && (assistedSource === "online" || (receptorRawPath.trim() && ligandRawPaths.length)),
  );
  const canCreate = startMode === "basic" ? canCreateBasic : canCreateAssisted;

  const renderModeForm = () => {
    if (startMode === "demo") {
      return (
        <div className="main-panel-section">
          <div className="main-panel-section-header">
            <h2>示例项目</h2>
            <p>选择一个示例，复制到你的工作区。示例只用于学习 DockStart 操作流程，不用于药效判断或科研结论。</p>
          </div>
          <div className="demo-project-list">
            {demos.length === 0 ? (
              <div className="demo-project-empty">未检测到示例资源。请检查 resources/examples 是否随应用打包。</div>
            ) : null}
            {demos.map((demo) => {
              const disabled = isBusy || !demo.exists;
              const missingText = demo.missing_files.length > 0 ? demo.missing_files.slice(0, 3).join("、") : "";
              return (
                <button
                  className={`demo-project-card ${demo.exists ? "" : "missing"}`.trim()}
                  data-layout="task-card"
                  disabled={disabled}
                  key={demo.demo_type}
                  onClick={() => void createDemo(demo)}
                  type="button"
                >
                  <span className="demo-project-card-copy">
                    <span className="demo-project-title-row">
                      <strong>{demo.title}</strong>
                      {demo.tags.length > 0 ? (
                        <span className="demo-project-tags" aria-label="示例标签">
                          {demo.tags.map((tag) => (
                            <span className="demo-project-tag" key={tag}>{tag}</span>
                          ))}
                        </span>
                      ) : null}
                    </span>
                    <small>{demo.description}</small>
                    <small className="demo-project-hint">{demo.exists ? demoToolHint(demo) : "示例资源未找到"}</small>
                    {!demo.exists && missingText ? (
                      <small className="demo-project-warning">缺少：{missingText}</small>
                    ) : null}
                  </span>
                  <span className="secondary-button demo-project-card-action">
                    {demo.exists ? demo.button_label : "示例资源未找到"}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      );
    }

    return (
      <div className="main-panel-section">
        <div className="main-panel-section-header">
          <h2>项目信息</h2>
          <p>
            {startMode === "basic"
              ? "选择项目保存位置，并导入已经准备好的受体和配体 PDBQT。"
              : "先选择在线获取或本地导入；原始结构随后会准备并转换为 PDBQT。"}
          </p>
        </div>
        <div className="form-panel create-mode-form">
          <div className="form-field" data-layout="form-row">
            <label htmlFor="project-name">项目名称</label>
            <input
              id="project-name"
              type="text"
              value={projectName}
              onChange={(event) => setProjectName(event.target.value)}
              placeholder="例如 demo_project"
            />
          </div>

          <div className="form-field" data-layout="form-row">
            <label htmlFor="base-dir">保存目录</label>
            <PathInput
              id="base-dir"
              value={baseDir}
              onChange={setBaseDir}
              mode="directory"
              title="选择项目保存目录"
              placeholder="选择项目的父目录"
            />
          </div>

          {startMode === "basic" ? (
            <>
              <div className="form-field" data-layout="form-row">
                <label htmlFor="receptor-pdbqt">受体 PDBQT 文件</label>
                <PathInput
                  id="receptor-pdbqt"
                  value={receptorPdbqtPath}
                  onChange={setReceptorPdbqtPath}
                  mode="file"
                  title="选择 receptor.pdbqt"
                  placeholder="选择 receptor.pdbqt"
                  filters={[{ name: "PDBQT", extensions: ["pdbqt"] }]}
                />
              </div>

              <div className="form-field" data-layout="form-row">
                <label>配体 PDBQT 文件</label>
                <div className="multi-ligand-file-picker">
                  <ActionButton onClick={() => void pickLigandFiles("pdbqt")}>选择一个或多个 PDBQT</ActionButton>
                  <span>{ligandPdbqtPaths.length ? `已选择 ${ligandPdbqtPaths.length} 个；${ligandPdbqtPaths.length > 1 ? "将进入多配体模式" : "将进入单配体模式"}` : "尚未选择"}</span>
                </div>
              </div>
            </>
          ) : (
            <>
              <fieldset className="assisted-source-picker">
                <legend>原始结构从哪里获取？</legend>
                <div className="assisted-source-options">
                  <button
                    aria-pressed={assistedSource === "online"}
                    className={assistedSource === "online" ? "selected" : ""}
                    onClick={() => setAssistedSource("online")}
                    type="button"
                  >
                    <strong>在线搜索并下载</strong>
                    <span>受体使用 RCSB PDB ID；配体使用 PubChem CID 或名称。需要联网。</span>
                    <small>适合还没有原始结构文件</small>
                  </button>
                  <button
                    aria-pressed={assistedSource === "local"}
                    className={assistedSource === "local" ? "selected" : ""}
                    onClick={() => setAssistedSource("local")}
                    type="button"
                  >
                    <strong>从电脑导入文件</strong>
                    <span>受体支持 PDB/CIF；配体支持 SDF/MOL。</span>
                    <small>适合已经下载好原始结构</small>
                  </button>
                </div>
              </fieldset>

              {assistedSource === "local" ? (
                <>
                  <div className="form-field" data-layout="form-row">
                    <label htmlFor="receptor-raw">受体原始结构：PDB / CIF</label>
                    <PathInput
                      id="receptor-raw"
                      value={receptorRawPath}
                      onChange={setReceptorRawPath}
                      mode="file"
                      title="选择受体 PDB / CIF"
                      placeholder="选择受体 PDB / CIF"
                      filters={[{ name: "Receptor structure", extensions: ["pdb", "cif"] }]}
                    />
                  </div>

                  <div className="form-field" data-layout="form-row">
                    <label>配体原始结构：SDF / MOL</label>
                    <div className="multi-ligand-file-picker">
                      <ActionButton onClick={() => void pickLigandFiles("raw")}>选择一个或多个 SDF / MOL</ActionButton>
                      <span>{ligandRawPaths.length ? `已选择 ${ligandRawPaths.length} 个；${ligandRawPaths.length > 1 ? "将自动准备并进入多配体模式" : "将进入单配体准备"}` : "尚未选择"}</span>
                    </div>
                  </div>
                </>
              ) : (
                <div className="assisted-online-note" role="note">
                  <strong>创建后直接进入“获取或导入原始结构”</strong>
                  <p>可随时搜索 RCSB / PubChem，也可改为从电脑导入文件；这个入口会一直保留在结构获取与转换工作区。</p>
                </div>
              )}
            </>
          )}

          <div className="button-row end">
            <ActionButton variant="primary" disabled={isBusy || !canCreate} onClick={() => void createProject()}>
              {isBusy
                ? "处理中..."
                : startMode === "assisted" && assistedSource === "online"
                  ? "创建项目并在线获取结构"
                  : currentConfig.primaryLabel}
            </ActionButton>
          </div>
        </div>
      </div>
    );
  };

  return (
    <PageShell className="project-create-workbench" labelledBy="project-create-title">
      <PageHero
        eyebrow="项目"
        title={currentConfig.title}
        titleId="project-create-title"
        description={currentConfig.subtitle}
        actions={
          <>
          <ActionButton variant="text" onClick={() => void pickAndLoadExistingProject()}>
            打开已有项目
          </ActionButton>
          <ActionButton variant="text" onClick={onBack}>返回总览</ActionButton>
          </>
        }
      />

      <BodyGrid>
        <MainPanel>
          <ModeTabs
            active={startMode}
            id="project-mode-tabs"
            label="选择开始方式"
            onChange={(mode) => {
              resetFeedback();
              onStartModeChange(mode);
            }}
            options={modeOptions}
          />
          {modeOptions.map((option, index) =>
            option.id === startMode ? null : (
              <div
                aria-labelledby={`project-mode-tabs-tab-${index}`}
                hidden
                id={option.controlsId}
                key={option.id}
                role="tabpanel"
              />
            ),
          )}
          <div
            aria-labelledby={`project-mode-tabs-tab-${activeModeIndex}`}
            className="main-panel-content"
            id={projectModePanelId(startMode)}
            role="tabpanel"
            tabIndex={0}
          >
          {showExistingProject ? (
            <section className="main-panel-section inline-secondary-section">
              <div className="main-panel-section-header">
                <h2>打开已有项目</h2>
              </div>
              <div className="form-panel compact-project-open-form">
                <div className="form-field" data-layout="form-row">
                  <label htmlFor="existing-project-dir">DockStart 项目目录</label>
                  <PathInput
                    id="existing-project-dir"
                    value={existingProjectDir}
                    onChange={setExistingProjectDir}
                    mode="directory"
                    title="选择已有 DockStart 项目目录"
                    placeholder="选择包含 project.json 的目录"
                  />
                </div>
                <div className="button-row end">
                  <ActionButton
                    disabled={isBusy || !existingProjectDir.trim()}
                    onClick={() => void loadExistingProject()}
                  >
                    {isBusy ? "加载中..." : "打开已有项目"}
                  </ActionButton>
                </div>
              </div>
            </section>
          ) : null}

          {renderModeForm()}

          {message ? <p className="message-line">{message}</p> : null}
          {rawError ? (
            <AdvancedDetails>
              <pre>{rawError}</pre>
            </AdvancedDetails>
          ) : null}
          </div>
        </MainPanel>

        <RightRail>
          <RightRailSection title="当前路径">
            <dl className="mode-context-list">
              <div>
                <dt>当前路径</dt>
                <dd>
                  {startMode === "assisted"
                    ? assistedSource === "online" ? "在线获取 → PDBQT" : "本地文件 → PDBQT"
                    : currentConfig.currentPath}
                </dd>
              </div>
              <div>
                <dt>下一步</dt>
                <dd>
                  {startMode === "assisted" && assistedSource === "online"
                    ? "搜索或导入原始结构"
                    : currentConfig.nextStep}
                </dd>
              </div>
              <div>
                <dt>需要</dt>
                <dd>{currentConfig.requirement}</dd>
              </div>
            </dl>
          </RightRailSection>

          {startMode === "demo" ? (
            <RightRailSection title="复制到">
              <div className="compact-project-open-form demo-destination-form">
                <div className="form-field" data-layout="form-row">
                  <label htmlFor="demo-base-dir">工作区目录</label>
                  <PathInput
                    id="demo-base-dir"
                    value={baseDir}
                    onChange={setBaseDir}
                    mode="directory"
                    title="选择示例保存目录"
                    placeholder="选择保存示例的父目录"
                  />
                </div>
              </div>
            </RightRailSection>
          ) : null}

          {startMode === "demo" ? (
            <RightRailSection title="复制规则">
              <p>复制为新项目；若目录已存在，自动生成不冲突名称。</p>
            </RightRailSection>
          ) : null}
        </RightRail>
      </BodyGrid>
    </PageShell>
  );
}
