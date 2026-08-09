import { lazy, Suspense, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { CheckCircle, FileArrowUp, Info } from "@phosphor-icons/react";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import CommandResultPanel from "../components/CommandResultPanel";
import MacrocycleBondSelector from "../components/MacrocycleBondSelector";
import { BodyGrid, MainPanel, PageHero, PageShell, RightRail, RightRailSection } from "../components/layout/PageLayout";
import OperationLoadingDialog from "../components/OperationLoadingDialog";
import ScientificDisclaimer from "../components/ScientificDisclaimer";
import StatusBadge from "../components/StatusBadge";
import type {
  DockStartProject,
  MacrocyclePreparationEvidence,
  MacrocycleReviewOptions,
  MacrocycleSelection,
  MacrocycleStatusResponse,
  PreparationResult,
  PreparationStatusResponse,
  PreparationTarget,
  PreparationToolCapabilityResult,
  RunFileStatus,
  StructureReviewPayload,
  ToolCheckResult,
} from "../types";
import {
  cancelBackgroundTask,
  findActiveBackgroundTask,
  startPreparationTask,
  waitForBackgroundTask,
  type BackgroundTaskStatus,
} from "../utils/backgroundTasks";
import {
  buildReviewedMacrocyclePreparationOptions,
  createMacrocycleApi,
  defaultMacrocycleReviewOptions,
  extractMacrocyclePreparationEvidence,
  normalizeMacrocycleReviewOptions,
  selectionFromMacrocycleStatus,
} from "../utils/macrocyclePreparation";
import {
  normalizeLigandImportPreview,
  type LigandImportCandidate,
  type LigandImportPreview,
} from "../utils/screeningLigandImport";
import { readDockingWorkspaceMode, writeDockingWorkspaceMode } from "../utils/dockingMode";
import { structureInputKind } from "../utils/structureInput";

const StructureMiniPreview = lazy(() => import("../components/StructureMiniPreview"));

type MacrocyclePreparationMode = "standard" | "reviewed";

const macrocycleApi = createMacrocycleApi(
  (command, args) => invoke<string>(command, args),
);

type PreparationPageProps = {
  project: DockStartProject;
  onBack: () => void;
  onOpenBoxSetup: (project: DockStartProject) => void;
  onProjectChange: (project: DockStartProject) => void;
};

function parsePreparationResponse(rawPayload: string): PreparationStatusResponse {
  const parsed = JSON.parse(rawPayload) as Partial<PreparationStatusResponse>;
  return {
    ok: Boolean(parsed.ok),
    project_dir: parsed.project_dir ?? "",
    project: parsed.project ?? null,
    preparation: parsed.preparation ?? null,
    structure_review: parsed.structure_review,
    tools: parsed.tools,
    files: parsed.files,
    target: parsed.target,
    ready: parsed.ready,
    missing_tools: parsed.missing_tools ?? [],
    message: parsed.message,
    error: parsed.error,
  };
}

function statusLabel(status: string | undefined): string {
  const labels: Record<string, string> = {
    ok: "可用",
    missing: "缺失",
    error: "失败",
    unknown: "需检查",
    not_started: "未开始",
    checking: "进行中",
    ready: "可进行",
    running: "进行中",
    finished: "已完成",
    failed: "失败",
    empty: "需检查",
  };
  return labels[status ?? ""] ?? "需检查";
}

function statusTone(status: string | undefined): "ok" | "warning" | "error" | "muted" | "info" {
  if (status === "ok" || status === "ready" || status === "finished") return "ok";
  if (status === "running" || status === "checking") return "info";
  if (status === "failed" || status === "error") return "error";
  if (status === "missing" || status === "empty") return "warning";
  return "muted";
}

function fileLine(file: RunFileStatus | undefined, fallback: string): string {
  if (!file) return fallback || "未记录";
  return file.path || fallback || "未记录";
}

function toolVersion(tool: ToolCheckResult | PreparationToolCapabilityResult | undefined): string {
  return tool?.version || "未获取版本";
}

function capabilityLine(tool: PreparationToolCapabilityResult | undefined, capabilityKey: string): string {
  const capability = tool?.capabilities?.[capabilityKey];
  if (!capability) return "未检测";
  return `${statusLabel(capability.status)} · ${capability.message}`;
}

function sourceIdentityLabel(value: string): string {
  const normalized = value.trim();
  if (!normalized) return "";
  const isFilesystemPath = /^[a-zA-Z]:[\\/]/.test(normalized)
    || /^\\\\/.test(normalized)
    || normalized.startsWith("/");
  if (!isFilesystemPath) return normalized;
  return normalized.split(/[\\/]/).filter(Boolean).pop() || normalized;
}

function factRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function factNumber(facts: Record<string, unknown>, key: string): number | null {
  const value = facts[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function factBooleanLabel(value: unknown, unknownLabel = "无法可靠判定"): string {
  if (value === true) return "是";
  if (value === false) return "否";
  return unknownLabel;
}

function formalChargeLabel(value: number | null): string {
  if (value === null) return "未可靠记录";
  return value > 0 ? `+${value}` : String(value);
}

const RECEPTOR_RAW_REQUIRED = "需原始 PDB/mmCIF；当前文件化学信息不足";

function factArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function factStringList(value: unknown): string {
  const items = factArray(value).map((item) => String(item)).filter(Boolean);
  return items.length ? items.join("、") : "无";
}

function coordinateBoundsLabel(value: unknown): string {
  const bounds = factRecord(value);
  const labels = (["x", "y", "z"] as const).map((axis) => {
    const range = factArray(bounds[axis]);
    return range.length === 2 ? `${axis.toUpperCase()} ${range[0]}–${range[1]} Å` : "";
  }).filter(Boolean);
  return labels.length === 3 ? labels.join(" · ") : "PDBQT 坐标列无法完整解析";
}

function FactValue({ children, source }: { children: ReactNode; source: string }) {
  return (
    <dd
      aria-label={`${String(children)}；数据来源：${source}`}
      className="preparation-fact-value"
    >
      <span className="preparation-fact-text">{children}</span>
    </dd>
  );
}

export default function PreparationPage({
  project: initialProject,
  onBack,
  onOpenBoxSetup,
  onProjectChange,
}: PreparationPageProps) {
  const [project, setProject] = useState(initialProject);
  const [response, setResponse] = useState<PreparationStatusResponse | null>(null);
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [overwriteReceptor, setOverwriteReceptor] = useState(false);
  const [overwriteLigand, setOverwriteLigand] = useState(false);
  const [badResidueReviewConfirmed, setBadResidueReviewConfirmed] = useState(false);
  const [receptorAltlocSelections, setReceptorAltlocSelections] = useState<Record<string, string>>({});
  const [macrocycleMode, setMacrocycleMode] = useState<MacrocyclePreparationMode>("standard");
  const [macrocycleOptions, setMacrocycleOptions] = useState<MacrocycleReviewOptions>(
    defaultMacrocycleReviewOptions,
  );
  const [macrocycleStatus, setMacrocycleStatus] = useState<MacrocycleStatusResponse | null>(null);
  const [macrocycleSelection, setMacrocycleSelection] = useState<MacrocycleSelection>(null);
  const [macrocycleEvidence, setMacrocycleEvidence] = useState<MacrocyclePreparationEvidence | null>(null);
  const [isMacrocycleBusy, setIsMacrocycleBusy] = useState(false);
  const [previewRevision, setPreviewRevision] = useState<Record<PreparationTarget, number>>({ receptor: 0, ligand: 0 });
  const [previewRequested, setPreviewRequested] = useState<Record<PreparationTarget, boolean>>({ receptor: false, ligand: false });
  const [tools, setTools] = useState<PreparationStatusResponse["tools"]>();
  const [isCheckingTools, setIsCheckingTools] = useState(false);
  const [batchLigandPreview, setBatchLigandPreview] = useState<LigandImportPreview | null>(null);
  const [selectedBatchLigandId, setSelectedBatchLigandId] = useState("");
  const [pendingTarget, setPendingTarget] = useState<PreparationTarget | null>(null);
  const [activeTask, setActiveTask] = useState<BackgroundTaskStatus | null>(null);
  const activeTaskAbortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  const macrocycleRequestRef = useRef(0);
  const statusRequestRef = useRef(0);
  const currentProjectDirRef = useRef(initialProject.project_dir);
  const preparedIdentityRef = useRef(`${initialProject.receptor.file}|${initialProject.ligand.file}`);
  const rawReceptorIdentityRef = useRef(`${initialProject.project_dir}|${initialProject.receptor.raw_file}`);
  const rawLigandIdentityRef = useRef(`${initialProject.project_dir}|${initialProject.ligand.raw_file}`);

  useEffect(() => {
    if (currentProjectDirRef.current !== initialProject.project_dir) {
      currentProjectDirRef.current = initialProject.project_dir;
      statusRequestRef.current += 1;
      setBatchLigandPreview(null);
      setSelectedBatchLigandId("");
    }
    const nextIdentity = `${initialProject.receptor.file}|${initialProject.ligand.file}`;
    if (preparedIdentityRef.current !== nextIdentity) {
      const [previousReceptor, previousLigand] = preparedIdentityRef.current.split("|");
      preparedIdentityRef.current = nextIdentity;
      setPreviewRevision((revision) => ({
        receptor: previousReceptor !== initialProject.receptor.file ? revision.receptor + 1 : revision.receptor,
        ligand: previousLigand !== initialProject.ligand.file ? revision.ligand + 1 : revision.ligand,
      }));
    }
    const nextRawReceptorIdentity = `${initialProject.project_dir}|${initialProject.receptor.raw_file}`;
    if (rawReceptorIdentityRef.current !== nextRawReceptorIdentity) {
      rawReceptorIdentityRef.current = nextRawReceptorIdentity;
      setBadResidueReviewConfirmed(false);
      setReceptorAltlocSelections({});
    }
    const nextRawLigandIdentity = `${initialProject.project_dir}|${initialProject.ligand.raw_file}`;
    if (rawLigandIdentityRef.current !== nextRawLigandIdentity) {
      rawLigandIdentityRef.current = nextRawLigandIdentity;
      macrocycleRequestRef.current += 1;
      setMacrocycleStatus(null);
      setMacrocycleSelection(null);
      setMacrocycleEvidence(null);
      setMacrocycleMode("standard");
    }
    setProject(initialProject);
  }, [initialProject]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      activeTaskAbortRef.current?.abort();
    };
  }, []);

  const applyResponse = useCallback(
    (next: PreparationStatusResponse, fallbackMessage: string, completedTarget?: PreparationTarget) => {
      if (!mountedRef.current) return;
      setResponse(next);
      if (next.tools) setTools(next.tools);
      if (next.project) {
        const nextIdentity = `${next.project.receptor.file}|${next.project.ligand.file}`;
        if (completedTarget || preparedIdentityRef.current !== nextIdentity) {
          const [previousReceptor, previousLigand] = preparedIdentityRef.current.split("|");
          preparedIdentityRef.current = nextIdentity;
          setPreviewRevision((revision) => ({
            receptor:
              completedTarget === "receptor" || previousReceptor !== next.project?.receptor.file
                ? revision.receptor + 1
                : revision.receptor,
            ligand:
              completedTarget === "ligand" || previousLigand !== next.project?.ligand.file
                ? revision.ligand + 1
                : revision.ligand,
          }));
        }
        setProject(next.project);
        onProjectChange(next.project);
      }
      setMessage(next.message ?? next.error?.message ?? fallbackMessage);
      setRawError(next.error?.raw_error ?? "");
      if (completedTarget === "receptor") {
        setBadResidueReviewConfirmed(false);
        setReceptorAltlocSelections({});
      }
    },
    [onProjectChange],
  );

  const waitForPreparation = useCallback(
    async (started: BackgroundTaskStatus, target: PreparationTarget, controller: AbortController) => {
      if (!mountedRef.current) return;
      setActiveTask(started);
      const completed = await waitForBackgroundTask(
        started.task_id,
        (task) => {
          if (!mountedRef.current) return;
          setActiveTask(task);
          setMessage(task.progress.message || task.message);
          if (task.error) setRawError(task.error);
        },
        controller.signal,
      );
      if (!mountedRef.current) return;
      setActiveTask(completed);
      if (completed.status === "cancelled") {
        setMessage("排队中的结构准备任务已取消。");
        return;
      }
      if (!completed.result_json) {
        throw new Error(completed.error || completed.message || "结构准备后台任务没有返回结果。");
      }
      const preparationResult = parsePreparationResponse(completed.result_json);
      applyResponse(
        preparationResult,
        target === "receptor" ? "受体输入已准备。" : "配体输入已准备。",
        preparationResult.ok ? target : undefined,
      );
    },
    [applyResponse],
  );

  useEffect(() => {
    const controller = new AbortController();
    let disposed = false;
    let resumedTaskId = "";
    const reconnect = async () => {
      try {
        const existing = await findActiveBackgroundTask(initialProject.project_dir, { kind: "preparation" });
        if (!existing || disposed) return;
        resumedTaskId = existing.task_id;
        activeTaskAbortRef.current?.abort();
        activeTaskAbortRef.current = controller;
        setIsBusy(true);
        setMessage("检测到未完成的结构准备任务，正在恢复进度显示。");
        await waitForPreparation(existing, existing.target === "receptor" ? "receptor" : "ligand", controller);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        if (!disposed) {
          setMessage("无法恢复结构准备任务状态。");
          setRawError(error instanceof Error ? error.message : String(error));
        }
      } finally {
        if (!disposed) {
          setActiveTask((current) => (current?.task_id === resumedTaskId ? null : current));
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
  }, [initialProject.project_dir, waitForPreparation]);

  const reloadStatus = useCallback(async () => {
    if (!mountedRef.current) return;
    const requestId = ++statusRequestRef.current;
    const requestedProjectDir = project.project_dir;
    setIsBusy(true);
    try {
      const [rawPayload, rawReviewPayload, rawScreeningPayload] = await Promise.all([
        invoke<string>("get_preparation_status", { projectDir: project.project_dir }),
        invoke<string>("get_structure_review", { projectDir: project.project_dir }),
        invoke<string>("get_screening_status", { projectDir: project.project_dir }),
      ]);
      const parsed = parsePreparationResponse(rawPayload);
      const reviewResponse = JSON.parse(rawReviewPayload) as {
        ok?: boolean;
        structure_review?: StructureReviewPayload;
      };
      if (
        !mountedRef.current
        || requestId !== statusRequestRef.current
        || requestedProjectDir !== project.project_dir
      ) return;
      if (reviewResponse.ok && reviewResponse.structure_review) {
        parsed.structure_review = reviewResponse.structure_review;
      }
      try {
        const screeningResponse = JSON.parse(rawScreeningPayload) as Record<string, unknown>;
        const preview = normalizeLigandImportPreview(screeningResponse);
        const selectedCandidateIds = new Set(
          Array.isArray(screeningResponse.staged)
            ? screeningResponse.staged.flatMap((item) => {
                if (!item || typeof item !== "object" || Array.isArray(item)) return [];
                const candidateId = String((item as Record<string, unknown>).candidate_id || "");
                return candidateId ? [candidateId] : [];
              })
            : [],
        );
        const viewableCandidates = preview.candidates.filter((candidate) => {
          if (!candidate.stagedFile) return false;
          if (candidate.status === "ready") {
            return selectedCandidateIds.size === 0 || selectedCandidateIds.has(candidate.id);
          }
          if (candidate.status === "duplicate") {
            return selectedCandidateIds.size === 0
              || Boolean(candidate.duplicateOf && selectedCandidateIds.has(candidate.duplicateOf));
          }
          return false;
        });
        const readyCandidates = viewableCandidates.filter((candidate) => candidate.status === "ready");
        const activeLigandSource = String(parsed.project?.ligand.source_id || "").trim().toLowerCase();
        const activeLigandMatches = Boolean(activeLigandSource) && viewableCandidates.some((candidate) => (
          [candidate.displayName, candidate.originalName]
            .map((value) => value.trim().toLowerCase())
            .filter(Boolean)
            .some((value) => activeLigandSource === value || activeLigandSource.includes(value))
        ));
        const batchModeIsCurrent = screeningResponse.mode === "batch"
          && readDockingWorkspaceMode(project.project_dir) === "batch"
          && activeLigandMatches;
        if (batchModeIsCurrent && readyCandidates.length > 1 && preview.revisionSha256) {
          setBatchLigandPreview({ ...preview, candidates: viewableCandidates });
          setSelectedBatchLigandId((current) => (
            viewableCandidates.some((candidate) => candidate.id === current)
              ? current
              : viewableCandidates[0].id
          ));
        } else {
          setBatchLigandPreview(null);
          setSelectedBatchLigandId("");
        }
      } catch {
        setBatchLigandPreview(null);
        setSelectedBatchLigandId("");
      }
      applyResponse(parsed, "准备状态已刷新。");
    } catch (error) {
      if (!mountedRef.current) return;
      setMessage("无法读取准备状态。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      if (mountedRef.current) setIsBusy(false);
    }
  }, [applyResponse, project.project_dir]);

  const pickStructureFile = useCallback(async (target: PreparationTarget) => {
    const isReceptor = target === "receptor";
    setMessage("");
    setRawError("");
    try {
      const selected = await open({
        directory: false,
        multiple: false,
        title: isReceptor ? "选择受体结构" : "选择配体结构",
        filters: [{
          name: isReceptor ? "受体结构" : "配体结构",
          extensions: isReceptor ? ["pdbqt", "pdb", "cif"] : ["pdbqt", "sdf", "mol", "mol2"],
        }],
      });
      const sourcePath = Array.isArray(selected) ? selected[0] ?? "" : selected ?? "";
      if (!sourcePath) return;
      const kind = structureInputKind(sourcePath, target);
      if (kind === "unsupported") {
        setMessage(isReceptor
          ? "受体格式不受支持。请选择 PDBQT、PDB 或 CIF。"
          : "配体格式不受支持。请选择 PDBQT、SDF、MOL 或 MOL2。");
        return;
      }
      setIsBusy(true);
      const command = target === "receptor"
        ? kind === "pdbqt" ? "import_receptor_pdbqt" : "import_receptor_raw_file"
        : kind === "pdbqt" ? "import_ligand_pdbqt" : "import_ligand_raw_file";
      const rawPayload = await invoke<string>(command, {
        projectDir: project.project_dir,
        sourcePath,
        ...(kind === "pdbqt" ? { sourceLabel: sourcePath.split(/[\\/]/).pop() || sourcePath } : {}),
      });
      const parsed = JSON.parse(rawPayload) as {
        ok?: boolean;
        project?: DockStartProject | null;
        message?: string;
        error?: { message?: string; raw_error?: string };
      };
      if (!parsed.ok || !parsed.project) {
        setMessage(parsed.error?.message || `${isReceptor ? "受体" : "配体"}导入失败。`);
        setRawError(parsed.error?.raw_error || "");
        return;
      }
      setProject(parsed.project);
      onProjectChange(parsed.project);
      if (target === "ligand") {
        writeDockingWorkspaceMode(project.project_dir, "single");
        setBatchLigandPreview(null);
        setSelectedBatchLigandId("");
      }
      if (target === "receptor") {
        setBadResidueReviewConfirmed(false);
        setReceptorAltlocSelections({});
      }
      setPreviewRequested((current) => ({ ...current, [target]: false }));
      setMessage(kind === "pdbqt"
        ? `${isReceptor ? "受体" : "配体"} PDBQT 已导入，可直接预览。`
        : `${isReceptor ? "受体" : "配体"}原始结构已导入，请检查后转换为 PDBQT。`);
      await reloadStatus();
    } catch (error) {
      setMessage(`无法导入${isReceptor ? "受体" : "配体"}结构。`);
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      if (mountedRef.current) setIsBusy(false);
    }
  }, [onProjectChange, project.project_dir, reloadStatus]);

  useEffect(() => {
    void reloadStatus();
  }, [reloadStatus]);

  useEffect(() => {
    const errorCode = response?.preparation?.ligand.error?.code ?? response?.error?.code;
    if (errorCode === "MACROCYCLE_REVIEW_REQUIRED") setMacrocycleMode("reviewed");
  }, [response?.error?.code, response?.preparation?.ligand.error?.code]);

  const applyMacrocycleStatus = useCallback((
    next: MacrocycleStatusResponse,
    fallbackMessage: string,
    selection?: MacrocycleSelection,
  ) => {
    if (!mountedRef.current) return;
    setMacrocycleStatus(next);
    if (next.ok) {
      setMacrocycleSelection(selection ?? selectionFromMacrocycleStatus(next));
      if (next.project) {
        setProject(next.project);
        onProjectChange(next.project);
      }
    }
    setMessage(next.message || next.error?.message || fallbackMessage);
    setRawError(next.error?.raw_error || "");
  }, [onProjectChange]);

  const refreshMacrocycleStatus = useCallback(async () => {
    const requestId = ++macrocycleRequestRef.current;
    try {
      const next = await macrocycleApi.getStatus(project.project_dir);
      if (!mountedRef.current || requestId !== macrocycleRequestRef.current) return;
      setMacrocycleStatus(next);
      if (next.ok) setMacrocycleSelection(selectionFromMacrocycleStatus(next));
    } catch (error) {
      if (!mountedRef.current || requestId !== macrocycleRequestRef.current) return;
      setMacrocycleStatus(null);
      setMacrocycleSelection(null);
      setRawError(error instanceof Error ? error.message : String(error));
    }
  }, [project.project_dir, project.ligand.raw_file]);

  useEffect(() => {
    void refreshMacrocycleStatus();
  }, [refreshMacrocycleStatus]);

  const resetMacrocycleConfirmation = useCallback(async (
    fallbackMessage = "大环确认已撤销。",
    selectionAfterReset: MacrocycleSelection = macrocycleSelection,
  ) => {
    const requestId = ++macrocycleRequestRef.current;
    setIsMacrocycleBusy(true);
    setMacrocycleStatus((current) => current
      ? { ...current, state: "reviewed", can_prepare: false, confirmation: null }
      : current);
    try {
      const next = await macrocycleApi.resetConfirmation(project.project_dir);
      if (!mountedRef.current || requestId !== macrocycleRequestRef.current) return;
      applyMacrocycleStatus(next, fallbackMessage, selectionAfterReset);
    } catch (error) {
      if (!mountedRef.current || requestId !== macrocycleRequestRef.current) return;
      setMessage("无法撤销大环确认。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      if (mountedRef.current && requestId === macrocycleRequestRef.current) {
        setIsMacrocycleBusy(false);
      }
    }
  }, [applyMacrocycleStatus, macrocycleSelection, project.project_dir]);

  const invalidateMacrocycleConfirmation = useCallback((
    nextSelection: MacrocycleSelection,
    fallbackMessage: string,
  ) => {
    setMacrocycleSelection(nextSelection);
    if (macrocycleStatus?.confirmation?.valid) {
      void resetMacrocycleConfirmation(fallbackMessage, nextSelection);
    }
  }, [macrocycleStatus?.confirmation?.valid, resetMacrocycleConfirmation]);

  const changeMacrocycleMode = (nextMode: MacrocyclePreparationMode) => {
    setMacrocycleMode(nextMode);
    if (nextMode === "standard") {
      invalidateMacrocycleConfirmation(null, "已切换到标准准备；大环确认已撤销。");
    }
  };

  const changeMacrocycleOptions = (nextOptions: MacrocycleReviewOptions) => {
    const normalized = normalizeMacrocycleReviewOptions(nextOptions);
    setMacrocycleOptions(normalized);
    invalidateMacrocycleConfirmation(
      macrocycleSelection?.kind === "candidate" ? macrocycleSelection : null,
      "分析参数已变化；原大环确认已撤销。",
    );
  };

  const reviewMacrocycle = async () => {
    const requestId = ++macrocycleRequestRef.current;
    setIsMacrocycleBusy(true);
    setRawError("");
    setMessage("正在分析大环断环候选。");
    try {
      const next = await macrocycleApi.review(project.project_dir, macrocycleOptions);
      if (!mountedRef.current || requestId !== macrocycleRequestRef.current) return;
      applyMacrocycleStatus(next, "大环候选分析完成。");
    } catch (error) {
      if (!mountedRef.current || requestId !== macrocycleRequestRef.current) return;
      setMessage("无法分析大环断环候选。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      if (mountedRef.current && requestId === macrocycleRequestRef.current) {
        setIsMacrocycleBusy(false);
      }
    }
  };

  const confirmMacrocycleCandidate = async () => {
    const reviewId = macrocycleStatus?.review?.review_id || "";
    const candidateId = macrocycleSelection?.kind === "candidate"
      ? macrocycleSelection.candidateId
      : "";
    if (!reviewId || !candidateId) return;
    const requestId = ++macrocycleRequestRef.current;
    setIsMacrocycleBusy(true);
    setRawError("");
    try {
      const next = await macrocycleApi.confirmCandidate(
        project.project_dir,
        reviewId,
        candidateId,
      );
      if (!mountedRef.current || requestId !== macrocycleRequestRef.current) return;
      applyMacrocycleStatus(
        next,
        "断环组合已确认。",
        { kind: "candidate", candidateId },
      );
    } catch (error) {
      if (!mountedRef.current || requestId !== macrocycleRequestRef.current) return;
      setMessage("无法确认断环组合。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      if (mountedRef.current && requestId === macrocycleRequestRef.current) {
        setIsMacrocycleBusy(false);
      }
    }
  };

  const confirmRigidMacrocycle = async () => {
    const reviewId = macrocycleStatus?.review?.review_id || "";
    if (!reviewId) return;
    const requestId = ++macrocycleRequestRef.current;
    setIsMacrocycleBusy(true);
    setRawError("");
    try {
      const next = await macrocycleApi.confirmRigid(project.project_dir, reviewId);
      if (!mountedRef.current || requestId !== macrocycleRequestRef.current) return;
      applyMacrocycleStatus(next, "刚性大环已确认。", { kind: "rigid" });
    } catch (error) {
      if (!mountedRef.current || requestId !== macrocycleRequestRef.current) return;
      setMessage("无法确认刚性大环。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      if (mountedRef.current && requestId === macrocycleRequestRef.current) {
        setIsMacrocycleBusy(false);
      }
    }
  };

  const selectMacrocycleCandidate = (candidateId: string) => {
    invalidateMacrocycleConfirmation(
      { kind: "candidate", candidateId },
      "断环组合已变化；原大环确认已撤销。",
    );
  };

  const restoreDefaultMacrocycleCandidate = () => {
    const candidateId = macrocycleStatus?.review?.recommended_candidate_id || "";
    if (candidateId) {
      invalidateMacrocycleConfirmation(
        { kind: "candidate", candidateId },
        "已恢复 Meeko 默认组合；原大环确认已撤销。",
      );
    }
  };

  const checkConversionTools = async () => {
    setIsCheckingTools(true);
    setRawError("");
    try {
      const rawPayload = await invoke<string>("get_preparation_tool_status", {
        projectDir: project.project_dir,
      });
      if (!mountedRef.current) return;
      const parsed = parsePreparationResponse(rawPayload);
      if (!parsed.ok) {
        setMessage(parsed.error?.message ?? "无法检测格式转换工具。");
        setRawError(parsed.error?.raw_error ?? "");
        return;
      }
      setTools(parsed.tools);
      setMessage("格式转换工具检测完成；开始转换时仍会复核输入和输出。" );
    } catch (error) {
      if (!mountedRef.current) return;
      setMessage("无法检测格式转换工具。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      if (mountedRef.current) setIsCheckingTools(false);
    }
  };

  const resetTarget = async (target: PreparationTarget) => {
    const label = target === "receptor" ? "受体" : "配体";
    if (!window.confirm(`确定重置${label}准备状态吗？已有 Vina 输入文件不会被删除。`)) return;
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>("reset_preparation_status", {
        projectDir: project.project_dir,
        target,
      });
      if (!mountedRef.current) return;
      applyResponse(parsePreparationResponse(rawPayload), `${label}准备状态已重置。`);
    } catch (error) {
      if (!mountedRef.current) return;
      setMessage("无法重置准备状态。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      if (mountedRef.current) setIsBusy(false);
    }
  };

  const prepareTarget = async (target: PreparationTarget) => {
    const reviewedMacrocycleOptions = target === "ligand" && macrocycleMode === "reviewed"
      ? buildReviewedMacrocyclePreparationOptions(
          macrocycleStatus,
          macrocycleSelection,
          macrocycleOptions,
        )
      : undefined;
    if (target === "ligand" && macrocycleMode === "reviewed" && !reviewedMacrocycleOptions) {
      setMessage("请先分析并确认当前大环处理方案。");
      setRawError("");
      return;
    }
    const detectedBadResidues = target === "receptor"
      ? response?.preparation?.receptor.error?.bad_residues ?? []
      : [];
    const detectedAltlocs = target === "receptor"
      ? response?.preparation?.receptor.error?.alternate_locations
        ?? response?.error?.alternate_locations
        ?? []
      : [];
    const reviewedBadResidueOptions = target === "receptor"
      && detectedBadResidues.length > 0
      && badResidueReviewConfirmed
      ? {
          protocol: "meeko_allow_bad_res_reviewed",
          acknowledged_bad_residues: detectedBadResidues,
          alternate_locations: receptorAltlocSelections,
        }
      : undefined;
    const reviewedAltlocOptions = target === "receptor"
      && detectedBadResidues.length === 0
      && detectedAltlocs.length > 0
      ? {
          protocol: "meeko_receptor_controls",
          receptor_controls: {
            schema_version: 1,
            allow_bad_res: false,
            alternate_locations: receptorAltlocSelections,
            template_assignments: {},
            deleted_residues: [],
          },
        }
      : undefined;
    setPendingTarget(target);
    setIsBusy(true);
    setRawError("");
    if (target === "ligand") setMacrocycleEvidence(null);
    activeTaskAbortRef.current?.abort();
    const controller = new AbortController();
    let taskId = "";
    activeTaskAbortRef.current = controller;
    try {
      const started = await startPreparationTask(
        project.project_dir,
        target,
        target === "receptor" ? overwriteReceptor : overwriteLigand,
        reviewedBadResidueOptions ?? reviewedAltlocOptions ?? reviewedMacrocycleOptions ?? undefined,
      );
      taskId = started.task_id;
      if (!mountedRef.current) return;
      setMessage(started.deduplicated ? "同一准备任务已在运行，正在读取进度。" : "准备任务已开始，可继续查看项目其他内容。" );
      await waitForPreparation(started, target, controller);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (!mountedRef.current) return;
      setMessage(target === "receptor" ? "无法准备受体输入。" : "无法准备配体输入。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      if (activeTaskAbortRef.current === controller) activeTaskAbortRef.current = null;
      if (mountedRef.current) {
        setActiveTask((current) => (current?.task_id === taskId ? null : current));
        setPendingTarget(null);
        setIsBusy(false);
      }
    }
  };

  const cancelPreparation = async () => {
    if (!activeTask || ["cancelled", "failed", "finished"].includes(activeTask.status)) return;
    try {
      const wasQueued = activeTask.status === "queued";
      const cancelled = await cancelBackgroundTask(activeTask.task_id);
      setActiveTask(cancelled);
      setMessage(
        wasQueued
          ? (cancelled.message || "排队中的结构准备任务已取消。")
          : "正在终止结构准备工具进程；现有 preparation 日志会保留，随后请刷新状态。",
      );
    } catch (error) {
      setMessage("无法取消结构准备任务。");
      setRawError(error instanceof Error ? error.message : String(error));
    }
  };

  const loadLog = async (target: PreparationTarget) => {
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>(
        target === "receptor" ? "load_receptor_preparation_log" : "load_ligand_preparation_log",
        { projectDir: project.project_dir },
      );
      if (!mountedRef.current) return;
      const parsed = JSON.parse(rawPayload) as {
        message?: string;
        stderr?: string;
        stdout?: string;
        log?: string;
        error?: { message: string; raw_error: string };
      };
      setMessage(parsed.message ?? parsed.error?.message ?? "准备日志已读取。");
      setRawError([parsed.stderr, parsed.stdout, parsed.log, parsed.error?.raw_error].filter(Boolean).join("\n\n"));
    } catch (error) {
      if (!mountedRef.current) return;
      setMessage("无法读取准备日志。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      if (mountedRef.current) setIsBusy(false);
    }
  };

  const preparation = response?.preparation ?? project.preparation;
  const receptorPrep: PreparationResult | undefined = preparation?.receptor;
  const ligandPrep: PreparationResult | undefined = preparation?.ligand;
  const files = response?.files;
  const batchViewableLigands = batchLigandPreview?.candidates.filter((candidate) => (
    (candidate.status === "ready" || candidate.status === "duplicate")
    && Boolean(candidate.stagedFile)
  )) ?? [];
  const batchReadyLigands = batchViewableLigands.filter((candidate) => (
    candidate.status === "ready" && Boolean(candidate.stagedFile)
  ));
  const isBatchPreparation = batchReadyLigands.length > 1 && Boolean(batchLigandPreview?.revisionSha256);
  const selectedBatchLigand = batchViewableLigands.find((candidate) => candidate.id === selectedBatchLigandId)
    ?? batchViewableLigands[0]
    ?? null;
  const readyForBox = files?.receptor_prepared?.status === "ok"
    && (isBatchPreparation || files?.ligand_prepared?.status === "ok");
  const reviewedMacrocyclePreparationOptions = buildReviewedMacrocyclePreparationOptions(
    macrocycleStatus,
    macrocycleSelection,
    macrocycleOptions,
  );
  const macrocycleConfirmation = macrocycleStatus?.confirmation?.valid
    ? macrocycleStatus.confirmation
    : null;
  const macrocyclePreparedForCurrentConfirmation = Boolean(
    files?.ligand_prepared?.status === "ok"
    && ligandPrep?.method === "meeko_macrocycle"
    && ligandPrep.status === "finished"
    && macrocycleConfirmation
    && macrocycleEvidence
    && macrocycleEvidence.reviewId === macrocycleConfirmation.review_id
    && macrocycleEvidence.confirmationSha256.toLowerCase()
      === String(macrocycleConfirmation.record?.sha256 || "").toLowerCase()
    && macrocycleEvidence.selectionMode === macrocycleConfirmation.selection_mode
    && (
      macrocycleConfirmation.selection_mode === "rigid"
      || macrocycleEvidence.candidateId === macrocycleConfirmation.candidate_id
    )
    && macrocycleEvidence.bondsMatch === true
  );
  const interactionBusy = Boolean(
    isMacrocycleBusy
    || pendingTarget
    || activeTask?.status === "queued"
    || activeTask?.status === "running",
  );
  const loadingTarget = pendingTarget ?? (
    activeTask?.target === "receptor" || activeTask?.target === "ligand"
      ? activeTask.target
      : null
  );
  const loadingTitle = isMacrocycleBusy
    ? "正在处理大环审查"
    : isCheckingTools
    ? "正在检查转换工具"
    : loadingTarget === "receptor"
      ? "正在转换受体"
      : loadingTarget === "ligand"
        ? "正在转换配体"
        : "";

  useEffect(() => {
    const prepId = ligandPrep?.prep_id || "";
    if (
      ligandPrep?.method !== "meeko_macrocycle"
      || ligandPrep.status !== "finished"
      || !prepId
    ) {
      if (ligandPrep?.method !== "meeko_macrocycle") setMacrocycleEvidence(null);
      return;
    }
    let disposed = false;
    const loadEvidence = async () => {
      try {
        const rawPayload = await invoke<string>("load_preparation_metadata", {
          projectDir: project.project_dir,
          target: "ligand",
          prepId,
        });
        const parsed = JSON.parse(rawPayload) as {
          ok?: boolean;
          metadata?: unknown;
          error?: { message?: string; raw_error?: string };
        };
        if (disposed || !mountedRef.current) return;
        if (parsed.ok) {
          setMacrocycleEvidence(extractMacrocyclePreparationEvidence(parsed.metadata));
        }
      } catch {
        if (!disposed && mountedRef.current) setMacrocycleEvidence(null);
      }
    };
    void loadEvidence();
    return () => {
      disposed = true;
    };
  }, [ligandPrep?.method, ligandPrep?.prep_id, ligandPrep?.status, project.project_dir]);

  const renderStructureRow = (target: PreparationTarget, prep: PreparationResult | undefined) => {
    const isReceptor = target === "receptor";
    const label = isReceptor ? "受体" : "配体";
    const rawFile = isReceptor ? files?.receptor_raw : files?.ligand_raw;
    const preparedFile = isReceptor ? files?.receptor_prepared : files?.ligand_prepared;
    const projectFile = isReceptor ? project.receptor.file : project.ligand.file;
    const projectRawFile = isReceptor ? project.receptor.raw_file : project.ligand.raw_file;
    const projectSourceId = isReceptor ? project.receptor.source_id : project.ligand.source_id;
    const rawReady = rawFile?.status === "ok";
    const isReady = preparedFile?.status === "ok";
    const macrocyclePreparationBlocked = (
      !isReceptor
      && macrocycleMode === "reviewed"
      && !reviewedMacrocyclePreparationOptions
    );
    const displayFile = isReady
      ? fileLine(preparedFile, projectFile)
      : fileLine(rawFile, projectRawFile);
    const sourceName = sourceIdentityLabel(projectSourceId);
    const fileName = sourceName
      || displayFile.split(/[\\/]/).filter(Boolean).pop()
      || "尚未选择结构";
    const preparedSize = preparedFile?.size ?? 0;
    const shouldLoadPreview = isReady && previewRequested[target];
    const review: StructureReviewPayload | undefined = response?.structure_review;
    const receptorFacts = factRecord(review?.receptor);
    const receptorRawFacts = factRecord(receptorFacts.raw);
    const receptorPdbqtFacts = factRecord(receptorFacts.pdbqt);
    const receptorHasPdbqtFacts = Object.keys(receptorPdbqtFacts).length > 0;
    const receptorDisplayFacts = receptorHasPdbqtFacts ? receptorPdbqtFacts : receptorRawFacts;
    const receptorDisplaySource = receptorHasPdbqtFacts ? "最终 PDBQT" : "原始结构";
    const ligandFacts = factRecord(review?.ligand);
    const ligandRawFacts = factRecord(ligandFacts.raw);
    const ligandPdbqtFacts = factRecord(ligandFacts.pdbqt);
    const sourceFacts = isReceptor
      ? receptorFacts
      : Object.keys(ligandRawFacts).length ? ligandRawFacts : ligandPdbqtFacts;
    const fragmentCount = factNumber(sourceFacts, "fragment_count");
    const formalCharge = factNumber(sourceFacts, "formal_charge");
    const heavyAtomCount = factNumber(sourceFacts, "heavy_atom_count");
    const torsdof = factNumber(ligandPdbqtFacts, "torsdof");
    const stereoEncoded = sourceFacts.stereochemistry_encoded === true;
    const receptorMode = String(receptorPdbqtFacts.receptor_pdbqt_mode || "unknown");
    const receptorRawAvailable = Object.keys(receptorRawFacts).length > 0;
    const receptorIonComponents = factArray(receptorRawFacts.ion_non_polymer_components);
    const receptorAltlocs = factArray(receptorRawFacts.alternate_locations);
    const receptorPartialCharge = factNumber(receptorPdbqtFacts, "partial_charge_sum");
    const receptorActiveTorsions = factNumber(receptorPdbqtFacts, "active_torsions");
    const receptorRepresentations = factArray(receptorFacts.representations);
    const badResidues = isReceptor
      ? prep?.error?.bad_residues ?? response?.error?.bad_residues ?? []
      : [];
    const alternateLocations = isReceptor
      ? prep?.error?.alternate_locations ?? response?.error?.alternate_locations ?? []
      : [];
    const allAlternateLocationsSelected = alternateLocations.every((item) => (
      item.ids.includes(receptorAltlocSelections[item.selector] ?? "")
    ));
    const ligandHasRawFacts = Object.keys(ligandRawFacts).length > 0;
    const ligandDisplaySource = ligandHasRawFacts ? "原始结构" : "最终 PDBQT";
    const preparationState = isReady
      ? "PDBQT 已就绪"
      : rawReady
        ? prep?.status
          ? `${statusLabel(prep.status)}（等待 PDBQT）`
          : "原始结构已导入，等待转换"
        : "等待导入结构";
    const receptorReviewCount = badResidues.length + alternateLocations.length;
    const receptorCriticalWarning = receptorReviewCount > 0
      ? `${receptorReviewCount} 项结构审查待确认${badResidues.length ? `，含 ${badResidues.length} 个不完整残基` : ""}${alternateLocations.length ? `，含 ${alternateLocations.length} 处替代构象` : ""}`
      : isReady && !receptorRawAvailable
        ? RECEPTOR_RAW_REQUIRED
        : receptorAltlocs.length > 0
          ? `原始结构记录了 ${receptorAltlocs.length} 项替代构象；请人工核对最终选用构象`
          : receptorIonComponents.length > 0
            ? `检测到 ${receptorIonComponents.length} 项离子或非聚合物组分；请人工核对其保留范围`
            : null;
    const ligandCriticalWarning = fragmentCount !== null && fragmentCount > 1
      ? `检测到 ${fragmentCount} 个连接组分；请人工核对盐、溶剂或共价关系`
      : sourceFacts.contains_salt === true
        ? "检测到结构包含盐；请人工核对对接时需要保留的组分"
        : sourceFacts.undefined_stereochemistry === true
          ? "检测到未定义立体信息；请在对接前核对目标立体化学"
          : isReady && !ligandHasRawFacts
            ? "当前仅有最终 PDBQT；盐、总形式电荷与立体信息可能无法可靠审计"
            : null;
    const criticalWarning = isReceptor ? receptorCriticalWarning : ligandCriticalWarning;

    return (
      <article className="preparation-target-row">
        <div className="preparation-target-identity">
          <div className="preparation-target-primary">
            <span className="preparation-target-icon"><FileArrowUp aria-hidden="true" size={24} /></span>
            <div>
              <span>{label}</span>
              <strong>{fileName}</strong>
              <small>{isReady ? "PDBQT 已就绪" : rawReady ? (isReceptor ? "PDB / CIF" : "SDF / MOL / MOL2") : "等待文件"}</small>
            </div>
          </div>
          <dl className="preparation-structure-facts preparation-structure-facts-summary" aria-label={`${label}核心结构事实`}>
            <div><dt>准备状态</dt><FactValue source="当前文件检查">{preparationState}</FactValue></div>
            {isReady && isReceptor ? (
              <>
                <div><dt>链 ID</dt><FactValue source={receptorDisplaySource}>{factStringList(receptorDisplayFacts.chains)}</FactValue></div>
                <div><dt>总原子数</dt><FactValue source={receptorDisplaySource}>{factNumber(receptorDisplayFacts, "atom_count") ?? "原子记录无法解析"}</FactValue></div>
              </>
            ) : null}
            {isReady && !isReceptor ? (
              <div><dt>重原子数</dt><FactValue source={ligandDisplaySource}>{heavyAtomCount ?? "无法可靠判定"}</FactValue></div>
            ) : null}
            {criticalWarning ? (
              <div className="preparation-structure-warning">
                <dt>关键警告</dt>
                <FactValue source={isReceptor ? "结构审查" : ligandDisplaySource}>{criticalWarning}</FactValue>
              </div>
            ) : null}
          </dl>
          {isReady ? (
            <AdvancedDetails className="preparation-structure-audit-details" summary={`查看${label}完整结构审计`}>
              <dl className="preparation-structure-facts preparation-structure-facts-audit" aria-label={`${label}完整结构审计事实`}>
                {isReceptor ? (
                  <>
                    <div><dt>重原子数</dt><FactValue source={receptorDisplaySource}>{factNumber(receptorDisplayFacts, "heavy_atom_count") ?? "原子类型无法解析"}</FactValue></div>
                    <div><dt>氢原子数</dt><FactValue source={receptorDisplaySource}>{factNumber(receptorDisplayFacts, "hydrogen_atom_count") ?? "原子类型无法解析"}</FactValue></div>
                    <div><dt>三维坐标</dt><FactValue source={receptorDisplaySource}>{factBooleanLabel(receptorDisplayFacts.has_3d_coordinates, "坐标列无法完整解析")}</FactValue></div>
                    <div><dt>坐标边界</dt><FactValue source={receptorDisplaySource}>{coordinateBoundsLabel(receptorDisplayFacts.coordinate_bounds)}</FactValue></div>
                    <div><dt>残基数量</dt><FactValue source={receptorDisplaySource}>{factNumber(receptorDisplayFacts, "residue_count") ?? "残基记录无法解析"}</FactValue></div>
                    <div><dt>AutoDock 类型</dt><FactValue source="最终 PDBQT">{receptorHasPdbqtFacts ? factStringList(receptorPdbqtFacts.autodock_atom_types) : "尚无最终 PDBQT"}</FactValue></div>
                    <div>
                      <dt>部分电荷总和</dt>
                      <FactValue source="最终 PDBQT">{!receptorHasPdbqtFacts ? "尚无最终 PDBQT" : receptorPartialCharge === null ? "PDBQT 部分电荷列无法完整解析" : receptorPartialCharge.toFixed(4)}</FactValue>
                    </div>
                    <div><dt>PDBQT 模式</dt><FactValue source="最终 PDBQT">{!receptorHasPdbqtFacts ? "尚无最终 PDBQT" : receptorMode === "flexible" ? "柔性受体" : receptorMode === "rigid" ? "刚性受体" : "无法判定"}</FactValue></div>
                    <div>
                      <dt>活动扭转</dt>
                      <FactValue source="最终 PDBQT">{!receptorHasPdbqtFacts ? "尚无最终 PDBQT" : receptorMode === "rigid" ? "不适用（刚性受体）" : receptorActiveTorsions ?? "柔性拓扑未给出扭转计数"}</FactValue>
                    </div>
                    <div>
                      <dt>离子与非聚合物组分</dt>
                      <FactValue source="原始结构">{receptorRawAvailable ? `${receptorIonComponents.length} 项` : RECEPTOR_RAW_REQUIRED}</FactValue>
                    </div>
                    <div><dt>替代构象</dt><FactValue source="原始结构">{receptorRawAvailable ? (receptorAltlocs.length ? receptorAltlocs.join("、") : "未检测到") : RECEPTOR_RAW_REQUIRED}</FactValue></div>
                    <div>
                      <dt>残基模板异常</dt>
                      <FactValue source="Meeko">
                        {receptorRawAvailable
                          ? "当前准备记录未包含足够的 Meeko 残基模板校验信息"
                          : RECEPTOR_RAW_REQUIRED}
                      </FactValue>
                    </div>
                    <div>
                      <dt>非标准手性或几何异常</dt>
                      <FactValue source="原始结构">
                        {receptorRawAvailable ? "原始结构已保留；尚未执行专用手性与几何模板校验" : RECEPTOR_RAW_REQUIRED}
                      </FactValue>
                    </div>
                    {receptorRepresentations.length > 1 ? (
                      <div className="preparation-representation-row">
                        <dt>关联格式</dt>
                        <FactValue source="准备快照">
                          {receptorRepresentations.map((item) => String(factRecord(item).format || "").toUpperCase()).filter(Boolean).join(" + ")}
                        </FactValue>
                      </div>
                    ) : null}
                  </>
                ) : (
                  <>
                    <div><dt>连接组分</dt><FactValue source={ligandDisplaySource}>{fragmentCount ?? "无法可靠判定"}</FactValue></div>
                    <div><dt>总形式电荷</dt><FactValue source={ligandDisplaySource}>{formalChargeLabel(formalCharge)}</FactValue></div>
                    <div><dt>包含盐</dt><FactValue source={ligandDisplaySource}>{factBooleanLabel(sourceFacts.contains_salt)}</FactValue></div>
                    <div>
                      <dt>未定义立体信息</dt>
                      <FactValue source={ligandDisplaySource}>{factBooleanLabel(sourceFacts.undefined_stereochemistry, stereoEncoded ? "无法判定（已记录立体标记）" : "无法可靠判定")}</FactValue>
                    </div>
                    <div><dt>三维坐标</dt><FactValue source={ligandDisplaySource}>{factBooleanLabel(sourceFacts.has_3d_coordinates, "未检测到")}</FactValue></div>
                    <div><dt>PDBQT 活动扭转</dt><FactValue source="最终 PDBQT">{torsdof ?? "无法从 TORSDOF 读取"}</FactValue></div>
                  </>
                )}
              </dl>
            </AdvancedDetails>
          ) : null}
        </div>

        {shouldLoadPreview ? (
          <Suspense fallback={<div className="structure-mini-preview structure-mini-preview-loading">正在加载 3D 预览…</div>}>
            <StructureMiniPreview
              fileKind={isReceptor ? "receptor_prepared" : "ligand_prepared"}
              label={label}
              projectDir={project.project_dir}
              refreshKey={previewRevision[target]}
            />
          </Suspense>
        ) : (
          <div className="structure-mini-preview structure-mini-preview-gate">
            {isReady ? (
              <>
                <strong>3D 预览按需加载</strong>
                <span>
                  {preparedSize > 0
                    ? `文件大小 ${preparedSize.toLocaleString()} B；点击加载 3D 预览。`
                    : "点击加载 3D 预览。"}
                </span>
                <ActionButton onClick={() => setPreviewRequested((current) => ({ ...current, [target]: true }))}>
                  加载 3D 预览
                </ActionButton>
              </>
            ) : (
              <>
                <strong>{rawReady ? "等待转换为 PDBQT" : "等待导入结构"}</strong>
                <span>{rawReady ? "完成转换后即可查看 PDBQT。" : "PDBQT 可直接预览，原始结构需要先转换。"}</span>
              </>
            )}
          </div>
        )}

        <div className="preparation-target-actions">
          <div className={`preparation-file-check ${isReady ? "is-ready" : "is-missing"}`}>
            {isReady ? <CheckCircle aria-hidden="true" size={18} weight="fill" /> : <Info aria-hidden="true" size={18} weight="fill" />}
            <div>
              <strong>{isReady ? "PDBQT 已就绪" : rawReady ? "可以开始转换" : "等待原始文件"}</strong>
              <span>{preparedFile?.size ? `${preparedFile.size.toLocaleString()} B` : statusLabel(prep?.status)}</span>
            </div>
          </div>

          <ActionButton variant={displayFile ? "secondary" : "primary"} disabled={interactionBusy} onClick={() => void pickStructureFile(target)}>
            {displayFile ? "更改结构文件" : "选择结构文件"}
          </ActionButton>
          {rawReady ? (
            <>
              <label className="checkbox-row compact">
                <input
                  type="checkbox"
                  checked={isReceptor ? overwriteReceptor : overwriteLigand}
                  onChange={(event) => (isReceptor ? setOverwriteReceptor(event.target.checked) : setOverwriteLigand(event.target.checked))}
                />
                {isReady ? "从原始文件重新转换" : "覆盖已有 PDBQT"}
              </label>
              {!isReceptor && macrocycleMode === "reviewed"
                ? null
                : badResidues.length || alternateLocations.length
                  ? null
                  : (
                <ActionButton
                  variant="primary"
                  disabled={
                    interactionBusy
                    || !rawReady
                    || macrocyclePreparationBlocked
                    || (badResidues.length > 0 && !badResidueReviewConfirmed)
                    || !allAlternateLocationsSelected
                    || (isReady && !(isReceptor ? overwriteReceptor : overwriteLigand))
                  }
                  onClick={() => void prepareTarget(target)}
                >
                  {isReceptor
                    ? badResidues.length || alternateLocations.length ? "确认并重新转换" : "转换受体为 PDBQT"
                    : macrocyclePreparationBlocked
                      ? "先确认大环方案"
                      : "转换配体为 PDBQT"}
                </ActionButton>
                  )}
            </>
          ) : null}

          <AdvancedDetails className="preparation-target-details" summary="查看详情">
            <dl className="meta-list">
              <div><dt>原始输入</dt><dd><code>{fileLine(rawFile, projectRawFile)}</code></dd></div>
              <div><dt>准备方法</dt><dd>{prep?.method ?? "外部或手动导入"}</dd></div>
              <div><dt>日志</dt><dd><code>{prep?.log_file || "未生成"}</code></dd></div>
            </dl>
            <div className="button-row">
              <ActionButton variant="text" onClick={() => void loadLog(target)} disabled={interactionBusy}>读取日志</ActionButton>
              <ActionButton variant="text" onClick={() => void resetTarget(target)} disabled={interactionBusy}>重置状态</ActionButton>
            </div>
          </AdvancedDetails>
        </div>
        {badResidues.length || alternateLocations.length ? (
          <section className="preparation-target-review" aria-label="受体结构审查">
            <header>
              <div>
                <span className="section-kicker">结构审查</span>
                <strong>确认不完整残基与替代构象</strong>
              </div>
              <span>{badResidues.length + alternateLocations.length} 项待确认</span>
            </header>
            <div className="preparation-target-review-grid">
              {badResidues.length ? (
                <div className="preparation-review-group">
                  <strong>不完整残基（{badResidues.length}）</strong>
                  <div className="preparation-bad-residue-list">
                    {badResidues.map((residue) => <code key={residue}>{residue}</code>)}
                  </div>
                  <label className="checkbox-row compact">
                    <input
                      type="checkbox"
                      checked={badResidueReviewConfirmed}
                      onChange={(event) => setBadResidueReviewConfirmed(event.target.checked)}
                    />
                    <span>已检查并同意本次转换忽略这些残基</span>
                  </label>
                </div>
              ) : null}
              {alternateLocations.length ? (
                <div className="preparation-review-group preparation-altloc-review">
                  <strong>替代构象（{alternateLocations.length}）</strong>
                  {alternateLocations.map((item) => (
                    <label key={item.selector}>
                      <span>{item.selector}{item.residue_name ? ` ${item.residue_name}` : ""}</span>
                      <select
                        value={receptorAltlocSelections[item.selector] ?? ""}
                        onChange={(event) => setReceptorAltlocSelections((current) => ({
                          ...current,
                          [item.selector]: event.target.value,
                        }))}
                      >
                        <option value="">请选择构象</option>
                        {item.ids.map((id) => <option key={id} value={id}>{id}</option>)}
                      </select>
                    </label>
                  ))}
                </div>
              ) : null}
            </div>
            <footer>
              <span>完成以上选择后，将使用本次确认重新准备受体。</span>
              <ActionButton
                variant="primary"
                disabled={
                  interactionBusy
                  || !rawReady
                  || (badResidues.length > 0 && !badResidueReviewConfirmed)
                  || !allAlternateLocationsSelected
                  || (isReady && !overwriteReceptor)
                }
                onClick={() => void prepareTarget("receptor")}
              >
                确认并重新转换
              </ActionButton>
            </footer>
          </section>
        ) : null}
      </article>
    );
  };

  const renderBatchLigandRow = (
    candidates: LigandImportCandidate[],
    selected: LigandImportCandidate,
    revisionSha256: string,
  ) => (
    <article className="preparation-target-row preparation-batch-target-row">
      <div className="preparation-batch-browser">
        <header>
          <div>
            <span className="section-kicker">02 · LIGAND LIBRARY</span>
            <strong>批量配体（{candidates.length}）</strong>
          </div>
          <span>{batchReadyLigands.length} 个对接快照{candidates.length !== batchReadyLigands.length ? ` · ${candidates.length} 条来源记录` : ""}</span>
        </header>
        <div className="preparation-batch-list" role="listbox" aria-label="选择要预览的批量配体">
          {candidates.map((candidate, index) => {
            const active = candidate.id === selected.id;
            const facts = candidate.chemicalFacts;
            return (
              <button
                className={`preparation-batch-item${active ? " is-selected" : ""}`}
                key={candidate.id}
                type="button"
                role="option"
                aria-selected={active}
                onClick={() => setSelectedBatchLigandId(candidate.id)}
              >
                <span>{String(index + 1).padStart(2, "0")}</span>
                <span>
                  <strong>{candidate.displayName}</strong>
                  <small>{candidate.originalName || candidate.sourceFile}</small>
                </span>
                <span>
                  {candidate.sourceFormat.toUpperCase()} → PDBQT
                  {candidate.status === "duplicate" ? " · 重复结构" : ""}
                  {facts?.heavyAtomCount !== null && facts?.heavyAtomCount !== undefined
                    ? ` · ${facts.heavyAtomCount} 重原子`
                    : ""}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      <Suspense fallback={<div className="structure-mini-preview structure-mini-preview-loading">正在加载 3D 预览…</div>}>
        <StructureMiniPreview
          fileKind="ligand_prepared"
          label={selected.displayName}
          projectDir={project.project_dir}
          screeningCandidate={{ id: selected.id, revisionSha256 }}
        />
      </Suspense>

      <div className="preparation-target-actions preparation-batch-actions">
        <div className="preparation-file-check is-ready">
          <CheckCircle aria-hidden="true" size={18} weight="fill" />
          <div>
            <strong>{selected.displayName}</strong>
            <span>当前 3D 预览</span>
          </div>
        </div>
        <dl className="preparation-batch-facts">
          <div><dt>原始文件</dt><dd>{selected.originalName || "未记录"}</dd></div>
          <div><dt>记录</dt><dd>{selected.recordIndex}</dd></div>
          <div><dt>形式电荷</dt><dd>{selected.chemicalFacts?.formalCharge ?? "未记录"}</dd></div>
          <div><dt>可旋转键</dt><dd>{selected.chemicalFacts?.rotatableBondCount ?? "未记录"}</dd></div>
        </dl>
        <ActionButton onClick={onBack}>更改配体库</ActionButton>
        <AdvancedDetails className="preparation-target-details" summary="查看候选身份">
          <dl className="meta-list">
            <div><dt>候选编号</dt><dd><code>{selected.id}</code></dd></div>
            <div><dt>来源格式</dt><dd>{selected.sourceFormat.toUpperCase()}</dd></div>
            <div><dt>准备后快照</dt><dd><code>{selected.stagedFile || "未生成"}</code></dd></div>
          </dl>
        </AdvancedDetails>
      </div>
    </article>
  );

  return (
    <PageShell labelledBy="preparation-title" className="preparation-workspace-page">
      <OperationLoadingDialog
        open={isCheckingTools || interactionBusy}
        title={loadingTitle || "正在处理结构转换"}
        message={isMacrocycleBusy
          ? message || "正在更新大环审查记录。"
          : isCheckingTools
          ? "正在检测 Python、RDKit 与 Meeko。"
          : activeTask?.progress.message || message || "结构转换任务正在本机运行。"}
        detail="转换完成后仍需人工检查结构与化学状态。"
        actionLabel={activeTask && !["cancelled", "failed", "finished"].includes(activeTask.status)
          ? (activeTask.status === "queued" ? "取消排队" : "终止准备")
          : undefined}
        onAction={activeTask && !["cancelled", "failed", "finished"].includes(activeTask.status)
          ? () => void cancelPreparation()
          : undefined}
      />
      <PageHero
        eyebrow="格式转换 · PDBQT PREPARATION"
        title="格式转换与 PDBQT 准备"
        titleId="preparation-title"
        description="将受体 PDB/CIF 与配体 SDF/MOL/MOL2 准备并转换为 PDBQT，或直接导入已有 PDBQT。"
        actions={(
          <>
            <ActionButton onClick={onBack}>在线搜索并下载</ActionButton>
            {activeTask && !["cancelled", "failed", "finished"].includes(activeTask.status) ? (
              <ActionButton onClick={() => void cancelPreparation()}>
                {activeTask.status === "queued" ? "取消排队" : "终止准备"}
              </ActionButton>
            ) : null}
            <ActionButton onClick={() => void reloadStatus()} disabled={isBusy}>{isBusy ? "刷新中…" : "刷新状态"}</ActionButton>
          </>
        )}
      />

      <BodyGrid className="preparation-workspace-layout">
        <MainPanel className="preparation-stage-panel">
          <div className="preparation-target-list">
            {renderStructureRow("receptor", receptorPrep)}
            {isBatchPreparation && selectedBatchLigand && batchLigandPreview?.revisionSha256
              ? renderBatchLigandRow(batchViewableLigands, selectedBatchLigand, batchLigandPreview.revisionSha256)
              : renderStructureRow("ligand", ligandPrep)}
          </div>

          {!isBatchPreparation ? (
          <section className="preparation-macrocycle-panel" aria-label="Meeko 大环配体准备">
            <MacrocycleBondSelector
              projectDir={project.project_dir}
              mode={macrocycleMode}
              status={macrocycleStatus}
              options={macrocycleOptions}
              selection={macrocycleSelection}
              evidence={macrocycleEvidence}
              rawReady={files?.ligand_raw?.status === "ok"}
              busy={interactionBusy}
              prepared={macrocyclePreparedForCurrentConfirmation}
              canPrepare={Boolean(reviewedMacrocyclePreparationOptions)}
              canContinue={Boolean(readyForBox && macrocyclePreparedForCurrentConfirmation)}
              onModeChange={changeMacrocycleMode}
              onOptionsChange={changeMacrocycleOptions}
              onReview={() => void reviewMacrocycle()}
              onSelectCandidate={selectMacrocycleCandidate}
              onRestoreDefault={restoreDefaultMacrocycleCandidate}
              onConfirmCandidate={() => void confirmMacrocycleCandidate()}
              onConfirmRigid={() => void confirmRigidMacrocycle()}
              onResetConfirmation={() => void resetMacrocycleConfirmation()}
              onPrepare={() => void prepareTarget("ligand")}
              onContinue={() => onOpenBoxSetup(project)}
            />
          </section>
          ) : null}

          <div className="preparation-feedback">
            <ScientificDisclaimer kind="preparation" />
            {message || rawError ? <CommandResultPanel title="格式转换状态" message={message} rawError={rawError} /> : null}
          </div>

          <footer className="preparation-action-bar">
            <p>自动准备结果仍需人工检查质子化、电荷、构象和缺失残基。</p>
            <div>
              <ActionButton onClick={() => void reloadStatus()} disabled={isBusy}>{isBusy ? "刷新中…" : "刷新文件状态"}</ActionButton>
              {!isBatchPreparation && macrocycleMode === "reviewed" ? (
                <span className="preparation-next-step-status">请在大环准备模块完成并继续</span>
              ) : readyForBox ? (
                <ActionButton variant="primary" onClick={() => onOpenBoxSetup(project)}>
                  设置搜索范围
                </ActionButton>
              ) : <span className="preparation-next-step-status">受体与配体准备完成后可继续</span>}
            </div>
          </footer>
        </MainPanel>

        <RightRail className="preparation-context-rail">
          <RightRailSection title="当前输入">
            <dl className="mode-context-list">
              <div><dt>受体</dt><dd>{project.receptor.source_id || files?.receptor_prepared?.path || files?.receptor_raw?.path || project.receptor.file || project.receptor.raw_file || "未选择"}</dd></div>
              {isBatchPreparation && selectedBatchLigand ? (
                <>
                  <div><dt>配体库</dt><dd>{batchViewableLigands.length} 条来源记录 · {batchReadyLigands.length} 个对接快照</dd></div>
                  <div><dt>当前预览</dt><dd>{selectedBatchLigand.displayName}</dd></div>
                </>
              ) : (
                <div><dt>配体</dt><dd>{project.ligand.source_id || files?.ligand_prepared?.path || files?.ligand_raw?.path || project.ligand.file || project.ligand.raw_file || "未选择"}</dd></div>
              )}
            </dl>
          </RightRailSection>

          <RightRailSection title="文件检查">
            <div className="preparation-check-list">
              <span className={files?.receptor_prepared?.status === "ok" ? "ready" : "missing"}><CheckCircle aria-hidden="true" size={16} weight="fill" /> 受体 PDBQT</span>
              <span className={isBatchPreparation || files?.ligand_prepared?.status === "ok" ? "ready" : "missing"}><CheckCircle aria-hidden="true" size={16} weight="fill" /> {isBatchPreparation ? `${batchReadyLigands.length} 个配体 PDBQT` : "配体 PDBQT"}</span>
            </div>
          </RightRailSection>

          <RightRailSection title="工具状态">
            {files?.receptor_raw?.status === "ok" || files?.ligand_raw?.status === "ok" ? (
              <>
                <p className="preparation-profile-hint">
                  Assisted 本地候选随附 RDKit / Meeko；Basic 本地候选默认直接导入 PDBQT，也可使用已配置的兼容 Python 工具链。
                </p>
                <p>{tools ? "转换工具状态已读取。" : "开始转换时会自动检查所需工具。"}</p>
                <ActionButton disabled={isCheckingTools || interactionBusy} onClick={() => void checkConversionTools()}>
                  {isCheckingTools ? "检测中…" : "检查转换工具"}
                </ActionButton>
                {tools ? (
                  <AdvancedDetails className="preparation-tool-details" summary="查看检测详情">
                    <dl className="mode-context-list">
                      <div><dt>Python</dt><dd>{statusLabel(tools.python?.status)} · {toolVersion(tools.python)}</dd></div>
                      <div><dt>RDKit</dt><dd>{statusLabel(tools.rdkit?.status)} · {capabilityLine(tools.rdkit, "sdf_inline_read")}</dd></div>
                      <div><dt>Meeko</dt><dd>{statusLabel(tools.meeko?.status)}</dd></div>
                    </dl>
                  </AdvancedDetails>
                ) : null}
              </>
            ) : <p>当前 PDBQT 可直接使用，不需要 RDKit / Meeko。</p>}
          </RightRailSection>

          <RightRailSection title="下一步">
            <p>{readyForBox ? "设置对接搜索范围，然后在同一工作台复核 Vina 参数。" : "先补全受体与配体 PDBQT。"}</p>
          </RightRailSection>
        </RightRail>
      </BodyGrid>
    </PageShell>
  );
}
