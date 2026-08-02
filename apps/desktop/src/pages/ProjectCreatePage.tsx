import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { FileText, PencilSimple, Trash } from "@phosphor-icons/react";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import { BodyGrid, MainPanel, ModeTabs, PageHero, PageShell, RightRail, RightRailSection } from "../components/layout/PageLayout";
import OperationLoadingDialog from "../components/OperationLoadingDialog";
import PathInput from "../components/PathInput";
import type { PageId, ProjectTaskIntent, StartMode } from "../navigation/pages";
import type { DemoProjectSummary, DemoProjectsResponse, DockStartProject, ProjectResponse, SettingsResponse } from "../types";
import { writeDockingWorkspaceMode } from "../utils/dockingMode";
import { normalizeLigandImportPreview } from "../utils/screeningLigandImport";
import {
  splitLigandStructurePaths,
  structureInputKind,
} from "../utils/structureInput";
import {
  effectiveProjectTaskIntent,
  projectCreateProtocol,
  projectTaskOptions,
  taskIntentLabel,
} from "../utils/vinaTask";

type ProjectCreatePageProps = {
  backLabel?: string;
  openExistingRequestKey?: number;
  onOpenExistingRequestHandled?: () => void;
  startMode: StartMode;
  taskIntent: ProjectTaskIntent;
  onBack: () => void;
  onCreated: (project: DockStartProject, nextPage: PageId, runId?: string) => void;
  onStartModeChange: (mode: StartMode) => void;
  onTaskIntentChange: (intent: ProjectTaskIntent) => void;
};

type ModeConfig = {
  title: string;
  subtitle: string;
  primaryLabel: string;
  currentPath: string;
  nextStep: string;
  requirement: string;
};

type TaskCreateCopy = {
  title: string;
  subtitle: string;
  primaryLabel: string;
  currentPath: string;
  nextStep: string;
};

type BusyOperation = {
  title: string;
  message: string;
} | null;

function projectModePanelId(mode: StartMode) {
  return `project-mode-panel-${mode}`;
}

const modeOptions: Array<{ id: StartMode; label: string; controlsId: string }> = [
  { id: "assisted", label: "导入受体与配体结构", controlsId: projectModePanelId("assisted") },
  { id: "demo", label: "示例项目（快速体验）", controlsId: projectModePanelId("demo") },
];

const modeConfig: Record<StartMode, ModeConfig> = {
  basic: {
    title: "使用已有 PDBQT 开始运行",
    subtitle: "适合已经准备好受体与配体 PDBQT 的用户；导入后按本次任务继续配置。",
    primaryLabel: "创建项目并导入 PDBQT",
    currentPath: "已有 PDBQT",
    nextStep: "复核输入结构",
    requirement: "AutoDock Vina",
  },
  assisted: {
    title: "导入结构并准备 Vina 输入",
    subtitle: "受体与配体可以分别使用 PDBQT 或原始结构；需要转换的文件会在下一步准备。",
    primaryLabel: "创建项目并检查结构",
    currentPath: "结构导入 → 按格式准备",
    nextStep: "检查结构并转换需要处理的文件",
    requirement: "AutoDock Vina；原始结构转换需要 RDKit / Meeko",
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

function taskCreateCopy(startMode: StartMode, taskIntent: ProjectTaskIntent): TaskCreateCopy | null {
  if (startMode === "demo") return null;
  if (taskIntent === "score_only") {
    return {
      title: startMode === "basic"
        ? "使用已有 PDBQT 评价当前姿势"
        : "准备结构后评价当前姿势",
      subtitle: "受体与配体需要处在同一坐标系。Vina 将评价输入姿势，不执行构象或新位点搜索。",
      primaryLabel: startMode === "basic"
        ? "创建项目并导入待评分姿势"
        : "创建项目并准备待评分姿势",
      currentPath: startMode === "basic" ? "已有 PDBQT → 姿势评分" : "原始结构 → PDBQT → 姿势评分",
      nextStep: startMode === "basic" ? "复核坐标系并设置评分参数" : "获取结构并完成格式转换",
    };
  }
  if (taskIntent === "local_only") {
    return {
      title: startMode === "basic"
        ? "使用已有 PDBQT 局部优化当前姿势"
        : "准备结构后局部优化当前姿势",
      subtitle: "受体与配体需要处在同一坐标系。Vina 将从输入姿势附近优化，不执行全局位点搜索。",
      primaryLabel: startMode === "basic"
        ? "创建项目并导入待优化姿势"
        : "创建项目并准备待优化姿势",
      currentPath: startMode === "basic" ? "已有 PDBQT → 局部优化" : "原始结构 → PDBQT → 局部优化",
      nextStep: startMode === "basic" ? "复核坐标系并设置优化参数" : "获取结构并完成格式转换",
    };
  }
  return {
    title: modeConfig[startMode].title,
    subtitle: modeConfig[startMode].subtitle,
    primaryLabel: modeConfig[startMode].primaryLabel,
    currentPath: modeConfig[startMode].currentPath,
    nextStep: modeConfig[startMode].nextStep,
  };
}

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
  return "preparation";
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

function projectStagedFilePath(projectDir: string, stagedFile: string): string {
  if (/^(?:[A-Za-z]:[\\/]|\\\\)/.test(stagedFile)) return stagedFile;
  return `${projectDir.replace(/[\\/]+$/, "")}\\${stagedFile.replace(/^[\\/]+/, "").replace(/\//g, "\\")}`;
}

function noReadyLigandMessage(): string {
  return "导入结果中没有可用配体。重复、准备失败或需要正式大环审查的记录不会进入任务；项目已保留，可检查后重新导入。";
}

function fileNameFromPath(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  return normalized.split("/").filter(Boolean).pop() || path;
}

function projectFromResponse(response: ProjectResponse, fallbackMessage: string): DockStartProject {
  if (response.ok && response.project) {
    return response.project;
  }
  throw new Error(response.error?.message ?? fallbackMessage);
}

export default function ProjectCreatePage({
  backLabel = "返回",
  openExistingRequestKey = 0,
  onOpenExistingRequestHandled,
  startMode,
  taskIntent,
  onBack,
  onCreated,
  onStartModeChange,
  onTaskIntentChange,
}: ProjectCreatePageProps) {
  const [projectName, setProjectName] = useState("demo_project");
  const [baseDir, setBaseDir] = useState("");
  const [receptorPdbqtPath, setReceptorPdbqtPath] = useState("");
  const [ligandPdbqtPaths, setLigandPdbqtPaths] = useState<string[]>([]);
  const [receptorRawPath, setReceptorRawPath] = useState("");
  const [ligandRawPaths, setLigandRawPaths] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [busyOperation, setBusyOperation] = useState<BusyOperation>(null);
  const [demos, setDemos] = useState<DemoProjectsResponse["demos"]>([]);

  const effectiveStartMode: StartMode = startMode === "demo" ? "demo" : "assisted";
  const currentConfig = modeConfig[effectiveStartMode];
  const effectiveTaskIntent = effectiveProjectTaskIntent(startMode, taskIntent);
  const isEvaluationTask = effectiveTaskIntent !== "dock";
  const selectedTask = projectTaskOptions.find((option) => option.id === effectiveTaskIntent)
    ?? projectTaskOptions[0];
  const currentTaskCopy = taskCreateCopy(effectiveStartMode, effectiveTaskIntent);
  const activeModeIndex = Math.max(0, modeOptions.findIndex((option) => option.id === effectiveStartMode));
  const receptorStructurePath = receptorPdbqtPath || receptorRawPath;
  const ligandStructurePaths = [...ligandPdbqtPaths, ...ligandRawPaths];

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
    payload: Record<string, unknown>,
    fallbackMessage: string,
  ): Promise<DockStartProject> => {
    const rawPayload = await invoke<string>(command, payload);
    const response = parseProjectResponse(rawPayload);
    return projectFromResponse(response, fallbackMessage);
  };

  const createProject = useCallback(async (useOnlineSource = false) => {
    if (useOnlineSource && isEvaluationTask) {
      resetFeedback();
      setMessage("姿势评分与局部优化需要导入与受体处在同一坐标系中的本地结构，不能使用独立的在线 PubChem 构象。");
      return;
    }
    setIsBusy(true);
    setBusyOperation({
      title: "正在创建项目",
      message: "正在建立项目目录与基础记录。",
    });
    resetFeedback();
    let createdProjectDir = "";
    try {
      let project = await runProjectCommand(
        "create_project",
        { projectName, baseDir },
        "项目创建失败。",
      );
      createdProjectDir = project.project_dir;
      const taskProtocol = projectCreateProtocol(effectiveStartMode, taskIntent);
      project = await runProjectCommand(
        "update_vina_run_protocol",
        {
          projectDir: project.project_dir,
          runMode: taskProtocol.runMode,
          autobox: taskProtocol.autobox,
          confirmPoseContext: taskProtocol.confirmPoseContext,
        },
        "运行任务类型保存失败。",
      );
      if (taskProtocol.workspaceMode) {
        writeDockingWorkspaceMode(project.project_dir, taskProtocol.workspaceMode);
      }

      if (useOnlineSource) {
        onCreated(project, "structure-fetch");
        return;
      }

      const receptorKind = structureInputKind(receptorStructurePath, "receptor");
      if (receptorKind === "unsupported") {
        throw new Error("受体格式不受支持。请选择 PDBQT、PDB 或 CIF 文件。");
      }
      if (ligandStructurePaths.some((path) => structureInputKind(path, "ligand") === "unsupported")) {
        throw new Error("配体格式不受支持。请选择 PDBQT、SDF 或 MOL 文件。");
      }

      setBusyOperation({
        title: "正在导入结构",
        message: "正在按每个文件的实际格式写入项目。",
      });
      project = await runProjectCommand(
        receptorKind === "pdbqt" ? "import_receptor_pdbqt" : "import_receptor_raw_file",
        { projectDir: project.project_dir, sourcePath: receptorStructurePath },
        receptorKind === "pdbqt" ? "受体 PDBQT 导入失败。" : "受体原始结构导入失败。",
      );

      if (taskProtocol.runMode === "dock" && ligandStructurePaths.length > 1) {
          const stageResponse = JSON.parse(await invoke<string>("stage_screening_inputs", {
            projectDir: project.project_dir,
            files: ligandStructurePaths,
          })) as {
            ok?: boolean;
            error?: { message?: string; raw_error?: string };
            [key: string]: unknown;
          };
          if (!stageResponse.ok) {
            throw new Error(stageResponse.error?.raw_error || stageResponse.error?.message || "配体导入或准备失败。");
          }
          const preview = normalizeLigandImportPreview(stageResponse);
          const firstReady = preview.candidates.find(
            (candidate) => candidate.status === "ready" && candidate.stagedFile,
          );
          if (preview.counts.ready === 0 || !firstReady?.stagedFile) {
            throw new Error(noReadyLigandMessage());
          }
          project = await runProjectCommand(
            "import_ligand_pdbqt",
            {
              projectDir: project.project_dir,
              sourcePath: projectStagedFilePath(project.project_dir, firstReady.stagedFile),
            },
            "首个可用配体预览文件导入失败。",
          );
          writeDockingWorkspaceMode(
            project.project_dir,
            preview.counts.ready >= 2 ? "batch" : "single",
          );
      } else {
        const ligandPath = ligandStructurePaths[0] ?? "";
        const ligandKind = structureInputKind(ligandPath, "ligand");
        project = await runProjectCommand(
          ligandKind === "pdbqt" ? "import_ligand_pdbqt" : "import_ligand_raw_file",
          { projectDir: project.project_dir, sourcePath: ligandPath },
          ligandKind === "pdbqt" ? "配体 PDBQT 导入失败。" : "配体原始结构导入失败。",
        );
        writeDockingWorkspaceMode(project.project_dir, "single");
      }
      onCreated(project, "preparation");
      return;
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : "无法创建项目。";
      if (createdProjectDir) {
        setMessage(`${errorMessage} 项目目录已保留在 ${createdProjectDir}。可使用页头“打开已有项目”重新选择该目录。`);
      } else {
        setMessage(errorMessage);
      }
      setRawError(error instanceof Error ? error.stack ?? error.message : String(error));
    } finally {
      setBusyOperation(null);
      setIsBusy(false);
    }
  }, [
    baseDir,
    effectiveStartMode,
    ligandStructurePaths,
    onCreated,
    projectName,
    receptorStructurePath,
    isEvaluationTask,
    taskIntent,
  ]);

  const handledOpenRequestRef = useRef(0);

  const loadExistingProject = useCallback(async (projectDir: string) => {
    setIsBusy(true);
    setBusyOperation({
      title: "正在打开项目",
      message: "正在读取 project.json 与当前工作流状态。",
    });
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
      setBusyOperation(null);
      setIsBusy(false);
    }
  }, [onCreated]);

  const pickAndLoadExistingProject = useCallback(async () => {
    resetFeedback();
    try {
      const selected = await open({
        directory: true,
        multiple: false,
        title: "选择已有 DockStart 项目目录",
      });
      const projectDir = Array.isArray(selected) ? selected[0] ?? "" : selected ?? "";
      if (!projectDir) return;
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
    onOpenExistingRequestHandled?.();
    void pickAndLoadExistingProject();
  }, [onOpenExistingRequestHandled, openExistingRequestKey, pickAndLoadExistingProject]);

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

  const setUnifiedLigandPaths = useCallback((paths: string[]) => {
    const split = splitLigandStructurePaths(paths);
    setLigandPdbqtPaths(split.pdbqt);
    setLigandRawPaths(split.raw);
  }, []);

  const pickLigandFiles = useCallback(async (single = false) => {
    const selected = await open({
      directory: false,
      multiple: !single,
      title: single ? "选择一个配体结构" : "选择一个或多个配体结构",
      filters: [{ name: "配体结构", extensions: ["pdbqt", "sdf", "mol"] }],
    });
    const files = Array.isArray(selected) ? selected : selected ? [selected] : [];
    setUnifiedLigandPaths(files);
  }, [setUnifiedLigandPaths]);

  const replaceLigandFile = useCallback(async (index: number) => {
    const selected = await open({
      directory: false,
      multiple: false,
      title: "更改配体结构",
      filters: [{ name: "配体结构", extensions: ["pdbqt", "sdf", "mol"] }],
    });
    const replacement = Array.isArray(selected) ? selected[0] ?? "" : selected ?? "";
    if (!replacement) return;
    setUnifiedLigandPaths(ligandStructurePaths.map((path, pathIndex) => (
      pathIndex === index ? replacement : path
    )));
  }, [ligandStructurePaths, setUnifiedLigandPaths]);

  const removeLigandFile = useCallback((index: number) => {
    setUnifiedLigandPaths(ligandStructurePaths.filter((_, pathIndex) => pathIndex !== index));
  }, [ligandStructurePaths, setUnifiedLigandPaths]);

  const renderSelectedLigandFiles = (paths: string[]) => {
    if (!paths.length) {
      return <span className="ligand-selection-empty">尚未选择</span>;
    }
    return (
      <ul className="selected-ligand-files" aria-label="已选择的配体文件">
        {paths.map((path, index) => {
          const fileName = fileNameFromPath(path);
          return (
            <li className="selected-ligand-file" key={`${path}-${index}`}>
              <FileText className="selected-ligand-file-icon" aria-hidden="true" size={19} />
              <span className="selected-ligand-file-copy">
                <strong title={fileName}>{fileName}</strong>
                <code title={path}>{path}</code>
              </span>
              <span className="selected-ligand-file-actions">
                <ActionButton
                  aria-label={`更改配体文件 ${fileName}`}
                  onClick={() => void replaceLigandFile(index)}
                  variant="text"
                >
                  <PencilSimple aria-hidden="true" size={15} />
                  更改
                </ActionButton>
                <ActionButton
                  aria-label={`删除配体文件 ${fileName}`}
                  className="selected-ligand-file-remove"
                  onClick={() => removeLigandFile(index)}
                  variant="text"
                >
                  <Trash aria-hidden="true" size={15} />
                  删除
                </ActionButton>
              </span>
            </li>
          );
        })}
      </ul>
    );
  };

  const createDemo = useCallback(
    async (demo: DemoProjectSummary) => {
      setIsBusy(true);
      resetFeedback();
      try {
        const destinationDir = await pickDemoDestinationDir();
        if (!destinationDir) {
          return;
        }
        setBusyOperation({
          title: "正在复制示例",
          message: "正在创建独立的示例项目副本。",
        });
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
        setBusyOperation(null);
        setIsBusy(false);
      }
    },
    [onCreated, pickDemoDestinationDir],
  );

  const canCreate = Boolean(
    projectName.trim()
      && baseDir.trim()
      && receptorStructurePath.trim()
      && ligandStructurePaths.length
      && (!isEvaluationTask || ligandStructurePaths.length === 1),
  );

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
            {isEvaluationTask
              ? "选择项目保存位置，并提供处在同一受体坐标系中的受体与配体结构。"
              : "受体与配体可以分别选择 PDBQT 或原始结构；DockStart 会按各自格式继续处理。"}
          </p>
        </div>
        <div className="form-panel create-mode-form">
          <div className="form-field" data-layout="form-row">
            <label htmlFor="project-name">项目名称</label>
            <input
              autoComplete="off"
              id="project-name"
              name="dockstart-project-name"
              spellCheck={false}
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

          <div className="form-field" data-layout="form-row">
            <label htmlFor="receptor-structure">受体结构</label>
            <PathInput
              id="receptor-structure"
              value={receptorStructurePath}
              onChange={(path) => {
                const kind = structureInputKind(path, "receptor");
                setReceptorPdbqtPath(kind === "pdbqt" ? path : "");
                setReceptorRawPath(kind === "raw" ? path : "");
              }}
              mode="file"
              title="选择受体 PDBQT / PDB / CIF"
              placeholder="选择 PDBQT、PDB 或 CIF"
              filters={[{ name: "受体结构", extensions: ["pdbqt", "pdb", "cif"] }]}
            />
            <small className="form-field-hint">PDBQT 直接使用；PDB/CIF 会在下一步提供转换。</small>
          </div>

          <div className="form-field" data-layout="form-row">
            <label>配体结构</label>
            <div className="multi-ligand-file-picker">
              <ActionButton onClick={() => void pickLigandFiles(isEvaluationTask)}>
                {isEvaluationTask ? "选择一个配体结构" : "选择一个或多个配体结构"}
              </ActionButton>
              {renderSelectedLigandFiles(ligandStructurePaths)}
              {ligandStructurePaths.length ? (
                <span className="ligand-selection-summary">
                  {isEvaluationTask
                    ? ligandStructurePaths.length === 1
                      ? "已选择 1 个待评价结构"
                      : "姿势评分与局部优化一次只能使用一个配体"
                    : `已选择 ${ligandStructurePaths.length} 个文件；PDBQT 直接使用，SDF/MOL 按需转换`}
                </span>
              ) : null}
            </div>
          </div>

          {isEvaluationTask ? (
            <div className="pose-context-note" role="note">
              <strong>坐标系要求</strong>
              <p>配体应已位于当前受体中的待评价位置；{selectedTask.label}不会搜索新的结合位点。原始结构转换后仍需复核坐标。</p>
            </div>
          ) : null}

          <div className="button-row end">
            {!isEvaluationTask ? (
              <ActionButton disabled={isBusy || !projectName.trim() || !baseDir.trim()} onClick={() => void createProject(true)}>
                在线搜索结构
              </ActionButton>
            ) : null}
            <ActionButton variant="primary" disabled={isBusy || !canCreate} onClick={() => void createProject()}>
              {isBusy ? "处理中..." : currentTaskCopy?.primaryLabel ?? currentConfig.primaryLabel}
            </ActionButton>
          </div>
        </div>
      </div>
    );
  };

  return (
    <PageShell className="project-create-workbench" labelledBy="project-create-title">
      <OperationLoadingDialog
        open={Boolean(busyOperation)}
        title={busyOperation?.title ?? ""}
        message={busyOperation?.message ?? ""}
        detail="完成前请保持 DockStart 打开。"
      />
      <PageHero
        eyebrow="项目"
        title={currentTaskCopy?.title ?? currentConfig.title}
        titleId="project-create-title"
        description={currentTaskCopy?.subtitle ?? currentConfig.subtitle}
        actions={
          <>
          <ActionButton variant="text" onClick={() => void pickAndLoadExistingProject()}>
            打开已有项目
          </ActionButton>
          <ActionButton variant="text" onClick={onBack}>{backLabel}</ActionButton>
          </>
        }
      />

      <BodyGrid>
        <MainPanel>
          {startMode !== "demo" ? (
            <section className="project-task-intent-picker" aria-labelledby="project-task-intent-title">
              <header className="project-task-intent-heading">
                <h2 id="project-task-intent-title">本次任务</h2>
                <p>先判断是否已有可信的配体姿势，再选择 Vina 要执行的计算。</p>
              </header>
              <div className="project-task-intent-options" role="radiogroup" aria-labelledby="project-task-intent-title">
                {projectTaskOptions.map((option) => {
                  const descriptionId = `project-task-${option.id}-description`;
                  return (
                    <label
                      className={effectiveTaskIntent === option.id ? "selected" : ""}
                      key={option.id}
                    >
                      <input
                        aria-describedby={descriptionId}
                        checked={effectiveTaskIntent === option.id}
                        name="project-task-intent"
                        onChange={() => {
                          resetFeedback();
                          onTaskIntentChange(option.id);
                        }}
                        type="radio"
                        value={option.id}
                      />
                      <span>
                        <strong>{option.label}</strong>
                        <small id={descriptionId}>{option.description}</small>
                      </span>
                    </label>
                  );
                })}
              </div>
            </section>
          ) : null}
          <section className="project-source-picker" aria-labelledby="project-source-picker-title">
            <div className="project-source-picker-heading">
              <strong id="project-source-picker-title">输入来源</strong>
            </div>
          <ModeTabs
            active={effectiveStartMode}
            id="project-mode-tabs"
            label="选择开始方式"
            onChange={(mode) => {
              resetFeedback();
              onStartModeChange(mode);
            }}
            options={modeOptions}
          />
          </section>
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
            id={projectModePanelId(effectiveStartMode)}
            role="tabpanel"
            tabIndex={0}
          >
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
                  {effectiveStartMode === "assisted"
                    ? `结构文件 → 按格式准备${isEvaluationTask ? ` → ${selectedTask.label}` : ""}`
                    : currentTaskCopy?.currentPath ?? currentConfig.currentPath}
                </dd>
              </div>
              <div>
                <dt>下一步</dt>
                <dd>
                  {currentTaskCopy?.nextStep ?? currentConfig.nextStep}
                </dd>
              </div>
              {startMode !== "demo" ? (
                <div>
                  <dt>本次任务</dt>
                  <dd>{taskIntentLabel(effectiveTaskIntent)}</dd>
                </div>
              ) : null}
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
