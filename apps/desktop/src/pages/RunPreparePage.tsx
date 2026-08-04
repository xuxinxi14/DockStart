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
  ShieldCheck,
  SpinnerGap,
  Stop,
  WarningCircle,
  XCircle,
} from "@phosphor-icons/react";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import AutoGridMapsPanel from "../components/AutoGridMapsPanel";
import BatchScreeningPanel from "../components/BatchScreeningPanel";
import FlexibleReceptorPanel, {
  type FlexibleReceptorIdentityContext,
} from "../components/FlexibleReceptorPanel";
import MultiLigandDockingPanel from "../components/MultiLigandDockingPanel";
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
  VinaCliCapabilities,
  VinaRunMode,
} from "../types";
import {
  cancelQueuedBackgroundTask,
  findActiveBackgroundTask,
  startVinaRunTask,
  waitForBackgroundTask,
  type BackgroundTaskStatus,
} from "../utils/backgroundTasks";
import { readDockingWorkspaceMode, writeDockingWorkspaceMode, type DockingWorkspaceMode } from "../utils/dockingMode";
import { multipleLigandCompatibilityIssues } from "../utils/multipleLigandSelection";
import {
  customizedAdvancedVinaCount,
  getApplicableAdvancedVinaKeys,
  getVinaExpertToggleState,
  getVinaExpertValueState,
  isVinaExpertOptionApplicable,
  parseVinaForm,
  resetAdvancedVinaFields,
  vinaFormsEqual,
  vinaSettingsToForm,
  type VinaAdvancedKey,
  type VinaExpertToggleKey,
  type VinaForm,
  type VinaNumericKey,
  type VinaTextKey,
} from "../utils/vinaForm";

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
const minBoxDimension = 0.1;

const runActionLabels: Record<RunActionMode, string> = {
  full: "开始对接",
  prepare: "创建运行记录",
  config: "生成配置",
};

const vinaRunModeLabels: Record<VinaRunMode, string> = {
  dock: "全局对接",
  score_only: "仅评分",
  local_only: "局部优化",
};

const vinaRunModeActions: Record<VinaRunMode, string> = {
  dock: "开始对接",
  score_only: "评估当前姿势",
  local_only: "局部优化当前姿势",
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
  queued: "等待后台队列",
  saving: "保存设置",
  configuring: "生成配置",
  preparing: "创建运行记录",
  starting: "启动 Vina",
  running: "AutoDock Vina 正在搜索构象",
  baseline_starting: "准备记录输入姿势评分",
  baseline_scoring: "正在记录输入姿势评分",
  baseline_recorded: "输入姿势评分已记录",
  local_starting: "准备局部优化",
  local_optimizing: "正在局部优化当前姿势",
  local_recorded: "局部优化结果已记录",
  postprocessing: "正在分类水分子并生成派生结构",
  postprocess_failed: "水分子后处理失败",
  cancelling: "正在终止运行",
  cancel_pending: "等待安全取消",
  cancelled: "已取消",
  analyzing: "解析评分",
  reporting: "生成结果分析报告",
  finished: "完整流程已完成",
  failed: "运行失败",
  interrupted: "运行已中断",
};

const activeRunStages = new Set([
  "queued",
  "starting",
  "running",
  "baseline_starting",
  "baseline_scoring",
  "baseline_recorded",
  "local_starting",
  "local_optimizing",
  "local_recorded",
  "postprocessing",
  "cancelling",
  "cancel_pending",
]);

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
  return vinaSettingsToForm(project.vina);
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

function projectRunMode(project: DockStartProject): VinaRunMode {
  const value = project.docking_protocol?.run_mode;
  return value === "score_only" || value === "local_only" ? value : "dock";
}

function projectAutobox(project: DockStartProject): boolean {
  return projectRunMode(project) === "dock" ? false : Boolean(project.docking_protocol?.autobox);
}

function boxCoordinate(value: number): string {
  return String(Number(value.toFixed(3)));
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

function vinaCapabilitySummary(
  capabilities: VinaCliCapabilities | undefined,
  fallbackVersion: string,
  checking: boolean,
  applicableKeys: readonly VinaAdvancedKey[],
): string {
  const version = capabilities?.version || fallbackVersion;
  const prefix = version ? `Vina ${version}` : "Vina";
  const relevantKeys = applicableKeys.filter(
    (key): key is "no_refine" | "force_even_voxels" | "unbound_energy" => (
      key === "no_refine"
      || key === "force_even_voxels"
      || key === "unbound_energy"
    ),
  );
  const relevantFeatures = relevantKeys.map((key) => capabilities?.features?.[key]);
  if (checking && !capabilities?.checked) return `${prefix} · 正在检查专家选项`;
  if (!capabilities?.checked || capabilities.status === "unknown") return `${prefix} · 专家选项尚未确认`;
  if (
    relevantFeatures.length > 0
    && relevantFeatures.every(
      (feature) => feature?.status === "supported" && feature.supported === true,
    )
  ) {
    return `${prefix} · 当前相关专家选项已确认`;
  }
  if (
    relevantFeatures.some(
      (feature) => !feature || feature.status === "unknown" || feature.supported === null,
    )
  ) {
    return `${prefix} · 当前相关专家选项尚未确认`;
  }
  return `${prefix} · 部分当前相关专家选项不可用`;
}

function checkIcon(check: RunPreflightCheck) {
  if (check.status === "ok") return <CheckCircle aria-hidden="true" size={20} weight="fill" />;
  if (check.status === "warning") return <WarningCircle aria-hidden="true" size={20} weight="fill" />;
  return <XCircle aria-hidden="true" size={20} weight="fill" />;
}

function safeRepairPage(check: RunPreflightCheck): PageId | null {
  const page = check.action_page;
  const supported = new Set<PageId>(["import-pdbqt", "box-setup", "vina-param", "vina-config", "run-prepare", "settings", "toolchain-status"]);
  if (page && supported.has(page as PageId)) return page as PageId;
  if (check.key === "receptor" || check.key === "ligand") return "import-pdbqt";
  if (check.key === "box") return "box-setup";
  if (check.key === "vina_params" || check.key === "cpu") return "vina-param";
  if (check.key === "ad4_maps") return "run-prepare";
  if (check.key === "vina") return "settings";
  return null;
}

function latestMultipleLigandRunId(project: DockStartProject): string {
  const value = [...project.runs]
    .reverse()
    .find((run) => run.protocol_id === "simultaneous_multi_ligand")
    ?.run_id;
  return typeof value === "string" ? value : "";
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
  const [runMode, setRunMode] = useState<VinaRunMode>(() => projectRunMode(initialProject));
  const [autobox, setAutobox] = useState(() => projectAutobox(initialProject));
  const [preflight, setPreflight] = useState<RunPreflightResponse | null>(null);
  const [runtime, setRuntime] = useState<RunRuntimeStatusResponse | null>(null);
  const [actionMode, setActionMode] = useState<RunActionMode>("full");
  const [stage, setStage] = useState("idle");
  const [activeRunId, setActiveRunId] = useState("");
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isBusy, setIsBusy] = useState(false);
  const [isConfirmingPose, setIsConfirmingPose] = useState(false);
  const [isDirty, setIsDirty] = useState(false);
  const [activeBackgroundTask, setActiveBackgroundTask] = useState<BackgroundTaskStatus | null>(null);
  const [boxWheelBinding, setBoxWheelBinding] = useState<RunBoxFieldKey | null>(null);
  const [boxWheelStep, setBoxWheelStep] = useState<RunBoxWheelStep>(0.1);
  const [boxLineThickness, setBoxLineThickness] = useState<RunBoxLineThickness>("standard");
  const [axisSpacing, setAxisSpacing] = useState<RunAxisSpacing>("standard");
  const [boxPlacementMessage, setBoxPlacementMessage] = useState("");
  const [previewFitRequestKey, setPreviewFitRequestKey] = useState(0);
  const [workspaceMode, setWorkspaceMode] = useState<DockingWorkspaceMode>(() => readDockingWorkspaceMode(initialProject.project_dir));
  const [residueSelectionActive, setResidueSelectionActive] = useState(false);
  const [flexibleResidues, setFlexibleResidues] = useState<string[]>([]);
  const [pickedResidue, setPickedResidue] = useState({
    selector: "",
    selectionContextSha256: "",
    token: 0,
  });
  const [flexibleIdentityContext, setFlexibleIdentityContext] =
    useState<FlexibleReceptorIdentityContext | null>(null);
  const mountedRef = useRef(true);
  const dirtyRef = useRef(false);
  const preflightRequestRef = useRef(0);
  const activeTaskAbortRef = useRef<AbortController | null>(null);
  const initialBoxSnapshotRef = useRef({
    projectDir: initialProject.project_dir,
    form: boxToForm(initialProject),
  });

  const isAd4Maps = project.docking_protocol?.engine === "ad4_maps";
  const isStandardAd4Maps = isAd4Maps && ![
    "ad4zn_beta",
    "hydrated_ad4_experimental",
  ].includes(String(project.docking_protocol?.protocol_id || "ad4_maps"));
  const isEvaluationMode = runMode !== "dock";
  const receptorMode = project.docking_protocol?.receptor_mode ?? project.docking_protocol?.mode ?? "rigid";
  const applicableAdvancedVinaKeys = useMemo(
    () => getApplicableAdvancedVinaKeys(project.docking_protocol?.engine, runMode, receptorMode),
    [project.docking_protocol?.engine, receptorMode, runMode],
  );
  const parsedBox = useMemo(() => parseBoxForm(boxForm), [boxForm]);
  const parsedVina = useMemo(
    () => parseVinaForm(vinaForm, {
      engine: project.docking_protocol?.engine,
      runMode,
      receptorMode,
    }),
    [project.docking_protocol?.engine, receptorMode, runMode, vinaForm],
  );
  const boxRequired = isAd4Maps || runMode === "dock" || !autobox;
  const vinaCapabilities = preflight?.tool?.capabilities;
  const capabilityChecking = isRefreshing && !vinaCapabilities?.checked;
  const noRefineApplicable = isVinaExpertOptionApplicable(project.docking_protocol?.engine, runMode, "no_refine");
  const forceEvenVoxelsApplicable = isVinaExpertOptionApplicable(project.docking_protocol?.engine, runMode, "force_even_voxels");
  const unboundEnergyApplicable = applicableAdvancedVinaKeys.includes("unbound_energy");
  const poseInputAttestation = preflight?.pose_input_attestation;
  const activeRunGuard = preflight?.active_run_guard;
  const activeRunBlocked = activeRunGuard?.blocked === true;
  const poseInputConfirmed =
    isEvaluationMode
    && poseInputAttestation?.required === true
    && poseInputAttestation.valid === true
    && poseInputAttestation.status === "confirmed";
  const attestedReceptorSha256 =
    poseInputAttestation?.current_receptor_sha256
    || poseInputAttestation?.receptor_sha256
    || "";
  const attestedLigandSha256 =
    poseInputAttestation?.current_ligand_sha256
    || poseInputAttestation?.ligand_sha256
    || "";
  const attestedFlexSha256 =
    poseInputAttestation?.current_flex_sha256
    || poseInputAttestation?.flex_sha256
    || "";
  const hasUnboundEnergy = vinaForm.unbound_energy.trim() !== "";
  const noRefineControl = getVinaExpertToggleState(
    vinaCapabilities?.features?.no_refine,
    vinaForm.no_refine,
    isBusy,
  );
  const forceEvenVoxelsControl = getVinaExpertToggleState(
    vinaCapabilities?.features?.force_even_voxels,
    vinaForm.force_even_voxels,
    isBusy,
  );
  const unboundEnergyControl = getVinaExpertValueState(
    vinaCapabilities?.features?.unbound_energy,
    hasUnboundEnergy,
    isBusy,
  );
  const expertCapabilityBlocked = (
    (noRefineApplicable && noRefineControl.blocking)
    || (forceEvenVoxelsApplicable && forceEvenVoxelsControl.blocking)
    || (unboundEnergyApplicable && unboundEnergyControl.blocking)
  );
  const multipleLigandFeature = vinaCapabilities?.features?.multiple_ligands;
  const multipleLigandIssues = useMemo(
    () => multipleLigandCompatibilityIssues({
      engine: project.docking_protocol?.engine,
      protocolId: project.docking_protocol?.protocol_id,
      receptorMode,
      runMode,
      capabilityChecked: Boolean(
        vinaCapabilities?.checked
        && multipleLigandFeature
        && multipleLigandFeature.status !== "unknown"
      ),
      capabilitySupported: multipleLigandFeature?.supported,
    }),
    [
      multipleLigandFeature,
      project.docking_protocol?.engine,
      project.docking_protocol?.protocol_id,
      receptorMode,
      runMode,
      vinaCapabilities?.checked,
    ],
  );
  const latestJointRunId = useMemo(() => latestMultipleLigandRunId(project), [project]);
  const latestJointRunOwnsActiveGuard = Boolean(
    latestJointRunId
    && activeRunGuard?.active_runs?.some((run) => run.run_id === latestJointRunId),
  );
  const numericFormIsValid = Boolean(parsedVina && (parsedBox || !boxRequired));
  const formIsValid = numericFormIsValid && !expertCapabilityBlocked;
  const displayBox = parsedBox ?? project.box;
  const volume = displayBox.size_x * displayBox.size_y * displayBox.size_z;
  const running = activeRunStages.has(stage);
  const progress = runtime?.progress?.percent ?? (stage === "finished" ? 100 : 0);
  const receptorCenter = preflight?.input_stats?.receptor?.coordinate_center ?? null;
  const canResetBox = !boxFormsEqual(boxForm, initialBoxSnapshotRef.current.form);
  const runProtocolChanged = runMode !== projectRunMode(project) || autobox !== projectAutobox(project);
  const advancedVinaCustomCount = useMemo(
    () => customizedAdvancedVinaCount(vinaForm, applicableAdvancedVinaKeys),
    [applicableAdvancedVinaKeys, vinaForm],
  );
  const advancedCapabilitySummary = vinaCapabilitySummary(
    vinaCapabilities,
    preflight?.tool?.version ?? "",
    capabilityChecking,
    applicableAdvancedVinaKeys,
  );

  const commitProject = useCallback((nextProject: DockStartProject, syncForms = false) => {
    if (!mountedRef.current) return;
    setProject(nextProject);
    onProjectChange(nextProject);
    if (syncForms) {
      setBoxForm(boxToForm(nextProject));
      setVinaForm(vinaToForm(nextProject));
      setRunMode(projectRunMode(nextProject));
      setAutobox(projectAutobox(nextProject));
      setIsDirty(false);
      dirtyRef.current = false;
    }
  }, [onProjectChange]);

  const selectWorkspaceMode = useCallback((mode: DockingWorkspaceMode) => {
    if (mode !== "single") setResidueSelectionActive(false);
    setWorkspaceMode(mode);
    writeDockingWorkspaceMode(project.project_dir, mode);
  }, [project.project_dir]);
  const selectRunMode = useCallback((mode: VinaRunMode) => {
    if (activeRunBlocked) return;
    setRunMode(mode);
    if (mode === "dock" || isAd4Maps) {
      setAutobox(false);
    } else if (projectRunMode(project) === "dock") {
      setAutobox(true);
    }
    if (mode !== "dock" && workspaceMode === "batch") {
      selectWorkspaceMode("single");
    }
    setIsDirty(true);
    dirtyRef.current = true;
  }, [activeRunBlocked, isAd4Maps, project, selectWorkspaceMode, workspaceMode]);
  const handleResidueSelect = useCallback((selector: string, selectionContextSha256: string) => {
    setPickedResidue((current) => ({
      selector,
      selectionContextSha256,
      token: current.token + 1,
    }));
  }, []);
  const handleFlexibleIdentityContext = useCallback(
    (context: FlexibleReceptorIdentityContext | null) => {
      setFlexibleIdentityContext(context);
    },
    [],
  );

  useEffect(() => {
    setWorkspaceMode(readDockingWorkspaceMode(initialProject.project_dir));
  }, [initialProject.project_dir]);

  useEffect(() => {
    setFlexibleIdentityContext(null);
    setPickedResidue({
      selector: "",
      selectionContextSha256: "",
      token: 0,
    });
  }, [project.project_dir]);

  useEffect(() => {
    if (((isAd4Maps && !isStandardAd4Maps) || isEvaluationMode) && workspaceMode === "batch") {
      setWorkspaceMode("single");
      writeDockingWorkspaceMode(project.project_dir, "single");
    }
  }, [isAd4Maps, isEvaluationMode, isStandardAd4Maps, project.project_dir, workspaceMode]);

  useEffect(() => {
    if (isAd4Maps && autobox) {
      setAutobox(false);
      setIsDirty(true);
      dirtyRef.current = true;
    }
  }, [autobox, isAd4Maps]);

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

  const confirmPoseInput = useCallback(async () => {
    if (!isEvaluationMode || isBusy || isConfirmingPose) return;
    setIsConfirmingPose(true);
    setRawError("");
    setMessage(
      receptorMode === "flexible"
        ? "正在记录当前运行受体、柔性侧链与配体文件的用户确认…"
        : "正在记录当前受体与配体文件的用户确认…",
    );
    try {
      const rawPayload = await invoke<string>("update_vina_run_protocol", {
        projectDir: project.project_dir,
        runMode,
        autobox: isAd4Maps ? false : autobox,
        confirmPoseContext: true,
      });
      const response = parseProjectResponse(rawPayload);
      if (!response.ok || !response.project) {
        throw new Error(response.error?.message ?? "输入姿势确认保存失败。");
      }
      commitProject(response.project);
      setMessage(
        receptorMode === "flexible"
          ? "已记录用户对当前运行受体、柔性侧链与配体坐标关系的确认，正在重新检查文件哈希…"
          : "已记录用户对当前受体与配体坐标关系的确认，正在重新检查文件哈希…",
      );
      await refreshPreflight(false);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "输入姿势确认保存失败。");
      setRawError(error instanceof Error ? error.stack ?? error.message : String(error));
    } finally {
      if (mountedRef.current) setIsConfirmingPose(false);
    }
  }, [
    autobox,
    commitProject,
    isAd4Maps,
    isBusy,
    isConfirmingPose,
    isEvaluationMode,
    project.project_dir,
    receptorMode,
    refreshPreflight,
    runMode,
  ]);

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
    const nextDirty = !boxFormsEqual(nextForm, boxToForm(project))
      || !vinaFormsEqual(vinaForm, vinaToForm(project))
      || runProtocolChanged;
    setIsDirty(nextDirty);
    dirtyRef.current = nextDirty;
  }, [boxForm, isBusy, project, receptorCenter, runProtocolChanged, vinaForm]);

  const resetBoxToInitial = useCallback(() => {
    if (isBusy) return;
    const nextForm = { ...initialBoxSnapshotRef.current.form };
    setBoxForm(nextForm);
    setBoxWheelBinding(null);
    setBoxPlacementMessage("已恢复进入对接工作台时的 Box 参数。");
    setPreviewFitRequestKey((current) => current + 1);
    const nextDirty = !boxFormsEqual(nextForm, boxToForm(project))
      || !vinaFormsEqual(vinaForm, vinaToForm(project))
      || runProtocolChanged;
    setIsDirty(nextDirty);
    dirtyRef.current = nextDirty;
  }, [isBusy, project, runProtocolChanged, vinaForm]);

  const updateVinaField = (key: VinaTextKey, value: string) => {
    setVinaForm((current) => ({ ...current, [key]: value }));
    setIsDirty(true);
    dirtyRef.current = true;
  };

  const updateVinaToggle = (key: VinaExpertToggleKey, checked: boolean) => {
    setVinaForm((current) => ({ ...current, [key]: checked }));
    setIsDirty(true);
    dirtyRef.current = true;
  };

  const resetAdvancedVina = () => {
    if (isBusy) return;
    setVinaForm((current) => resetAdvancedVinaFields(current, applicableAdvancedVinaKeys));
    setIsDirty(true);
    dirtyRef.current = true;
  };

  const saveSettings = useCallback(async (announce = true): Promise<DockStartProject> => {
    if (activeRunBlocked) {
      throw new Error(
        "当前 Vina 运行尚未结束，不能修改 Box、Vina 参数或任务类型。",
      );
    }
    const nextBox = parseBoxForm(boxForm) ?? (!boxRequired ? project.box : null);
    const nextVina = parseVinaForm(vinaForm, {
      engine: project.docking_protocol?.engine,
      runMode,
      receptorMode,
    });
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
    const protocolPayload = await invoke<string>("update_vina_run_protocol", {
      projectDir: project.project_dir,
      runMode,
      autobox: runMode === "dock" || isAd4Maps ? false : autobox,
      confirmPoseContext: false,
    });
    const protocolResponse = parseProjectResponse(protocolPayload);
    if (!protocolResponse.ok || !protocolResponse.project) {
      throw new Error(protocolResponse.error?.message ?? "运行任务类型保存失败。");
    }
    const nextProject = projectFromResponse(protocolResponse, projectFromResponse(vinaResponse, boxResponse.project));
    commitProject(nextProject, true);
    if (announce) setMessage("搜索范围、任务类型与 Vina 参数已保存。正在重新检查…");
    return nextProject;
  }, [activeRunBlocked, autobox, boxForm, boxRequired, commitProject, isAd4Maps, project.box, project.docking_protocol?.engine, project.project_dir, receptorMode, runMode, vinaForm]);

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
      setMessage(
        `${runId} 正在启动 ${
          runMode === "score_only" ? "当前姿势评分" : runMode === "local_only" ? "局部优化" : "AutoDock Vina 对接"
        }…`,
      );
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
      setMessage(
        runMode === "dock"
          ? "对接完成，正在解析评分并生成 scores.csv…"
          : runMode === "score_only"
            ? "当前姿势评分完成，正在解析能量分解…"
            : "局部优化完成，正在比较优化前后评分与几何变化…",
      );
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
      setMessage(
        `${runId} 已完成：${vinaRunModeLabels[runMode]}、结果解析和实验记录导出全部成功。`,
      );
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
    if (!formIsValid || isBusy || activeRunBlocked) return;
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
  const guardedRun = activeRunGuard?.active_runs?.[0];
  const latestCompletedRun = activeRunId || history.find((run) => run.status === "finished")?.run_id || "";
  const stageText = stage === "running"
    ? runMode === "score_only"
      ? "正在评估当前姿势"
      : runMode === "local_only"
        ? "正在局部优化当前姿势"
        : stageLabels.running
    : stageLabels[stage] ?? stage;

  return (
    <section className={`run-cockpit-page is-${workspaceMode}-mode`} aria-labelledby="run-cockpit-title">
      <header className="run-cockpit-header">
        <div>
          <span>VINA 运行工作台 · VINA RUN CONSOLE</span>
          <h1 id="run-cockpit-title">
            {workspaceMode === "batch"
              ? "串行批量筛选"
              : workspaceMode === "simultaneous"
                ? "多配体共同对接"
              : runMode === "score_only"
                ? "当前姿势评分"
                : runMode === "local_only"
                  ? "当前姿势局部优化"
                  : "搜索范围与运行"}
          </h1>
          <p>{workspaceMode === "batch"
            ? "检查共享受体，设置统一 Box 与 Vina 参数，然后创建逐个运行、可恢复的配体队列。"
            : workspaceMode === "simultaneous"
              ? "选择两个配体，让它们在同一次 Vina 全局搜索中共同优化并输出联合构象。"
            : runMode === "score_only"
              ? "计算输入姿势的评分与能量分解，不执行全局构象搜索。"
              : runMode === "local_only"
                ? "在输入姿势附近进行局部优化，并保存优化后的单个 PDBQT。"
                : isAd4Maps
                  ? "复核网格范围与 AutoDock4 maps，检查运行条件并开始本地对接。"
                  : "可视化设置搜索范围与 Vina 参数，检查运行条件并开始本地对接。"}</p>
        </div>
        <div className="run-cockpit-header-actions">
          <StatusBadge tone={preflight?.ready && !isDirty ? "ok" : preflight ? "warning" : "muted"}>
            {isDirty
              ? "参数待保存"
              : preflight?.ready
                ? workspaceMode === "batch"
                  ? "可创建串行队列"
                  : workspaceMode === "simultaneous"
                    ? multipleLigandIssues.length
                      ? "共同对接不可用"
                      : "可选择两个成员"
                    : `可开始${vinaRunModeLabels[runMode]}`
                : preflight
                  ? `${preflight.blockers.length} 个阻塞项`
                  : "检查中"}
          </StatusBadge>
          <ActionButton variant="text" onClick={onBack}>返回格式转换</ActionButton>
        </div>
      </header>

      <nav className="run-workspace-mode" aria-label="配体运行模式">
        <button type="button" className={workspaceMode === "single" ? "active" : ""} aria-pressed={workspaceMode === "single"} onClick={() => selectWorkspaceMode("single")}>单配体任务</button>
        <button
          type="button"
          className={workspaceMode === "batch" ? "active" : ""}
          aria-pressed={workspaceMode === "batch"}
          disabled={(isAd4Maps && !isStandardAd4Maps) || isEvaluationMode}
          title={
            isAd4Maps && !isStandardAd4Maps
              ? "串行批量 AD4 仅支持标准 AutoDock4 maps，不支持 AD4Zn 或水合协议"
              : isEvaluationMode
                ? "串行批量筛选仅支持全局对接"
                : undefined
          }
          onClick={() => selectWorkspaceMode("batch")}
        >
          串行批量筛选
        </button>
        <button
          type="button"
          className={workspaceMode === "simultaneous" ? "active" : ""}
          aria-pressed={workspaceMode === "simultaneous"}
          disabled={multipleLigandIssues.length > 0 && workspaceMode !== "simultaneous"}
          title={multipleLigandIssues.length ? multipleLigandIssues.join("；") : undefined}
          onClick={() => selectWorkspaceMode("simultaneous")}
        >
          多配体共同对接 <small>实验性</small>
        </button>
      </nav>
      {multipleLigandIssues.length && workspaceMode !== "simultaneous" ? (
        <p className="run-workspace-mode-availability" role="status">
          多配体共同对接暂不可用：{multipleLigandIssues.join("；")}。
        </p>
      ) : null}

      {workspaceMode === "single" ? (
        <nav className="run-task-mode-switch" aria-label="Vina 任务类型">
          {(Object.keys(vinaRunModeLabels) as VinaRunMode[]).map((mode) => (
            <button
              key={mode}
              type="button"
              className={runMode === mode ? "active" : ""}
              aria-pressed={runMode === mode}
              disabled={isBusy || activeRunBlocked}
              title={activeRunBlocked ? "当前 Vina 运行尚未结束，不能切换任务类型" : undefined}
              onClick={() => selectRunMode(mode)}
            >
              <strong>{vinaRunModeLabels[mode]}</strong>
              <span>
                {mode === "dock"
                  ? "搜索并输出多个候选构象"
                  : mode === "score_only"
                    ? "评价输入姿势，不生成新构象"
                    : "优化输入姿势并输出单个构象"}
              </span>
            </button>
          ))}
        </nav>
      ) : null}

      <div className="run-cockpit-layout">
        <main className="run-cockpit-main">
          <section className="run-cockpit-card run-preview-card">
            <div className="run-cockpit-section-heading">
              <div>
                <span className="run-cockpit-kicker">结构复核</span>
                <h2>
                  {isEvaluationMode
                    ? "受体与当前配体姿势"
                    : workspaceMode === "simultaneous"
                      ? "受体、参考配体与搜索范围"
                      : "受体、配体与搜索范围"}
                </h2>
              </div>
            </div>
            <div className="run-preview-grid">
              <Suspense fallback={<div className="run-preview run-preview-loading"><SpinnerGap className="run-monitor-spinner" size={24} /><span>正在加载 3D 复核视图…</span></div>}>
                <RunStructurePreview
                  projectDir={project.project_dir}
                  box={displayBox}
                  fitRequestKey={previewFitRequestKey}
                  wheelBinding={isBusy || residueSelectionActive ? null : boxWheelBinding}
                  onWheelAdjust={adjustBoundBoxField}
                  boxLineThickness={boxLineThickness}
                  axisSpacing={axisSpacing}
                  residueSelectionActive={residueSelectionActive}
                  selectedResidues={flexibleResidues}
                  residueSelectionStructure={
                    residueSelectionActive
                      ? flexibleIdentityContext?.viewer ?? null
                      : null
                  }
                  selectionContextSha256={
                    flexibleIdentityContext?.selection_context_sha256 ?? ""
                  }
                  onResidueSelect={handleResidueSelect}
                  onResidueSelectionComplete={() => setResidueSelectionActive(false)}
                  useActiveReceptorInputs={
                    isEvaluationMode
                    && receptorMode === "flexible"
                    && !residueSelectionActive
                  }
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

          {workspaceMode === "single" ? (
            <AutoGridMapsPanel
              project={project}
              disabled={isBusy || isDirty || activeRunBlocked}
              disabledReason={
                activeRunBlocked
                  ? "当前 Vina 运行尚未结束。"
                  : isDirty
                    ? "请先保存当前 Box 与 Vina 参数，再切换协议或处理 maps。"
                    : isBusy
                      ? "当前运行流程尚未结束。"
                      : ""
              }
              onProjectChange={(nextProject) => {
                commitProject(nextProject, true);
              }}
              onStatusChange={() => {
                void refreshPreflight(true);
              }}
            />
          ) : null}

          {workspaceMode === "single" && isEvaluationMode ? (
            <section
              className={`run-pose-attestation ${poseInputConfirmed ? "is-confirmed" : "is-required"}`}
              aria-labelledby="pose-input-attestation-title"
            >
              <div className="run-pose-attestation-icon" aria-hidden="true">
                {poseInputConfirmed
                  ? <ShieldCheck size={22} weight="fill" />
                  : <WarningCircle size={22} weight="fill" />}
              </div>
              <div className="run-pose-attestation-copy">
                <div>
                  <h2 id="pose-input-attestation-title">确认输入姿势</h2>
                  <StatusBadge tone={poseInputConfirmed ? "ok" : "warning"}>
                    {poseInputConfirmed ? "用户已确认" : "运行前需要确认"}
                  </StatusBadge>
                </div>
                <p>
                  请在上方 3D 视图中确认配体已经位于受体中的待评价位置，并与受体使用同一坐标系。
                  DockStart 只记录你的确认，不会把几何显示当作科学验证。
                </p>
                <small>
                  {poseInputConfirmed
                    ? `确认时间 ${formatTime(poseInputAttestation?.confirmed_at ?? "")} · 记录已绑定当前${attestedFlexSha256 ? "运行受体、柔性侧链与配体" : "受体与配体"} SHA256`
                    : poseInputAttestation?.message || `确认会绑定当前${receptorMode === "flexible" ? "运行受体、柔性侧链与配体" : "受体与配体"} PDBQT 的 SHA256；重新导入或替换任一文件后自动失效。`}
                </small>
                {attestedReceptorSha256 && attestedLigandSha256 ? (
                  <dl className="run-pose-attestation-hashes">
                    <div><dt>运行受体</dt><dd>{attestedReceptorSha256.slice(0, 12)}…</dd></div>
                    {attestedFlexSha256 ? <div><dt>柔性侧链</dt><dd>{attestedFlexSha256.slice(0, 12)}…</dd></div> : null}
                    <div><dt>配体</dt><dd>{attestedLigandSha256.slice(0, 12)}…</dd></div>
                  </dl>
                ) : null}
              </div>
              <ActionButton
                variant={poseInputConfirmed ? "secondary" : "primary"}
                disabled={
                  isBusy
                  || activeRunBlocked
                  || isConfirmingPose
                  || !receptor
                  || !ligand
                }
                onClick={() => void confirmPoseInput()}
              >
                {isConfirmingPose
                  ? <SpinnerGap className="run-monitor-spinner" size={17} />
                  : <ShieldCheck size={17} />}
                {isConfirmingPose
                  ? "正在记录…"
                  : poseInputConfirmed
                    ? "重新确认当前文件"
                    : "确认当前输入姿势"}
              </ActionButton>
            </section>
          ) : null}

          <section className="run-cockpit-card run-settings-card">
            <div className="run-cockpit-section-heading">
              <div>
                <span className="run-cockpit-kicker">运行设置</span>
                <h2>{workspaceMode === "single" ? "输入、参数与输出" : "共享输入、参数与输出"}</h2>
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
                  <span>{workspaceMode === "single" ? "配体 PDBQT" : "当前预览配体"}</span>
                  <strong>{project.ligand.file || "未导入"}</strong>
                  <small>{workspaceMode !== "single"
                    ? ligand
                      ? `${ligand.atom_count.toLocaleString()} 原子 · ${
                        workspaceMode === "batch"
                          ? "其余配体在下方串行队列中管理"
                          : "共同对接的两个成员在下方面板中选择"
                      }`
                      : workspaceMode === "batch"
                        ? "用于 3D 预览；完整配体库在下方串行队列中管理"
                        : "用于 3D 预览；共同对接成员在下方面板中选择"
                    : ligand
                      ? `${ligand.atom_count.toLocaleString()} 原子 · PDBQT 活性扭转 ${ligand.torsdof ?? "未记录"}`
                      : "等待检查"}</small>
                  <button type="button" onClick={() => onNavigate("import-pdbqt")}>查看</button>
                </div>
              </div>

              <div className="run-ledger-group">
                <h3>{isAd4Maps ? "AutoDock4 运行参数" : `${vinaRunModeLabels[runMode]}参数`}</h3>
                <div className="run-task-mode-summary">
                  <span>任务类型</span>
                  <strong>{vinaRunModeLabels[runMode]}</strong>
                  <small>
                    {runMode === "dock"
                      ? "生成构象排名、scores.csv 与对接报告"
                      : runMode === "score_only"
                        ? "只读取当前姿势的评分和能量分解"
                        : "先记录输入评分，再输出 optimized.pdbqt 并比较前后变化"}
                  </small>
                </div>
                <div className="run-vina-fields">
                  <label>
                    <span>评分协议</span>
                    {isAd4Maps ? (
                      <input aria-label="评分协议" disabled value="AutoDock4 (maps)" />
                    ) : (
                      <select disabled={isBusy} value={vinaForm.scoring} onChange={(event) => updateVinaField("scoring", event.target.value)}>
                        <option value="vina">Vina</option>
                        <option value="vinardo">Vinardo</option>
                      </select>
                    )}
                    <small>{isAd4Maps ? "与 Vina / Vinardo 评分不可直接比较" : "Vina 与 Vinardo 分值不可直接比较"}</small>
                  </label>
                  {isEvaluationMode ? (
                    <label className="run-autobox-option">
                      <span>评价范围</span>
                      <span className="run-autobox-toggle">
                        <input
                          type="checkbox"
                          checked={!isAd4Maps && autobox}
                          disabled={isBusy || isAd4Maps}
                          onChange={(event) => {
                            setAutobox(event.target.checked);
                            setIsDirty(true);
                            dirtyRef.current = true;
                          }}
                        />
                        <strong>{isAd4Maps ? "由 AutoDock4 maps 定义" : autobox ? "按当前配体自动建立" : "使用项目 Box"}</strong>
                      </span>
                      <small>{isAd4Maps ? "预计算 maps 已固定网格范围" : "autobox 只用于仅评分和局部优化"}</small>
                    </label>
                  ) : null}
                  {vinaFields.filter((field) => runMode === "dock" || field.key === "cpu").map((field) => {
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
                <AdvancedDetails
                  className="run-vina-advanced"
                  summary={`高级设置 · ${advancedVinaCustomCount ? `已自定义 ${advancedVinaCustomCount} 项` : isAd4Maps ? "AutoDock4 默认" : "Vina 默认"}`}
                >
                    {isAd4Maps ? (
                      <p className="run-advanced-protocol-note">
                        AutoDock4 maps 不使用网格间距、体素取偶或受体原子精修选项。
                      </p>
                    ) : null}
                    <div className="run-advanced-groups">
                      {runMode === "dock" ? (
                        <section className="run-advanced-group run-advanced-group-search">
                          <h4>搜索与构象</h4>
                          <label className={!Number.isInteger(Number(vinaForm.max_evals)) || Number(vinaForm.max_evals) < 0 || Number(vinaForm.max_evals) > 2_147_483_647 ? "is-invalid" : ""}>
                            <span>每次搜索评估上限</span>
                            <input
                              disabled={isBusy}
                              inputMode="numeric"
                              value={vinaForm.max_evals}
                              onChange={(event) => updateVinaField("max_evals", event.target.value)}
                            />
                            <small>0 = Vina 自动</small>
                          </label>
                          <label className={!Number.isFinite(Number(vinaForm.min_rmsd)) || Number(vinaForm.min_rmsd) < 0 || Number(vinaForm.min_rmsd) > 100 ? "is-invalid" : ""}>
                            <span>构象最小间距</span>
                            <input
                              disabled={isBusy}
                              inputMode="decimal"
                              value={vinaForm.min_rmsd}
                              onChange={(event) => updateVinaField("min_rmsd", event.target.value)}
                            />
                            <small>默认 1 Å；仅全局对接</small>
                          </label>
                        </section>
                      ) : null}
                      {!isAd4Maps ? (
                        <section className="run-advanced-group">
                          <h4>网格计算</h4>
                          <label className={!Number.isFinite(Number(vinaForm.spacing)) || Number(vinaForm.spacing) < 0.1 || Number(vinaForm.spacing) > 2 ? "is-invalid" : ""}>
                            <span>网格间距</span>
                            <input
                              disabled={isBusy}
                              inputMode="decimal"
                              value={vinaForm.spacing}
                              onChange={(event) => updateVinaField("spacing", event.target.value)}
                            />
                            <small>默认 0.375 Å</small>
                          </label>
                          {forceEvenVoxelsApplicable ? (
                            <label className={`run-advanced-toggle ${forceEvenVoxelsControl.status}${forceEvenVoxelsControl.blocking ? " is-blocking" : ""}`}>
                              <span className="run-advanced-toggle-row">
                                <input
                                  type="checkbox"
                                  checked={vinaForm.force_even_voxels}
                                  disabled={forceEvenVoxelsControl.disabled}
                                  aria-invalid={forceEvenVoxelsControl.blocking}
                                  aria-describedby="force-even-voxels-hint force-even-voxels-capability"
                                  onChange={(event) => updateVinaToggle("force_even_voxels", event.target.checked)}
                                />
                                <span>网格体素数取偶数</span>
                              </span>
                              <small id="force-even-voxels-hint">可能调整实际网格边界</small>
                              <small id="force-even-voxels-capability" className="run-advanced-capability">
                                {capabilityChecking ? "正在检查当前 Vina" : forceEvenVoxelsControl.label}
                              </small>
                              {vinaForm.force_even_voxels ? (
                                <small className="run-advanced-active-note">实际网格边界可能随体素取整调整。</small>
                              ) : null}
                            </label>
                          ) : null}
                        </section>
                      ) : null}
                      <section className="run-advanced-group">
                        <h4>评分与日志</h4>
                        {!isAd4Maps && runMode === "score_only" && receptorMode === "flexible" ? (
                          <p className="run-advanced-protocol-note">柔性受体暂不开放自定义未结合态参考能量。</p>
                        ) : null}
                        {unboundEnergyApplicable ? (
                          <label
                            className={`run-advanced-capability-field ${unboundEnergyControl.status}${unboundEnergyControl.blocking ? " is-blocking" : ""}${hasUnboundEnergy && !Number.isFinite(Number(vinaForm.unbound_energy)) ? " is-invalid" : ""}`}
                          >
                            <span>显式未结合体系能量参考（kcal/mol）</span>
                            <input
                              type="text"
                              inputMode="decimal"
                              value={vinaForm.unbound_energy}
                              disabled={unboundEnergyControl.disabled}
                              aria-invalid={unboundEnergyControl.blocking || (hasUnboundEnergy && !Number.isFinite(Number(vinaForm.unbound_energy)))}
                              aria-describedby="unbound-energy-hint unbound-energy-capability"
                              placeholder="留空使用 Vina 默认"
                              onChange={(event) => updateVinaField("unbound_energy", event.target.value)}
                            />
                            <small id="unbound-energy-hint">仅用于当前姿势评分；空值由 Vina 计算</small>
                            <small id="unbound-energy-capability" className="run-advanced-capability">
                              {capabilityChecking ? "正在检查当前 Vina" : unboundEnergyControl.label}
                            </small>
                            {hasUnboundEnergy && Number.isFinite(Number(vinaForm.unbound_energy)) ? (
                              <small className="run-advanced-active-note">不是实验结合自由能；只与相同设置的结果比较。</small>
                            ) : null}
                          </label>
                        ) : null}
                        {noRefineApplicable ? (
                          <label className={`run-advanced-toggle ${noRefineControl.status}${noRefineControl.blocking ? " is-blocking" : ""}`}>
                            <span className="run-advanced-toggle-row">
                              <input
                                type="checkbox"
                                checked={vinaForm.no_refine}
                                disabled={noRefineControl.disabled}
                                aria-invalid={noRefineControl.blocking}
                                aria-describedby="no-refine-hint no-refine-capability"
                                onChange={(event) => updateVinaToggle("no_refine", event.target.checked)}
                              />
                              <span>关闭显式受体原子精修</span>
                            </span>
                            <small id="no-refine-hint">使用网格完成最终精修与评分</small>
                            <small id="no-refine-capability" className="run-advanced-capability">
                              {capabilityChecking ? "正在检查当前 Vina" : noRefineControl.label}
                            </small>
                            {vinaForm.no_refine ? (
                              <small className="run-advanced-active-note">已关闭显式受体原子精修；只与相同设置的结果比较。</small>
                            ) : null}
                          </label>
                        ) : null}
                        <label>
                          <span>日志详细程度</span>
                          <select
                            disabled={isBusy}
                            value={vinaForm.verbosity}
                            onChange={(event) => updateVinaField("verbosity", event.target.value)}
                          >
                            <option value="1">标准（1）</option>
                            <option value="2">详细（2）</option>
                          </select>
                          <small>详细模式会保存更多 Vina 诊断</small>
                        </label>
                      </section>
                    </div>
                    <div className="run-advanced-toolbar">
                      <div className="run-advanced-toolbar-copy">
                        <strong>
                          {isAd4Maps
                            ? preflight?.tool?.version
                              ? `Vina ${preflight.tool.version} · AutoDock4 maps`
                              : "AutoDock4 maps"
                            : advancedCapabilitySummary}
                        </strong>
                        <span>
                          {workspaceMode === "batch"
                            ? "适用值会写入项目配置，并冻结到新建的批量队列。"
                            : workspaceMode === "simultaneous"
                              ? "适用值会写入项目配置，并冻结到两个成员共享的联合 run。"
                            : "非默认值会写入项目配置与本次 run 快照。"}
                        </span>
                      </div>
                      <ActionButton
                        variant="text"
                        disabled={isBusy || advancedVinaCustomCount === 0}
                        onClick={resetAdvancedVina}
                      >
                        恢复 Vina 默认值
                      </ActionButton>
                    </div>
                </AdvancedDetails>
                <p className="run-science-note">
                  {runMode === "score_only"
                    ? "仅评分不会搜索新姿势，也不会生成构象排名或新的 PDBQT。"
                    : runMode === "local_only"
                      ? "局部优化只探索输入姿势附近，不等同于全局分子对接。"
                      : isAd4Maps
                    ? "当前运行使用预计算 AutoDock4 网格图；AD4、Vina 与 Vinardo 的分值不能直接横向比较。"
                    : "Vina 使用随机搜索与局部优化，而不是遗传算法。Vina 与 Vinardo 的分值不能直接横向比较。"}
                </p>
                {workspaceMode === "batch" ? <p className="run-batch-shared-note">这些 Box 与适用的 Vina 参数会冻结到每个配体任务中；显式未结合态参考能量不适用于批量全局对接。修改后需要重新创建队列。</p> : null}
                {workspaceMode === "simultaneous" ? <p className="run-batch-shared-note">这些 Box 与 Vina 参数会冻结到一个联合 run；两个成员不会分别运行，也不会产生可独立解释的成员评分。</p> : null}
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
                    disabled={isBusy || activeRunBlocked || !formIsValid}
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

            {!numericFormIsValid ? (
              <WarningCallout title="参数格式需要修正">
                <p>请检查 Box 尺寸、基础参数和高级设置中的数值范围。</p>
              </WarningCallout>
            ) : null}

            {expertCapabilityBlocked ? (
              <WarningCallout title="专家选项尚不可用">
                <p>已启用的选项未通过当前 Vina 能力检查。请关闭标记的选项，或重新检查工具。</p>
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

            {workspaceMode === "single" && (runtime || running || stage === "finished" || stage === "failed" || stage === "cancelled") ? (
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

            {workspaceMode === "single" ? <div className="run-action-bar">
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
                <div className="run-split-action" role="group" aria-label={`${vinaRunModeLabels[runMode]}执行操作`}>
                  <ActionButton
                    className="run-split-action-main"
                    variant="primary"
                    disabled={isBusy || !formIsValid || !preflight?.ready}
                    onClick={() => void runWorkflow()}
                  >
                    {isBusy ? <SpinnerGap className="run-monitor-spinner" size={18} /> : <Play size={18} weight="fill" />}
                    {isBusy ? "运行中…" : actionMode === "full" ? vinaRunModeActions[runMode] : runActionLabels[actionMode]}
                  </ActionButton>
                  <span
                    className="run-split-action-selector"
                    title={actionMode === "full" && runMode !== "dock" ? `${vinaRunModeLabels[runMode]}、解析与报告` : runActionDescriptions[actionMode]}
                  >
                    <select
                      aria-label="选择执行方式"
                      value={actionMode}
                      disabled={isBusy}
                      onChange={(event) => setActionMode(event.target.value as RunActionMode)}
                    >
                      <option value="full">
                        {runMode === "dock"
                          ? runActionDescriptions.full
                          : runMode === "score_only"
                            ? "完整流程：评分、解析、报告"
                            : "完整流程：局部优化、解析、报告"}
                      </option>
                      <option value="prepare">{runActionDescriptions.prepare}</option>
                      <option value="config">{runActionDescriptions.config}</option>
                    </select>
                    <CaretDown aria-hidden="true" size={15} weight="bold" />
                  </span>
                </div>
              </div>
            </div> : null}
            {workspaceMode === "single" && message && !runtime ? <p className="run-inline-message" role={stage === "failed" ? "alert" : "status"}>{message}</p> : null}
            {rawError ? <AdvancedDetails summary="查看原始诊断"><pre>{rawError}</pre></AdvancedDetails> : null}
          </section>

          {workspaceMode === "single" && (!isAd4Maps || isStandardAd4Maps) ? <FlexibleReceptorPanel
            project={project}
            disabled={isBusy || activeRunBlocked || isDirty}
            pickedResidue={pickedResidue.selector}
            pickedResidueToken={pickedResidue.token}
            pickedResidueContextSha256={pickedResidue.selectionContextSha256}
            selectionActive={residueSelectionActive}
            onSelectionActiveChange={setResidueSelectionActive}
            onResiduesChange={setFlexibleResidues}
            onIdentityContextChange={handleFlexibleIdentityContext}
            onProjectChange={(nextProject) => {
              commitProject(nextProject, true);
              void refreshPreflight(true);
            }}
          /> : null}

          {workspaceMode === "batch" ? (
            <BatchScreeningPanel
              projectDir={project.project_dir}
              receptorFile={project.receptor.file}
              box={project.box}
              vina={project.vina}
              scoringProtocol={project.docking_protocol?.engine}
              disabled={isBusy || isDirty || !formIsValid || !preflight?.ready || !project.receptor.file || receptorMode === "flexible" || (isAd4Maps && !isStandardAd4Maps) || isEvaluationMode}
              disabledReason={isEvaluationMode
                ? "串行批量筛选只执行全局对接。请先将任务类型切换为“全局对接”。"
                : isAd4Maps && !isStandardAd4Maps
                ? "串行批量 AD4 仅支持标准 AutoDock4 maps；AD4Zn 与水合协议仍使用各自的单配体工作流。"
                : receptorMode === "flexible"
                ? "串行批量筛选当前仅支持刚性受体。请切回刚性受体后创建或恢复队列；系统不会静默改用旧受体。"
                : isBusy
                ? "当前单次对接流程正在运行，暂不能创建新的筛选队列。"
                : isDirty
                  ? "请先保存当前 Box 与 Vina 参数，再创建可复现的筛选快照。"
                  : !project.receptor.file
                    ? "请先导入受体 PDBQT。"
                    : !formIsValid
                      ? "请先修正 Box 与 Vina 参数。"
                      : !preflight?.ready
                        ? "请先处理右侧运行前检查中的阻塞项。"
                    : ""}
            />
          ) : null}

          {workspaceMode === "simultaneous" ? (
            <MultiLigandDockingPanel
              projectDir={project.project_dir}
              receptorFile={project.receptor.file}
              box={project.box}
              vina={project.vina}
              scoringProtocol={project.docking_protocol?.engine}
              compatibilityIssues={multipleLigandIssues}
              disabled={
                isBusy
                || isDirty
                || !formIsValid
                || (!preflight?.ready && !latestJointRunOwnsActiveGuard)
                || !project.receptor.file
                || (activeRunBlocked && !latestJointRunOwnsActiveGuard)
              }
              disabledReason={activeRunBlocked && !latestJointRunOwnsActiveGuard
                ? "当前项目已有未完成的 Vina 运行，请先恢复或结束该任务。"
                : isBusy
                  ? "当前单配体流程尚未结束。"
                  : isDirty
                    ? "请先保存当前 Box 与 Vina 参数，再冻结共同对接输入。"
                    : !project.receptor.file
                      ? "请先导入受体 PDBQT。"
                      : !formIsValid
                        ? "请先修正 Box 与 Vina 参数。"
                        : !preflight?.ready && !latestJointRunOwnsActiveGuard
                          ? "请先处理右侧运行前检查中的阻塞项。"
                          : ""}
              initialRunId={latestJointRunId}
              onOpenImport={() => onNavigate("import-pdbqt")}
              onOpenResult={(runId) => onOpenResultPage(project, runId)}
              onStatusChange={() => {
                void refreshPreflight(true);
              }}
            />
          ) : null}

          {workspaceMode === "single" ? <section className="run-cockpit-card run-history-card">
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
                  <thead><tr><th>Run</th><th>任务 / 协议</th><th>状态</th><th>开始时间</th><th>耗时</th><th>主要结果</th><th>操作</th></tr></thead>
                  <tbody>
                    {history.slice(0, 8).map((run) => (
                      <tr key={run.run_id}>
                        <td><strong>{run.run_id}</strong></td>
                        <td>
                          {vinaRunModeLabels[run.run_mode ?? "dock"]} · {run.scoring_protocol === "ad4_maps" ? "AD4 maps" : run.scoring_function === "vinardo" ? "Vinardo" : "Vina"}
                        </td>
                        <td><StatusBadge tone={statusTone(run.status)}>{run.status}</StatusBadge></td>
                        <td>{formatTime(run.started_at || run.created_at)}</td>
                        <td>{formatDuration(run.duration_seconds)}</td>
                        <td>{run.run_mode && run.run_mode !== "dock" ? run.primary_score_kcal_mol ?? "—" : run.best_affinity ?? "—"}</td>
                        <td>
                          {run.status === "finished" ? <button type="button" onClick={() => onOpenResultPage(project, run.run_id)}>查看结果</button> : <button type="button" onClick={() => onOpenRunExecute(project, run.run_id)}>运行详情</button>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p className="run-history-empty">尚无运行记录。首次完整运行后可查看状态、耗时和最佳评分。</p>}
          </section> : null}
        </main>

        <aside className="run-preflight-rail" aria-label="运行前检查">
          <header>
            <div>
              <span>Preflight</span>
              <h2>
                {workspaceMode === "batch"
                  ? "串行筛选运行前检查"
                  : workspaceMode === "simultaneous"
                    ? "共同对接运行前检查"
                    : "运行前检查"}
              </h2>
            </div>
            <button type="button" disabled={isRefreshing || isBusy || activeRunBlocked} onClick={() => void (isDirty ? saveAndRefresh() : refreshPreflight())}>
              <ArrowRight className={isRefreshing ? "run-monitor-spinner" : ""} size={16} /> {isDirty ? "保存并检查" : "重新检查"}
            </button>
          </header>

          <section className={`run-readiness-summary ${preflight?.ready && !isDirty ? "ready" : "blocked"}`}>
            {preflight?.ready && !isDirty ? <CheckCircle size={25} weight="fill" /> : <WarningCircle size={25} weight="fill" />}
            <div>
              <strong>
                {isDirty
                  ? "屏幕参数尚未保存"
                  : preflight?.ready
                    ? workspaceMode === "batch"
                      ? "串行队列共享条件已满足"
                      : workspaceMode === "simultaneous"
                        ? multipleLigandIssues.length
                          ? "共同对接协议仍有阻塞项"
                          : "共同运行条件已满足"
                        : "执行条件已满足"
                    : preflight
                      ? "仍有阻塞项"
                      : "正在检查"}
              </strong>
              <p>
                {isDirty
                  ? "请先保存任务类型、范围与 Vina 参数。"
                  : preflight?.ready
                    ? workspaceMode === "batch"
                      ? "受体、Box、Vina 与输出环境可用；配体队列状态请看主工作区。"
                      : workspaceMode === "simultaneous"
                        ? multipleLigandIssues.length
                          ? multipleLigandIssues.join("；")
                          : "受体、Box、Vina 与输出环境可用；请在主工作区选择两个 staging 配体。"
                        : "表示当前文件与环境可执行，不代表结构方案科学正确。"
                    : preflight?.blockers?.[0] || "正在读取本机状态。"}
              </p>
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
            <p>
              {workspaceMode === "simultaneous"
                ? "联合评分描述两个成员与受体构成的整体体系，不能拆分成单个成员贡献，也不能替代实验验证。"
                : isEvaluationMode
                  ? "姿势评分只描述当前输入或其局部优化结果，不能替代全局搜索或实验验证。"
                  : "Docking score 仅供结构结合趋势参考，不能替代实验验证。"}
            </p>
          </section>
        </aside>
      </div>
    </section>
  );
}
