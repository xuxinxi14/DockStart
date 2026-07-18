import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  ArrowRight,
  CaretDown,
  CheckCircle,
  Clock,
  Cpu,
  Database,
  FloppyDisk,
  FolderOpen,
  HardDrives,
  Play,
  SpinnerGap,
  Stop,
  WarningCircle,
  XCircle,
} from "@phosphor-icons/react";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import RunBoxInspector, {
  type RunAxisSpacing,
  type RunBoxFieldKey,
  type RunBoxLineThickness,
  type RunBoxWheelStep,
} from "../components/RunBoxInspector";
import StatusBadge from "../components/StatusBadge";
import WarningCallout from "../components/WarningCallout";
import type { PageId } from "../navigation/pages";
import type {
  DockStartProject,
  ProjectResponse,
  RunPreflightCheck,
  RunPreflightResponse,
  RunRuntimeStatusResponse,
} from "../types";
import {
  cancelQueuedBackgroundTask,
  findActiveBackgroundTask,
  startVinaRunTask,
  waitForBackgroundTask,
  type BackgroundTaskStatus,
} from "../utils/backgroundTasks";

const RunStructurePreview = lazy(() => import("../components/RunStructurePreview"));

type RunPreparePageProps = {
  project: DockStartProject;
  onBack: () => void;
  onProjectChange: (project: DockStartProject) => void;
  onOpenRunExecute: (project: DockStartProject, runId: string) => void;
  onOpenResultPage: (project: DockStartProject, runId: string) => void;
  onNavigate: (page: PageId) => void;
};

type RunActionMode = "full" | "prepare" | "config";
type BoxForm = Record<keyof DockStartProject["box"], string>;
type VinaForm = Record<keyof DockStartProject["vina"], string>;
type VinaNumericKey = Exclude<keyof DockStartProject["vina"], "scoring">;
const minBoxDimension = 0.1;

const runActionLabels: Record<RunActionMode, string> = {
  full: "开始对接",
  prepare: "创建运行记录",
  config: "生成配置",
};

const runActionDescriptions: Record<RunActionMode, string> = {
  full: "完整流程：运行、解析、报告",
  prepare: "仅创建可复现运行记录",
  config: "仅保存并生成配置",
};

const vinaFields: Array<{ key: VinaNumericKey; label: string; hint: string }> = [
  { key: "exhaustiveness", label: "搜索彻底程度", hint: "建议从 8 开始" },
  { key: "num_modes", label: "输出构象数量", hint: "建议 9" },
  { key: "energy_range", label: "能量范围", hint: "kcal/mol" },
  { key: "cpu", label: "CPU 线程", hint: "0 为 Vina 自动" },
  { key: "seed", label: "随机种子", hint: "留空则不写入配置" },
];

const stageLabels: Record<string, string> = {
  idle: "等待开始",
  saving: "保存设置",
  configuring: "生成配置",
  preparing: "创建运行记录",
  starting: "启动 Vina",
  running: "AutoDock Vina 正在搜索构象",
  cancelling: "正在终止运行",
  cancel_pending: "等待安全取消",
  cancelled: "已取消",
  analyzing: "解析评分",
  reporting: "生成结果分析报告",
  finished: "完整流程已完成",
  failed: "运行失败",
  interrupted: "运行已中断",
};

function monotonicRunStage(current: string, observed: string): string {
  const next = observed === "cancel_pending" ? "cancelling" : observed;
  if ((current === "cancelling" || current === "cancel_pending") && (next === "starting" || next === "running")) {
    return "cancelling";
  }
  return next || current;
}

function parseProjectResponse(rawPayload: string): ProjectResponse {
  return JSON.parse(rawPayload) as ProjectResponse;
}

function parsePreflight(rawPayload: string): RunPreflightResponse {
  return JSON.parse(rawPayload) as RunPreflightResponse;
}

function parseRuntime(rawPayload: string): RunRuntimeStatusResponse {
  return JSON.parse(rawPayload) as RunRuntimeStatusResponse;
}

function boxToForm(project: DockStartProject): BoxForm {
  return {
    center_x: String(project.box.center_x),
    center_y: String(project.box.center_y),
    center_z: String(project.box.center_z),
    size_x: String(project.box.size_x),
    size_y: String(project.box.size_y),
    size_z: String(project.box.size_z),
  };
}

function vinaToForm(project: DockStartProject): VinaForm {
  return {
    scoring: project.vina.scoring ?? "vina",
    exhaustiveness: String(project.vina.exhaustiveness),
    num_modes: String(project.vina.num_modes),
    energy_range: String(project.vina.energy_range),
    cpu: String(project.vina.cpu),
    seed: project.vina.seed === null ? "" : String(project.vina.seed),
  };
}

function projectFromResponse(response: ProjectResponse, fallback: DockStartProject): DockStartProject {
  return response.project ?? fallback;
}

function parseBoxForm(form: BoxForm): DockStartProject["box"] | null {
  const parsed = Object.fromEntries(Object.entries(form).map(([key, value]) => [key, Number(value)])) as DockStartProject["box"];
  if (!Object.values(parsed).every(Number.isFinite)) return null;
  if (parsed.size_x <= 0 || parsed.size_y <= 0 || parsed.size_z <= 0) return null;
  return parsed;
}

function boxFormsEqual(left: BoxForm, right: BoxForm): boolean {
  return (Object.keys(left) as Array<keyof BoxForm>).every((key) => Number(left[key]) === Number(right[key]));
}

function vinaFormsEqual(left: VinaForm, right: VinaForm): boolean {
  return (Object.keys(left) as Array<keyof VinaForm>).every((key) => {
    if (key === "scoring") return left[key] === right[key];
    if (key === "seed" && left[key].trim() === "" && right[key].trim() === "") return true;
    return Number(left[key]) === Number(right[key]);
  });
}

function boxCoordinate(value: number): string {
  return String(Number(value.toFixed(3)));
}

function parseVinaForm(form: VinaForm): DockStartProject["vina"] | null {
  const scoring = form.scoring.trim().toLowerCase();
  const exhaustiveness = Number(form.exhaustiveness);
  const num_modes = Number(form.num_modes);
  const energy_range = Number(form.energy_range);
  const cpu = Number(form.cpu);
  const seed = form.seed.trim() ? Number(form.seed) : null;
  if (scoring !== "vina" && scoring !== "vinardo") return null;
  if (!Number.isInteger(exhaustiveness) || exhaustiveness <= 0) return null;
  if (!Number.isInteger(num_modes) || num_modes <= 0) return null;
  if (!Number.isFinite(energy_range) || energy_range <= 0) return null;
  if (!Number.isInteger(cpu) || cpu < 0) return null;
  if (seed !== null && !Number.isInteger(seed)) return null;
  return { scoring, exhaustiveness, num_modes, energy_range, cpu, seed };
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "未知";
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

function formatTime(value: string): string {
  if (!value) return "未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "—";
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} 秒`;
}

function statusTone(status: string): "ok" | "warning" | "error" | "muted" | "info" {
  if (status === "ok" || status === "finished" || status === "prepared") return "ok";
  if (status === "warning" || status === "cancelled") return "warning";
  if (status === "error" || status === "missing" || status === "failed" || status === "interrupted") return "error";
  if (status === "running" || status === "cancelling") return "info";
  return "muted";
}

function checkIcon(check: RunPreflightCheck) {
  if (check.status === "ok") return <CheckCircle aria-hidden="true" size={20} weight="fill" />;
  if (check.status === "warning") return <WarningCircle aria-hidden="true" size={20} weight="fill" />;
  return <XCircle aria-hidden="true" size={20} weight="fill" />;
}

function safeRepairPage(check: RunPreflightCheck): PageId | null {
  const page = check.action_page;
  const supported = new Set<PageId>(["import-pdbqt", "box-setup", "vina-param", "vina-config", "settings", "toolchain-status"]);
  if (page && supported.has(page as PageId)) return page as PageId;
  if (check.key === "receptor" || check.key === "ligand") return "import-pdbqt";
  if (check.key === "box") return "box-setup";
  if (check.key === "vina_params" || check.key === "cpu") return "vina-param";
  if (check.key === "vina") return "settings";
  return null;
}

export default function RunPreparePage({
  project: initialProject,
  onBack,
  onProjectChange,
  onOpenRunExecute,
  onOpenResultPage,
  onNavigate,
}: RunPreparePageProps) {
  const [project, setProject] = useState(initialProject);
  const [boxForm, setBoxForm] = useState<BoxForm>(() => boxToForm(initialProject));
  const [vinaForm, setVinaForm] = useState<VinaForm>(() => vinaToForm(initialProject));
  const [preflight, setPreflight] = useState<RunPreflightResponse | null>(null);
  const [runtime, setRuntime] = useState<RunRuntimeStatusResponse | null>(null);
  const [actionMode, setActionMode] = useState<RunActionMode>("full");
  const [stage, setStage] = useState("idle");
  const [activeRunId, setActiveRunId] = useState("");
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isBusy, setIsBusy] = useState(false);
  const [isDirty, setIsDirty] = useState(false);
  const [activeBackgroundTask, setActiveBackgroundTask] = useState<BackgroundTaskStatus | null>(null);
  const [boxWheelBinding, setBoxWheelBinding] = useState<RunBoxFieldKey | null>(null);
  const [boxWheelStep, setBoxWheelStep] = useState<RunBoxWheelStep>(0.1);
  const [boxLineThickness, setBoxLineThickness] = useState<RunBoxLineThickness>("standard");
  const [axisSpacing, setAxisSpacing] = useState<RunAxisSpacing>("standard");
  const [boxPlacementMessage, setBoxPlacementMessage] = useState("");
  const [previewFitRequestKey, setPreviewFitRequestKey] = useState(0);
  const mountedRef = useRef(true);
  const dirtyRef = useRef(false);
  const preflightRequestRef = useRef(0);
  const activeTaskAbortRef = useRef<AbortController | null>(null);
  const initialBoxSnapshotRef = useRef({
    projectDir: initialProject.project_dir,
    form: boxToForm(initialProject),
  });

  const parsedBox = useMemo(() => parseBoxForm(boxForm), [boxForm]);
  const parsedVina = useMemo(() => parseVinaForm(vinaForm), [vinaForm]);
  const formIsValid = Boolean(parsedBox && parsedVina);
  const displayBox = parsedBox ?? project.box;
  const volume = displayBox.size_x * displayBox.size_y * displayBox.size_z;
  const running = stage === "starting" || stage === "running" || stage === "cancelling" || stage === "cancel_pending";
  const progress = runtime?.progress?.percent ?? (stage === "finished" ? 100 : 0);
  const receptorCenter = preflight?.input_stats?.receptor?.coordinate_center ?? null;
  const canResetBox = !boxFormsEqual(boxForm, initialBoxSnapshotRef.current.form);

  const commitProject = useCallback((nextProject: DockStartProject, syncForms = false) => {
    if (!mountedRef.current) return;
    setProject(nextProject);
    onProjectChange(nextProject);
    if (syncForms) {
      setBoxForm(boxToForm(nextProject));
      setVinaForm(vinaToForm(nextProject));
      setIsDirty(false);
      dirtyRef.current = false;
    }
  }, [onProjectChange]);

  const refreshPreflight = useCallback(async (syncForms = false): Promise<RunPreflightResponse | null> => {
    if (!mountedRef.current) return null;
    const requestId = ++preflightRequestRef.current;
    setIsRefreshing(true);
    try {
      const rawPayload = await invoke<string>("get_run_preflight", { projectDir: initialProject.project_dir });
      if (!mountedRef.current || requestId !== preflightRequestRef.current) return null;
      const parsed = parsePreflight(rawPayload);
      setPreflight(parsed);
      if (parsed.project) commitProject(parsed.project, syncForms && !dirtyRef.current);
      setMessage(parsed.message || (parsed.ready ? "运行前检查通过。" : "请先处理阻塞项。"));
      setRawError(parsed.error?.raw_error ?? "");
      const guardedRun = parsed.active_run_guard?.active_runs?.[0];
      if (parsed.active_run_guard?.blocked && guardedRun?.run_id) {
        setActiveRunId(guardedRun.run_id);
        setStage((current) => monotonicRunStage(current, guardedRun.stage || "running"));
        try {
          const runtimePayload = await invoke<string>("get_run_runtime_status", {
            projectDir: initialProject.project_dir,
            runId: guardedRun.run_id,
          });
          if (mountedRef.current && requestId === preflightRequestRef.current) {
            const recoveredRuntime = parseRuntime(runtimePayload);
            setRuntime(recoveredRuntime);
            setStage((current) => monotonicRunStage(current, recoveredRuntime.stage || guardedRun.stage));
            if (recoveredRuntime.project) commitProject(recoveredRuntime.project);
          }
        } catch (error) {
          if (mountedRef.current && requestId === preflightRequestRef.current) {
            setRawError(error instanceof Error ? error.message : String(error));
          }
        }
      }
      return parsed;
    } catch (error) {
      if (!mountedRef.current || requestId !== preflightRequestRef.current) return null;
      setPreflight(null);
      setMessage("无法完成聚合运行前检查。");
      setRawError(error instanceof Error ? error.message : String(error));
      return null;
    } finally {
      if (mountedRef.current && requestId === preflightRequestRef.current) setIsRefreshing(false);
    }
  }, [commitProject, initialProject.project_dir]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      preflightRequestRef.current += 1;
      activeTaskAbortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (initialBoxSnapshotRef.current.projectDir === initialProject.project_dir) return;
    initialBoxSnapshotRef.current = {
      projectDir: initialProject.project_dir,
      form: boxToForm(initialProject),
    };
    setBoxPlacementMessage("");
    setPreviewFitRequestKey(0);
  }, [initialProject]);

  useEffect(() => {
    void refreshPreflight(true);
  }, [refreshPreflight]);

  const waitForVinaBackgroundTask = useCallback(
    async (
      startedTask: BackgroundTaskStatus,
      projectDir: string,
      runId: string,
      controller: AbortController,
    ) => {
      setActiveBackgroundTask(startedTask);
      return waitForBackgroundTask(
        startedTask.task_id,
        (task) => {
          if (!mountedRef.current) return;
          setActiveBackgroundTask(task);
          const taskStage = task.stage === "queued" ? "starting" : task.stage;
          setStage((current) => monotonicRunStage(current, taskStage));
          setMessage(task.progress.message || task.message);
          setRuntime((current) => ({
            ok: task.status !== "failed",
            project_dir: projectDir,
            project: null,
            run_id: runId,
            metadata: current?.metadata ?? null,
            progress: task.progress,
            stage: taskStage,
            elapsed_seconds: task.elapsed_seconds,
            stdout_tail: task.stdout_tail || current?.stdout_tail || "",
            stderr_tail: task.stderr_tail || current?.stderr_tail || "",
            log_tail: task.log_tail || current?.log_tail || "",
            message: task.message,
            error: task.error
              ? { code: "BACKGROUND_TASK_ERROR", message: task.message, raw_error: task.error, suggestion: "请查看运行日志。" }
              : null,
          }));
        },
        controller.signal,
      );
    },
    [],
  );

  useEffect(() => {
    const controller = new AbortController();
    let disposed = false;
    let resumedTaskId = "";
    const reconnect = async () => {
      try {
        const existing = await findActiveBackgroundTask(initialProject.project_dir, { kind: "vina" });
        if (!existing || disposed || !existing.run_id) return;
        resumedTaskId = existing.task_id;
        activeTaskAbortRef.current?.abort();
        activeTaskAbortRef.current = controller;
        setActiveRunId(existing.run_id);
        setStage(existing.status === "queued" ? "starting" : "running");
        setIsBusy(true);
        setMessage(`${existing.run_id} 仍在后台执行，已恢复进度显示。`);
        const completed = await waitForVinaBackgroundTask(
          existing,
          initialProject.project_dir,
          existing.run_id,
          controller,
        );
        if (disposed) return;
        setActiveBackgroundTask(completed);
        if (completed.status === "cancelled") {
          setStage("cancelled");
          setMessage(`${existing.run_id} 已取消，现有日志已保留。`);
          return;
        }
        if (completed.status === "failed") {
          setStage("failed");
          setMessage(completed.message || `${existing.run_id} 运行失败。`);
          setRawError(completed.error);
          return;
        }
        const finalPayload = await invoke<string>("get_run_runtime_status", {
          projectDir: initialProject.project_dir,
          runId: existing.run_id,
        });
        const finalRuntime = parseRuntime(finalPayload);
        setRuntime(finalRuntime);
        if (finalRuntime.project) commitProject(finalRuntime.project);
        setStage(finalRuntime.stage || "finished");
        setMessage(finalRuntime.message || `${existing.run_id} 后台运行已结束，可打开运行详情继续处理结果。`);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        if (!disposed) {
          setStage("failed");
          setMessage("无法恢复后台 Vina 任务状态。");
          setRawError(error instanceof Error ? error.message : String(error));
        }
      } finally {
        if (!disposed) {
          setActiveBackgroundTask((current) => (current?.task_id === resumedTaskId ? null : current));
          setIsBusy(false);
        }
        if (activeTaskAbortRef.current === controller) activeTaskAbortRef.current = null;
      }
    };
    void reconnect();
    return () => {
      disposed = true;
      controller.abort();
    };
  }, [commitProject, initialProject.project_dir, waitForVinaBackgroundTask]);

  const updateBoxField = (key: keyof BoxForm, value: string) => {
    setBoxForm((current) => ({ ...current, [key]: value }));
    setBoxPlacementMessage("");
    setIsDirty(true);
    dirtyRef.current = true;
  };

  const adjustBoundBoxField = useCallback((direction: 1 | -1) => {
    if (!boxWheelBinding || isBusy) return;
    setBoxForm((current) => {
      const currentValue = Number(current[boxWheelBinding]);
      if (!Number.isFinite(currentValue)) return current;
      const rawNext = currentValue + direction * boxWheelStep;
      const next = boxWheelBinding.startsWith("size_")
        ? Math.max(minBoxDimension, rawNext)
        : rawNext;
      return {
        ...current,
        [boxWheelBinding]: String(Number(next.toFixed(3))),
      };
    });
    setBoxPlacementMessage("");
    setIsDirty(true);
    dirtyRef.current = true;
  }, [boxWheelBinding, boxWheelStep, isBusy]);

  const centerBoxOnReceptor = useCallback(() => {
    if (!receptorCenter || isBusy) return;
    const nextForm = {
      ...boxForm,
      center_x: boxCoordinate(receptorCenter.x),
      center_y: boxCoordinate(receptorCenter.y),
      center_z: boxCoordinate(receptorCenter.z),
    };
    setBoxForm(nextForm);
    setBoxWheelBinding(null);
    setBoxPlacementMessage(
      `已移动到受体坐标范围中心：${boxCoordinate(receptorCenter.x)}, ${boxCoordinate(receptorCenter.y)}, ${boxCoordinate(receptorCenter.z)} Å。`,
    );
    setPreviewFitRequestKey((current) => current + 1);
    const nextDirty = !boxFormsEqual(nextForm, boxToForm(project)) || !vinaFormsEqual(vinaForm, vinaToForm(project));
    setIsDirty(nextDirty);
    dirtyRef.current = nextDirty;
  }, [boxForm, isBusy, project, receptorCenter, vinaForm]);

  const resetBoxToInitial = useCallback(() => {
    if (isBusy) return;
    const nextForm = { ...initialBoxSnapshotRef.current.form };
    setBoxForm(nextForm);
    setBoxWheelBinding(null);
    setBoxPlacementMessage("已恢复进入对接工作台时的 Box 参数。");
    setPreviewFitRequestKey((current) => current + 1);
    const nextDirty = !boxFormsEqual(nextForm, boxToForm(project)) || !vinaFormsEqual(vinaForm, vinaToForm(project));
    setIsDirty(nextDirty);
    dirtyRef.current = nextDirty;
  }, [isBusy, project, vinaForm]);

  const updateVinaField = (key: keyof VinaForm, value: string) => {
    setVinaForm((current) => ({ ...current, [key]: value }));
    setIsDirty(true);
    dirtyRef.current = true;
  };

  const saveSettings = useCallback(async (announce = true): Promise<DockStartProject> => {
    const nextBox = parseBoxForm(boxForm);
    const nextVina = parseVinaForm(vinaForm);
    if (!nextBox || !nextVina) throw new Error("Box 或 Vina 参数格式无效，请检查高亮字段。");
    setStage("saving");
    const boxPayload = await invoke<string>("update_box_params", {
      projectDir: project.project_dir,
      boxJson: JSON.stringify(nextBox),
    });
    const boxResponse = parseProjectResponse(boxPayload);
    if (!boxResponse.ok || !boxResponse.project) throw new Error(boxResponse.error?.message ?? "搜索范围保存失败。");
    const vinaPayload = await invoke<string>("update_vina_params", {
      projectDir: project.project_dir,
      vinaJson: JSON.stringify(nextVina),
    });
    const vinaResponse = parseProjectResponse(vinaPayload);
    if (!vinaResponse.ok || !vinaResponse.project) throw new Error(vinaResponse.error?.message ?? "Vina 参数保存失败。");
    const nextProject = projectFromResponse(vinaResponse, boxResponse.project);
    commitProject(nextProject, true);
    if (announce) setMessage("搜索范围与 Vina 参数已保存。正在重新检查…");
    return nextProject;
  }, [boxForm, commitProject, project.project_dir, vinaForm]);

  const runWorkflow = async () => {
    if (!preflight?.ready || !formIsValid || isBusy) return;
    setIsBusy(true);
    setRawError("");
    setRuntime(null);
    try {
      const savedProject = await saveSettings(false);
      setMessage("参数已保存，正在重新执行运行前检查…");
      const verifiedPreflight = await refreshPreflight(true);
      if (!verifiedPreflight?.ready) {
        throw new Error(verifiedPreflight?.message || "保存后的运行前检查未通过，请先处理阻塞项。");
      }
      setStage("configuring");
      setMessage("正在生成可复现的 vina_config.txt…");
      const configPayload = await invoke<string>("generate_vina_config", { projectDir: savedProject.project_dir });
      const configResponse = parseProjectResponse(configPayload);
      if (!configResponse.ok) throw new Error(configResponse.error?.message ?? "Vina 配置生成失败。");
      const configuredProject = projectFromResponse(configResponse, savedProject);
      commitProject(configuredProject, true);
      if (actionMode === "config") {
        setStage("idle");
        setMessage("Vina 配置已生成，尚未创建或执行 run。");
        await refreshPreflight(true);
        return;
      }

      setStage("preparing");
      setMessage("正在创建运行记录、输入哈希与配置快照…");
      const preparePayload = await invoke<string>("prepare_vina_run", { projectDir: configuredProject.project_dir });
      const prepareResponse = parseProjectResponse(preparePayload);
      if (!prepareResponse.ok || !prepareResponse.run_id) throw new Error(prepareResponse.error?.message ?? "运行记录创建失败。");
      const preparedProject = projectFromResponse(prepareResponse, configuredProject);
      commitProject(preparedProject);
      setActiveRunId(prepareResponse.run_id);
      if (actionMode === "prepare") {
        setStage("idle");
        setMessage(`${prepareResponse.run_id} 已准备，可进入单独执行页复核命令。`);
        await refreshPreflight();
        return;
      }

      const runId = prepareResponse.run_id;
      setStage("starting");
      setMessage(`${runId} 正在启动 AutoDock Vina…`);
      activeTaskAbortRef.current?.abort();
      const taskController = new AbortController();
      activeTaskAbortRef.current = taskController;
      const startedTask = await startVinaRunTask(preparedProject.project_dir, runId);
      setActiveBackgroundTask(startedTask);
      if (startedTask.deduplicated) setMessage(`${runId} 已在后台运行，正在重新接收进度事件。`);
      const completedTask = await waitForVinaBackgroundTask(
        startedTask,
        preparedProject.project_dir,
        runId,
        taskController,
      );
      setActiveBackgroundTask(completedTask);
      if (activeTaskAbortRef.current === taskController) activeTaskAbortRef.current = null;
      if (completedTask.status === "cancelled") {
        setStage("cancelled");
        setMessage(`${runId} 已安全取消，已保留取消前日志。`);
        return;
      }
      if (!completedTask.result_json) {
        throw new Error(completedTask.error || completedTask.message || "Vina 后台任务没有返回执行结果。");
      }
      const executePayload = completedTask.result_json;
      const executeResponse = parseProjectResponse(executePayload);
      if (mountedRef.current) {
        const finalRuntimePayload = await invoke<string>("get_run_runtime_status", {
          projectDir: preparedProject.project_dir,
          runId,
        });
        setRuntime(parseRuntime(finalRuntimePayload));
      }
      const executedProject = projectFromResponse(executeResponse, preparedProject);
      if (executeResponse.project) commitProject(executeResponse.project);
      const runStatus = String(executeResponse.metadata?.status ?? "");
      if (!executeResponse.ok || runStatus !== "finished") {
        if (runStatus === "cancelled") {
          setStage("cancelled");
          setMessage(`${runId} 已安全取消，已保留取消前日志。`);
          return;
        }
        setStage("failed");
        throw new Error(executeResponse.error?.message ?? String(executeResponse.metadata?.error_message ?? "AutoDock Vina 执行失败。"));
      }

      setStage("analyzing");
      setMessage("对接完成，正在解析评分并生成 scores.csv…");
      const analyzePayload = await invoke<string>("analyze_vina_run_results", {
        projectDir: executedProject.project_dir,
        runId,
      });
      const analyzeResponse = parseProjectResponse(analyzePayload);
      if (!analyzeResponse.ok) throw new Error(analyzeResponse.error?.message ?? "评分解析失败。");
      const analyzedProject = projectFromResponse(analyzeResponse, executedProject);
      commitProject(analyzedProject);

      setStage("reporting");
      setMessage("正在导出 Markdown 实验记录…");
      const reportPayload = await invoke<string>("export_markdown_report", {
        projectDir: analyzedProject.project_dir,
        runId,
      });
      const reportResponse = parseProjectResponse(reportPayload);
      if (!reportResponse.ok) throw new Error(reportResponse.error?.message ?? "实验记录导出失败。");
      if (reportResponse.project) commitProject(reportResponse.project);
      setStage("finished");
      setMessage(`${runId} 已完成：Vina 执行、评分解析和实验记录导出全部成功。`);
      await refreshPreflight();
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (stage !== "cancelled") setStage("failed");
      setMessage(error instanceof Error ? error.message : "完整对接流程未能完成。");
      setRawError(error instanceof Error ? error.stack ?? error.message : String(error));
    } finally {
      setActiveBackgroundTask(null);
      setIsBusy(false);
    }
  };

  const cancelRun = async () => {
    if (!activeRunId || !running) return;
    if (activeBackgroundTask?.status === "queued") {
      try {
        const cancelled = await cancelQueuedBackgroundTask(activeBackgroundTask.task_id);
        setActiveBackgroundTask(cancelled);
        if (cancelled.status === "cancelled") {
          setStage("cancelled");
          setMessage(`${activeRunId} 尚未启动，已从后台队列取消。`);
          return;
        }
      } catch (error) {
        setRawError(error instanceof Error ? error.message : String(error));
        return;
      }
    }
    setStage("cancelling");
    setMessage(`正在终止 ${activeRunId}…`);
    try {
      const payload = await invoke<string>("cancel_vina_run", { projectDir: project.project_dir, runId: activeRunId });
      const parsed = parseRuntime(payload);
      if (!parsed.ok) {
        setStage("running");
        setMessage(parsed.error?.message || "取消请求失败，运行状态将继续刷新。");
        setRawError(parsed.error?.raw_error || parsed.error?.suggestion || "");
        return;
      }
      setRuntime(parsed);
      if (parsed.project) commitProject(parsed.project);
      setStage(parsed.stage || "cancelling");
      setMessage(parsed.message || "取消请求已发送。");
    } catch (error) {
      setRawError(error instanceof Error ? error.message : String(error));
    }
  };

  const saveAndRefresh = async () => {
    if (!formIsValid || isBusy) return;
    setIsBusy(true);
    setRawError("");
    try {
      await saveSettings();
      await refreshPreflight(true);
      setStage("idle");
    } catch (error) {
      setStage("failed");
      setMessage(error instanceof Error ? error.message : "设置保存失败。");
      setRawError(error instanceof Error ? error.stack ?? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  const receptor = preflight?.input_stats?.receptor;
  const ligand = preflight?.input_stats?.ligand;
  const history = preflight?.run_history ?? [];
  const activeRunGuard = preflight?.active_run_guard;
  const guardedRun = activeRunGuard?.active_runs?.[0];
  const latestCompletedRun = activeRunId || history.find((run) => run.status === "finished")?.run_id || "";
  const stageText = stageLabels[stage] ?? stage;

  return (
    <section className="run-cockpit-page" aria-labelledby="run-cockpit-title">
      <header className="run-cockpit-header">
        <div>
          <span>对接工作台 · DOCKING CONSOLE</span>
          <h1 id="run-cockpit-title">搜索范围与运行</h1>
          <p>可视化设置搜索范围与 Vina 参数，检查运行条件并开始本地对接。</p>
        </div>
        <div className="run-cockpit-header-actions">
          <StatusBadge tone={preflight?.ready && !isDirty ? "ok" : preflight ? "warning" : "muted"}>
            {isDirty ? "开始时将保存并复核" : preflight?.ready ? "可开始对接" : preflight ? `${preflight.blockers.length} 个阻塞项` : "检查中"}
          </StatusBadge>
          <ActionButton variant="text" onClick={onBack}>返回格式转换</ActionButton>
        </div>
      </header>

      <div className="run-cockpit-layout">
        <main className="run-cockpit-main">
          <section className="run-cockpit-card run-preview-card">
            <div className="run-cockpit-section-heading">
              <div>
                <span className="run-cockpit-kicker">结构复核</span>
                <h2>受体、配体与搜索范围</h2>
              </div>
            </div>
            <div className="run-preview-grid">
              <Suspense fallback={<div className="run-preview run-preview-loading"><SpinnerGap className="run-monitor-spinner" size={24} /><span>正在加载 3D 复核视图…</span></div>}>
                <RunStructurePreview
                  projectDir={project.project_dir}
                  box={displayBox}
                  fitRequestKey={previewFitRequestKey}
                  wheelBinding={isBusy ? null : boxWheelBinding}
                  onWheelAdjust={adjustBoundBoxField}
                  boxLineThickness={boxLineThickness}
                  axisSpacing={axisSpacing}
                  fullscreenInspector={(
                    <RunBoxInspector
                      boxForm={boxForm}
                      volume={volume}
                      wheelBinding={boxWheelBinding}
                      wheelStep={boxWheelStep}
                      boxLineThickness={boxLineThickness}
                      axisSpacing={axisSpacing}
                      canCenterOnReceptor={Boolean(receptorCenter)}
                      canReset={canResetBox}
                      placementMessage={boxPlacementMessage}
                      disabled={isBusy}
                      idPrefix="run-box-fullscreen"
                      className="run-box-inspector-fullscreen"
                      onFieldChange={updateBoxField}
                      onWheelBindingChange={setBoxWheelBinding}
                      onWheelStepChange={setBoxWheelStep}
                      onBoxLineThicknessChange={setBoxLineThickness}
                      onAxisSpacingChange={setAxisSpacing}
                      onCenterOnReceptor={centerBoxOnReceptor}
                      onReset={resetBoxToInitial}
                    />
                  )}
                />
              </Suspense>
              <RunBoxInspector
                boxForm={boxForm}
                volume={volume}
                wheelBinding={boxWheelBinding}
                wheelStep={boxWheelStep}
                boxLineThickness={boxLineThickness}
                axisSpacing={axisSpacing}
                canCenterOnReceptor={Boolean(receptorCenter)}
                canReset={canResetBox}
                placementMessage={boxPlacementMessage}
                disabled={isBusy}
                onFieldChange={updateBoxField}
                onWheelBindingChange={setBoxWheelBinding}
                onWheelStepChange={setBoxWheelStep}
                onBoxLineThicknessChange={setBoxLineThickness}
                onAxisSpacingChange={setAxisSpacing}
                onCenterOnReceptor={centerBoxOnReceptor}
                onReset={resetBoxToInitial}
              />
            </div>
          </section>

          <section className="run-cockpit-card run-settings-card">
            <div className="run-cockpit-section-heading">
              <div>
                <span className="run-cockpit-kicker">运行设置</span>
                <h2>输入、参数与输出</h2>
              </div>
              <span className={`run-save-state ${isDirty ? "dirty" : "saved"}`}>
                <FloppyDisk aria-hidden="true" size={15} />
                {isDirty ? "有未保存更改" : `已保存 ${formatTime(project.updated_at)}`}
              </span>
            </div>

            <div className="run-settings-ledger">
              <div className="run-ledger-group">
                <h3>受体 / 配体</h3>
                <div className="run-ledger-row">
                  <span>受体 PDBQT</span>
                  <strong>{project.receptor.file || "未导入"}</strong>
                  <small>{receptor ? `${receptor.atom_count.toLocaleString()} 原子 · 链 ${receptor.chains.join(", ") || "未标注"}` : "等待检查"}</small>
                  <button type="button" onClick={() => onNavigate("import-pdbqt")}>查看</button>
                </div>
                <div className="run-ledger-row">
                  <span>配体 PDBQT</span>
                  <strong>{project.ligand.file || "未导入"}</strong>
                  <small>{ligand ? `${ligand.atom_count.toLocaleString()} 原子 · PDBQT 活性扭转 ${ligand.torsdof ?? "未记录"}` : "等待检查"}</small>
                  <button type="button" onClick={() => onNavigate("import-pdbqt")}>查看</button>
                </div>
              </div>

              <div className="run-ledger-group">
                <h3>AutoDock Vina 参数</h3>
                <div className="run-vina-fields">
                  <label>
                    <span>评分函数</span>
                    <select disabled={isBusy} value={vinaForm.scoring} onChange={(event) => updateVinaField("scoring", event.target.value)}>
                      <option value="vina">Vina</option>
                      <option value="vinardo">Vinardo</option>
                      <option value="ad4" disabled>AutoDock4（需要 maps）</option>
                    </select>
                    <small>分值不可跨函数比较</small>
                  </label>
                  {vinaFields.map((field) => {
                    const value = vinaForm[field.key];
                    const invalid = field.key === "seed"
                      ? Boolean(value.trim() && !Number.isInteger(Number(value)))
                      : !Number.isFinite(Number(value)) || Number(value) < (field.key === "cpu" ? 0 : Number.EPSILON);
                    return (
                      <label key={field.key} className={invalid ? "is-invalid" : ""}>
                        <span>{field.label}</span>
                        <input disabled={isBusy} value={value} inputMode={field.key === "energy_range" ? "decimal" : "numeric"} onChange={(event) => updateVinaField(field.key, event.target.value)} aria-invalid={invalid} />
                        <small>{field.hint}</small>
                      </label>
                    );
                  })}
                </div>
                <p className="run-science-note">Vina 使用随机搜索与局部优化；界面不将其错误描述为遗传算法。不同评分函数的分值不能直接横向比较。</p>
                <div className={`run-parameter-save-row ${isDirty ? "dirty" : "saved"}`}>
                  <div>
                    <FloppyDisk aria-hidden="true" size={18} weight="duotone" />
                    <span>
                      <strong>{isDirty ? "参数有未保存更改" : "当前参数已经保存"}</strong>
                      <small>保存 Box 与 Vina 参数，并立即重新执行运行前检查。</small>
                    </span>
                  </div>
                  <ActionButton
                    className="run-parameter-save-button"
                    variant={isDirty ? "primary" : "secondary"}
                    disabled={isBusy || !formIsValid}
                    onClick={() => void saveAndRefresh()}
                  >
                    <FloppyDisk size={16} /> 保存参数并重新检查
                  </ActionButton>
                </div>
              </div>

              <div className="run-ledger-group">
                <h3>输出与工具来源</h3>
                <div className="run-ledger-row">
                  <span>输出目录</span>
                  <strong>{preflight?.output?.runs_dir || `${project.project_dir}\\runs`}</strong>
                  <small>剩余 {formatBytes(preflight?.output?.free_bytes ?? 0)}</small>
                  <FolderOpen aria-hidden="true" size={16} />
                </div>
                <div className="run-ledger-row">
                  <span>AutoDock Vina</span>
                  <strong>{preflight?.tool?.version ? `v${preflight.tool.version}` : "等待检测"}</strong>
                  <small>{preflight?.tool?.source || "unknown"} · {preflight?.tool?.path || "未解析路径"}</small>
                  <StatusBadge tone={preflight?.tool?.status === "ok" ? "ok" : "warning"}>{preflight?.tool?.status === "ok" ? "可用" : "需配置"}</StatusBadge>
                </div>
              </div>
            </div>

            {preflight?.structure_review?.checks?.length ? (
              <AdvancedDetails summary={`结构审查明细 · ${preflight.structure_review.warning_count} 项警告`}>
                <div className="run-structure-review-grid">
                  {preflight.structure_review.checks.map((check) => (
                    <article key={check.key} className={`run-structure-review-item ${check.status}`}>
                      <div>
                        <strong>{check.name}</strong>
                        <StatusBadge tone={check.status === "ok" ? "ok" : "warning"}>
                          {check.status === "ok" ? "已读取" : check.status === "unknown" ? "需人工确认" : "需复核"}
                        </StatusBadge>
                      </div>
                      <p>{check.message}</p>
                      {check.evidence ? <small>依据：{check.evidence}</small> : null}
                    </article>
                  ))}
                </div>
                <p className="run-science-note">{preflight.structure_review.disclaimer}</p>
              </AdvancedDetails>
            ) : null}

            {!formIsValid ? (
              <WarningCallout title="参数格式需要修正">
                <p>Box 尺寸必须大于 0；Vina 的整数参数和能量范围也必须有效。</p>
              </WarningCallout>
            ) : null}

            {activeRunGuard?.blocked ? (
              <WarningCallout title={guardedRun ? "已有未完成的 Vina 运行" : "暂时无法确认运行恢复状态"}>
                <p>{activeRunGuard.message}</p>
                {activeRunGuard.error ? <small>{activeRunGuard.error}</small> : null}
                {guardedRun ? (
                  <div className="button-row">
                    <ActionButton variant="secondary" onClick={() => onOpenRunExecute(project, guardedRun.run_id)}>
                      打开 {guardedRun.run_id} 详情
                    </ActionButton>
                    {guardedRun.can_cancel ? (
                      <ActionButton variant="secondary" onClick={() => void cancelRun()}>
                        <Stop size={15} weight="fill" /> 安全取消
                      </ActionButton>
                    ) : null}
                  </div>
                ) : null}
              </WarningCallout>
            ) : null}

            {runtime || running || stage === "finished" || stage === "failed" || stage === "cancelled" ? (
              <section className={`run-monitor run-monitor-${stage}`} aria-live="polite">
                <div className="run-monitor-heading">
                  <div>
                    {running ? <SpinnerGap className="run-monitor-spinner" size={20} /> : stage === "finished" ? <CheckCircle size={20} weight="fill" /> : <WarningCircle size={20} weight="fill" />}
                    <div>
                      <strong>{stageText}</strong>
                      <span>{activeRunId || preflight?.next_run_id || "待创建"} · {formatDuration(runtime?.elapsed_seconds ?? null)}</span>
                    </div>
                  </div>
                  {running ? <ActionButton variant="secondary" onClick={() => void cancelRun()}><Stop size={15} weight="fill" /> 终止运行</ActionButton> : null}
                </div>
                <div className="run-progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress)}>
                  <span style={{ width: `${Math.max(0, Math.min(100, progress))}%` }} />
                </div>
                <p>{runtime?.progress?.message || message || stageText}</p>
                {(runtime?.log_tail || runtime?.stderr_tail) ? (
                  <AdvancedDetails summary="实时日志与诊断">
                    <pre className="run-log-tail">{runtime.log_tail || runtime.stdout_tail || runtime.stderr_tail}</pre>
                    {runtime.stderr_tail ? <pre className="run-log-tail error">{runtime.stderr_tail}</pre> : null}
                  </AdvancedDetails>
                ) : null}
              </section>
            ) : null}

            <div className="run-action-bar">
              <div className="run-action-context">
                <span className={`run-action-save-status ${isDirty ? "dirty" : "saved"}`}>
                  <FloppyDisk aria-hidden="true" size={15} />
                  {isDirty ? "参数尚未保存" : "参数已保存并通过最新检查"}
                </span>
                {activeRunId && stage !== "finished" ? (
                  <ActionButton variant="text" onClick={() => onOpenRunExecute(project, activeRunId)}>打开运行详情</ActionButton>
                ) : null}
                {latestCompletedRun && stage === "finished" ? (
                  <ActionButton variant="text" onClick={() => onOpenResultPage(project, latestCompletedRun)}>查看本次结果</ActionButton>
                ) : null}
              </div>
              <div className="run-primary-action">
                <div className="run-split-action" role="group" aria-label="对接执行操作">
                  <ActionButton
                    className="run-split-action-main"
                    variant="primary"
                    disabled={isBusy || !formIsValid || !preflight?.ready}
                    onClick={() => void runWorkflow()}
                  >
                    {isBusy ? <SpinnerGap className="run-monitor-spinner" size={18} /> : <Play size={18} weight="fill" />}
                    {isBusy ? "运行中…" : runActionLabels[actionMode]}
                  </ActionButton>
                  <span className="run-split-action-selector" title={runActionDescriptions[actionMode]}>
                    <select
                      aria-label="选择执行方式"
                      value={actionMode}
                      disabled={isBusy}
                      onChange={(event) => setActionMode(event.target.value as RunActionMode)}
                    >
                      <option value="full">{runActionDescriptions.full}</option>
                      <option value="prepare">{runActionDescriptions.prepare}</option>
                      <option value="config">{runActionDescriptions.config}</option>
                    </select>
                    <CaretDown aria-hidden="true" size={15} weight="bold" />
                  </span>
                </div>
              </div>
            </div>
            {message && !runtime ? <p className="run-inline-message" role={stage === "failed" ? "alert" : "status"}>{message}</p> : null}
            {rawError ? <AdvancedDetails summary="查看原始诊断"><pre>{rawError}</pre></AdvancedDetails> : null}
          </section>

          <section className="run-cockpit-card run-history-card">
            <div className="run-cockpit-section-heading">
              <div>
                <span className="run-cockpit-kicker">项目记录</span>
                <h2>运行历史</h2>
              </div>
              <span>{history.length} 条 run</span>
            </div>
            {history.length ? (
              <div className="run-history-table-wrap">
                <table className="run-history-table">
                  <thead><tr><th>Run</th><th>状态</th><th>开始时间</th><th>耗时</th><th>最佳评分</th><th>操作</th></tr></thead>
                  <tbody>
                    {history.slice(0, 8).map((run) => (
                      <tr key={run.run_id}>
                        <td><strong>{run.run_id}</strong></td>
                        <td><StatusBadge tone={statusTone(run.status)}>{run.status}</StatusBadge></td>
                        <td>{formatTime(run.started_at || run.created_at)}</td>
                        <td>{formatDuration(run.duration_seconds)}</td>
                        <td>{run.best_affinity ?? "—"}</td>
                        <td>
                          {run.status === "finished" ? <button type="button" onClick={() => onOpenResultPage(project, run.run_id)}>查看结果</button> : <button type="button" onClick={() => onOpenRunExecute(project, run.run_id)}>运行详情</button>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p className="run-history-empty">尚无运行记录。首次完整运行后，这里会保存状态、耗时和最佳评分。</p>}
          </section>
        </main>

        <aside className="run-preflight-rail" aria-label="运行前检查">
          <header>
            <div>
              <span>Preflight</span>
              <h2>运行前检查</h2>
            </div>
            <button type="button" disabled={isRefreshing || isBusy} onClick={() => void (isDirty ? saveAndRefresh() : refreshPreflight())}>
              <ArrowRight className={isRefreshing ? "run-monitor-spinner" : ""} size={16} /> {isDirty ? "保存并检查" : "重新检查"}
            </button>
          </header>

          <section className={`run-readiness-summary ${preflight?.ready && !isDirty ? "ready" : "blocked"}`}>
            {preflight?.ready && !isDirty ? <CheckCircle size={25} weight="fill" /> : <WarningCircle size={25} weight="fill" />}
            <div>
              <strong>{isDirty ? "屏幕参数尚未保存" : preflight?.ready ? "执行条件已满足" : preflight ? "仍有阻塞项" : "正在检查"}</strong>
              <p>{isDirty ? "开始对接或点击保存检查时会先写入并重新校验。" : preflight?.ready ? "表示当前文件与环境可执行，不代表结构方案科学正确。" : preflight?.blockers?.[0] || "正在读取本机状态。"}</p>
            </div>
          </section>

          <div className="run-preflight-list">
            {(preflight?.checks ?? []).map((check) => {
              const repairPage = safeRepairPage(check);
              return (
                <article key={check.key} className={`run-preflight-item ${check.status}`}>
                  {checkIcon(check)}
                  <div>
                    <strong>{check.name}</strong>
                    <p>{check.message}</p>
                    {check.detail ? <small>{check.detail}</small> : null}
                  </div>
                  {repairPage && check.status !== "ok" ? <button type="button" onClick={() => onNavigate(repairPage)}>修复</button> : null}
                </article>
              );
            })}
            {!preflight?.checks?.length ? <p className="run-preflight-empty">正在汇总输入、工具和本机资源…</p> : null}
          </div>

          <section className="run-rail-section">
            <div className="run-rail-section-title"><Clock size={18} /><strong>耗时估计</strong></div>
            <p>{preflight?.estimate?.available ? preflight.estimate.range_label : "暂无可靠估算"}</p>
            <small>{preflight?.estimate?.message || "完成同机、同协议运行后再基于历史样本给出区间。"}</small>
          </section>

          <section className="run-rail-section">
            <div className="run-rail-section-title"><Cpu size={18} /><strong>本机环境</strong></div>
            <dl>
              <div><dt>系统</dt><dd>{preflight?.system ? `${preflight.system.system} ${preflight.system.release}` : "检查中"}</dd></div>
              <div><dt>架构</dt><dd>{preflight?.system?.machine || "—"}</dd></div>
              <div><dt>逻辑 CPU</dt><dd>{preflight?.system?.cpu_count || "—"}</dd></div>
              <div><dt>内存</dt><dd>{formatBytes(preflight?.system?.memory_bytes ?? 0)}</dd></div>
            </dl>
          </section>

          <section className="run-rail-section">
            <div className="run-rail-section-title"><HardDrives size={18} /><strong>项目与输出</strong></div>
            <dl>
              <div><dt>项目</dt><dd>{project.project_name}</dd></div>
              <div><dt>下一 run</dt><dd>{preflight?.next_run_id || "待计算"}</dd></div>
              <div><dt>保存时间</dt><dd>{formatTime(project.updated_at)}</dd></div>
              <div><dt>磁盘空间</dt><dd>{formatBytes(preflight?.output?.free_bytes ?? 0)}</dd></div>
            </dl>
            <AdvancedDetails summary="命令预览">
              <pre>{preflight?.command_preview || "保存设置并生成配置后显示。"}</pre>
            </AdvancedDetails>
          </section>

          <section className="run-rail-section run-rail-disclaimer">
            <div className="run-rail-section-title"><Database size={18} /><strong>科学边界</strong></div>
            <p>Docking score 仅供结构结合趋势参考，不能替代实验验证。</p>
          </section>
        </aside>
      </div>
    </section>
  );
}
