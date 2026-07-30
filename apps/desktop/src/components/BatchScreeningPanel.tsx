import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open, save } from "@tauri-apps/plugin-dialog";
import {
  CaretLeft,
  CaretRight,
  CheckCircle,
  Copy,
  DownloadSimple,
  Eye,
  FileText,
  FolderOpen,
  MagnifyingGlass,
  Play,
  Scales,
  SpinnerGap,
  Stop,
  TrayArrowDown,
  X,
} from "@phosphor-icons/react";

import type { DockStartProject } from "../types";
import { startScreeningTask, waitForBackgroundTask } from "../utils/backgroundTasks";
import {
  buildScreeningRankMap,
  filterScreeningItems,
  paginateScreeningItems,
  screeningItemDisplayName,
  selectTopScreeningItems,
  sortScreeningItems,
  type ScreeningResultSort,
  type ScreeningResultStatusFilter,
} from "../utils/screeningResults";
import {
  buildScreeningAdvancedVinaSummary,
  hasCompleteScreeningAdvancedVinaSettings,
  serializeScreeningVinaSettings,
} from "../utils/screeningVina";
import {
  defaultLigandSelection,
  ligandTopologyCoverage,
  normalizeLigandImportPreview,
  retryableLigandCandidateIds,
  selectAllReadyLigands,
  selectedLigandCandidateIds as collectSelectedLigandCandidateIds,
  selectedLigandFiles,
  toggleLigandSelection,
  type LigandImportPreview,
  type LigandImportStatus,
} from "../utils/screeningLigandImport";
import {
  archiveExportIntegrityLabel,
  ensureZipExtension,
  formatArchiveExportBytes,
  readScreeningArchiveExportConflict,
  readScreeningArchiveExportRecord,
  screeningArchiveExportDefaultName,
  type ScreeningArchiveExportRecord,
  type ScreeningArchiveExportResponse,
} from "../utils/screeningArchiveExport";
import ActionButton from "./ActionButton";
import AdvancedDetails from "./AdvancedDetails";
import OperationLoadingDialog from "./OperationLoadingDialog";
import ScreeningArchiveComparisonView, {
  type ScreeningArchiveComparison,
} from "./ScreeningArchiveComparisonView";
import StatusBadge from "./StatusBadge";
import "../styles/batch-screening.css";

const PoseStructurePreview = lazy(() => import("./PoseStructurePreview"));

type ScreeningItem = {
  item_id: string;
  order?: number;
  ligand_file?: string;
  source_file?: string;
  display_label?: string;
  status: string;
  attempt_count?: number;
  best_affinity_kcal_mol?: number | null;
  best_output_file?: string;
  best_output_sha256?: string;
  best_output_size_bytes?: number;
  last_error?: string;
};

type ScreeningState = {
  screening_id?: string;
  status: string;
  queue?: string[];
  items?: ScreeningItem[];
  box?: Partial<DockStartProject["box"]>;
  vina?: Partial<DockStartProject["vina"]>;
  compatibility?: {
    inferred_vina_fields?: string[];
    [key: string]: unknown;
  };
  top_n?: number;
  max_retries?: number;
  parameter_warnings?: string[];
  grid_resource?: {
    warnings?: string[];
    [key: string]: unknown;
  };
  last_integrity_error?: {
    code?: string;
    message?: string;
    detected_at?: string;
  };
  last_runtime_error?: {
    code?: string;
    message?: string;
    detected_at?: string;
  };
  outputs?: {
    summary_csv?: string;
    top_n_csv?: string;
    report_md?: string;
    report_sha256?: string;
    reported_at?: string;
    sdf?: { generated?: boolean; file?: string; reason?: string };
  };
  tools?: { vina?: { version?: string; source?: string; sha256?: string } };
};

type ScreeningResponse = {
  ok: boolean;
  screening?: ScreeningState;
  staged?: Array<{
    item_id?: string;
    file: string;
    original_name?: string;
    source_file?: string;
    source_record_name?: string;
  }>;
  import_preview?: unknown;
  staged_labels?: Record<string, string>;
  item_labels?: Record<string, string>;
  archives?: ScreeningArchiveSummary[];
  archive?: string | ScreeningArchiveSummary;
  archive_id?: string;
  files?: ScreeningArchiveFiles;
  report_file?: string;
  report_sha256?: string;
  attempt_integrity?: {
    status?: "verified" | "partially_verified" | "legacy_unverified" | "not_applicable" | string;
    warnings?: string[];
    vina_binary_archived?: boolean;
    vina_evidence?: string;
  };
  message?: string;
  error?: { code?: string; message?: string; raw_error?: string; suggestion?: string };
};

type ScreeningArchiveCounts = {
  total: number;
  succeeded: number;
  failed: number;
  unfinished: number;
};

type ScreeningArchiveSummary = {
  archive_id: string;
  valid: boolean;
  integrity: "state_verified" | "invalid" | string;
  screening_id?: string;
  status?: string;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  archived_at?: string | null;
  counts: ScreeningArchiveCounts;
  best_affinity_kcal_mol?: number | null;
  top_n?: number;
  report_available?: boolean;
  attempt_integrity?: string;
  error?: { code?: string; message?: string } | null;
};

type ScreeningArchiveFiles = {
  directory?: string;
  manifest?: string;
  state?: string;
  receptor?: string;
  summary_csv?: string;
  top_n_csv?: string;
  report_md?: string;
  items?: Record<string, { ligand_input?: string; best_output?: string }>;
};

type ScreeningArchiveDetail = ScreeningResponse & {
  archive: ScreeningArchiveSummary;
  screening: ScreeningState;
};

type ScreeningArchiveComparisonResponse = ScreeningArchiveComparison & Pick<
  ScreeningResponse,
  "ok" | "message" | "error"
>;

type BatchScreeningPanelProps = {
  projectDir: string;
  receptorFile: string;
  box: DockStartProject["box"];
  vina: DockStartProject["vina"];
  disabled?: boolean;
  disabledReason?: string;
  onBatchModeDetected?: () => void;
};

function parseResponse(raw: string): ScreeningResponse {
  return JSON.parse(raw) as ScreeningResponse;
}

function ligandImportStatusLabel(status: LigandImportStatus): string {
  if (status === "ready") return "可用";
  if (status === "duplicate") return "重复";
  if (status === "review_required") return "需审查";
  return "失败";
}

function ligandImportSizeLabel(value: number | null): string {
  if (value == null || value <= 0) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 ** 2).toFixed(1)} MB`;
}

function terminal(status: string): boolean {
  return ["completed", "completed_with_failures", "canceled"].includes(status);
}

function tone(status: string): "ok" | "warning" | "error" | "info" | "muted" {
  if (["completed", "succeeded"].includes(status)) return "ok";
  if (["completed_with_failures", "canceled", "cancel_requested", "interrupted"].includes(status)) return "warning";
  if (status === "running") return "info";
  if (status === "failed") return "error";
  return "muted";
}

const statusLabels: Record<string, string> = {
  ready: "已就绪",
  running: "运行中",
  cancel_requested: "等待安全取消",
  canceled: "已取消",
  interrupted: "已中断",
  completed: "已完成",
  completed_with_failures: "完成（含失败项）",
  pending: "待处理",
  succeeded: "成功",
  failed: "失败",
};

const RESULT_PAGE_SIZE = 10;
const ARCHIVE_PAGE_SIZE = 10;
const MAX_SCREENING_CPU = 64;
const MAX_SCREENING_RETRIES = 3;
const MAX_SCREENING_LIGANDS = 500;

function clampIntegerInput(
  value: string,
  minimum: number,
  maximum: number,
  fallback: number,
): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(maximum, Math.max(minimum, Math.trunc(parsed)));
}

function formatProtocolNumber(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "未记录";
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(6)));
}

function formatScreeningProtocol(value: unknown): string {
  if (value === "vina") return "Vina";
  if (value === "vinardo") return "Vinardo";
  return "未记录";
}

function absoluteProjectPath(projectDir: string, relativePath: string): string {
  const separator = projectDir.includes("\\") ? "\\" : "/";
  return `${projectDir.replace(/[\\/]+$/, "")}${separator}${relativePath.replace(/[\\/]+/g, separator)}`;
}

function archiveIdFromResponse(response: ScreeningResponse): string {
  if (response.archive_id) return response.archive_id;
  if (response.archive && typeof response.archive !== "string") return response.archive.archive_id;
  if (typeof response.archive !== "string") return "";
  return response.archive.split(/[\\/]/).filter(Boolean).pop() ?? "";
}

function formatArchiveTime(value?: string | null): string {
  if (!value) return "时间未记录";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export default function BatchScreeningPanel({
  projectDir,
  receptorFile,
  box,
  vina,
  disabled = false,
  disabledReason = "",
  onBatchModeDetected,
}: BatchScreeningPanelProps) {
  const [state, setState] = useState<ScreeningState | null>(null);
  const [ligandImportPreview, setLigandImportPreview] = useState<LigandImportPreview | null>(null);
  const [selectedLigandCandidateIds, setSelectedLigandCandidateIds] = useState<Set<string>>(new Set());
  const [importingLigands, setImportingLigands] = useState(false);
  const [retryingPreparation, setRetryingPreparation] = useState(false);
  const [maxRetries, setMaxRetries] = useState(1);
  const [topN, setTopN] = useState(20);
  const [cpuPerTask, setCpuPerTask] = useState(
    Math.min(MAX_SCREENING_CPU, Math.max(1, vina.cpu || 1)),
  );
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [resultQuery, setResultQuery] = useState("");
  const [resultStatus, setResultStatus] = useState<ScreeningResultStatusFilter>("all");
  const [resultSort, setResultSort] = useState<ScreeningResultSort>("score");
  const [topOnly, setTopOnly] = useState(false);
  const [resultPageNumber, setResultPageNumber] = useState(1);
  const [selectedPoseItemId, setSelectedPoseItemId] = useState("");
  const [panelTab, setPanelTab] = useState<"current" | "history">("current");
  const [archives, setArchives] = useState<ScreeningArchiveSummary[]>([]);
  const [archiveDetail, setArchiveDetail] = useState<ScreeningArchiveDetail | null>(null);
  const [archiveListBusy, setArchiveListBusy] = useState(false);
  const [archiveDetailBusy, setArchiveDetailBusy] = useState(false);
  const [loadingArchiveId, setLoadingArchiveId] = useState("");
  const [archivePageNumber, setArchivePageNumber] = useState(1);
  const [archiveError, setArchiveError] = useState("");
  const [comparisonSelection, setComparisonSelection] = useState<string[]>([]);
  const [archiveComparison, setArchiveComparison] = useState<ScreeningArchiveComparison | null>(null);
  const [archiveComparisonBusy, setArchiveComparisonBusy] = useState(false);
  const [exportingArchiveId, setExportingArchiveId] = useState("");
  const [archiveExportResult, setArchiveExportResult] = useState<ScreeningArchiveExportRecord | null>(null);
  const [archiveExportError, setArchiveExportError] = useState("");
  const projectGenerationRef = useRef(0);
  const statusRequestRef = useRef(0);
  const archiveListRequestRef = useRef(0);
  const archiveDetailRequestRef = useRef(0);
  const archiveComparisonRequestRef = useRef(0);
  const archiveExportRequestRef = useRef(0);
  const ligandImportRequestRef = useRef(0);
  const archiveExportTriggerRef = useRef<HTMLButtonElement | null>(null);
  const poseTriggerRef = useRef<HTMLButtonElement | null>(null);
  const poseDialogRef = useRef<HTMLDivElement | null>(null);
  const stagedFiles = useMemo(
    () => ligandImportPreview
      ? selectedLigandFiles(ligandImportPreview, selectedLigandCandidateIds)
      : [],
    [ligandImportPreview, selectedLigandCandidateIds],
  );
  const selectedCandidateIds = useMemo(
    () => ligandImportPreview
      ? collectSelectedLigandCandidateIds(ligandImportPreview, selectedLigandCandidateIds)
      : [],
    [ligandImportPreview, selectedLigandCandidateIds],
  );
  const retryableCandidateIds = useMemo(
    () => ligandImportPreview ? retryableLigandCandidateIds(ligandImportPreview) : [],
    [ligandImportPreview],
  );
  const topologyCoverage = useMemo(
    () => ligandImportPreview
      ? ligandTopologyCoverage(ligandImportPreview, selectedLigandCandidateIds)
      : null,
    [ligandImportPreview, selectedLigandCandidateIds],
  );
  const stagedLabels = useMemo(() => {
    if (!ligandImportPreview) return {};
    const labels: Record<string, string> = {};
    for (const candidate of ligandImportPreview.candidates) {
      if (candidate.stagedFile) labels[candidate.stagedFile] = candidate.displayName;
      if (candidate.sourceFile) labels[candidate.sourceFile] = candidate.displayName;
    }
    return labels;
  }, [ligandImportPreview]);

  useEffect(() => {
    setCpuPerTask(Math.min(MAX_SCREENING_CPU, Math.max(1, vina.cpu || 1)));
  }, [projectDir, vina.cpu]);

  useEffect(() => {
    setMaxRetries(1);
    setTopN(20);
  }, [projectDir]);

  const refresh = useCallback(async (quiet = false): Promise<ScreeningResponse | null> => {
    const request = ++statusRequestRef.current;
    try {
      const parsed = parseResponse(await invoke<string>("get_screening_status", { projectDir }));
      if (request !== statusRequestRef.current) return parsed;
      if (parsed.ok) {
        const preview = normalizeLigandImportPreview(parsed);
        setLigandImportPreview(preview);
        setSelectedLigandCandidateIds((current) => {
          const readyIds = new Set(
            preview.candidates
              .filter((item) => item.status === "ready" && item.stagedFile)
              .map((item) => item.id),
          );
          const preserved = new Set([...current].filter((id) => readyIds.has(id)));
          return current.size && preserved.size
            ? preserved
            : defaultLigandSelection(preview);
        });
        setState(parsed.screening ?? null);
        if (preview.counts.ready > 1 || parsed.screening) onBatchModeDetected?.();
        if (!quiet) setMessage(parsed.message || "批量筛选状态已刷新。");
      } else if (!parsed.ok) {
        setRawError(parsed.error?.raw_error || parsed.error?.message || "无法读取批量筛选状态。");
      }
      return parsed;
    } catch (error) {
      if (request === statusRequestRef.current && !quiet) {
        setRawError(error instanceof Error ? error.message : String(error));
      }
      return null;
    }
  }, [onBatchModeDetected, projectDir]);

  const refreshArchives = useCallback(async (quiet = false): Promise<ScreeningArchiveSummary[]> => {
    const request = ++archiveListRequestRef.current;
    setArchiveListBusy(true);
    try {
      const parsed = parseResponse(await invoke<string>("list_screening_archives", { projectDir }));
      if (request !== archiveListRequestRef.current) return parsed.archives ?? [];
      if (!parsed.ok) {
        throw new Error(parsed.error?.message || "无法读取批量筛选历史记录。");
      }
      const nextArchives = parsed.archives ?? [];
      setArchives(nextArchives);
      const comparableArchiveIds = new Set(
        nextArchives.filter((archive) => archive.valid).map((archive) => archive.archive_id),
      );
      setComparisonSelection((current) => current.filter((archiveId) => comparableArchiveIds.has(archiveId)).slice(0, 2));
      setArchivePageNumber((current) => Math.min(
        Math.max(1, Math.ceil(nextArchives.length / ARCHIVE_PAGE_SIZE)),
        current,
      ));
      setArchiveError("");
      if (!quiet) setMessage(parsed.message || "历史归档已刷新。");
      return nextArchives;
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      if (request === archiveListRequestRef.current) setArchiveError(detail);
      return [];
    } finally {
      if (request === archiveListRequestRef.current) setArchiveListBusy(false);
    }
  }, [projectDir]);

  const loadArchive = useCallback(async (archiveId: string) => {
    if (!archiveId) return;
    const request = ++archiveDetailRequestRef.current;
    archiveComparisonRequestRef.current += 1;
    setArchiveComparison(null);
    setArchiveComparisonBusy(false);
    setArchiveDetailBusy(true);
    setLoadingArchiveId(archiveId);
    setArchiveError("");
    setSelectedPoseItemId("");
    try {
      const parsed = parseResponse(await invoke<string>("get_screening_archive", {
        projectDir,
        archiveId,
      }));
      if (request !== archiveDetailRequestRef.current) return;
      if (!parsed.ok || !parsed.screening || !parsed.archive || typeof parsed.archive === "string") {
        throw new Error(parsed.error?.message || "无法读取所选历史归档。");
      }
      setArchiveDetail(parsed as ScreeningArchiveDetail);
      setResultPageNumber(1);
      setResultQuery("");
      setResultStatus("all");
      setTopOnly(false);
    } catch (error) {
      if (request === archiveDetailRequestRef.current) {
        setArchiveDetail(null);
        setArchiveError(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (request === archiveDetailRequestRef.current) {
        setArchiveDetailBusy(false);
        setLoadingArchiveId("");
      }
    }
  }, [projectDir]);

  const toggleComparisonArchive = (archiveId: string) => {
    archiveComparisonRequestRef.current += 1;
    setArchiveComparison(null);
    setArchiveComparisonBusy(false);
    setArchiveError("");
    setComparisonSelection((current) => {
      if (current.includes(archiveId)) {
        return current.filter((selected) => selected !== archiveId);
      }
      if (current.length >= 2) return current;
      return [...current, archiveId];
    });
  };

  const compareSelectedArchives = async () => {
    if (comparisonSelection.length !== 2) {
      setArchiveError("请先选择两个有效归档；先选择的是基线，后选择的是对照。");
      return;
    }
    const request = ++archiveComparisonRequestRef.current;
    archiveDetailRequestRef.current += 1;
    setArchiveComparisonBusy(true);
    setArchiveError("");
    setArchiveDetail(null);
    setSelectedPoseItemId("");
    try {
      const parsed = JSON.parse(await invoke<string>("compare_screening_archives", {
        projectDir,
        archiveIds: comparisonSelection,
      })) as ScreeningArchiveComparisonResponse;
      if (request !== archiveComparisonRequestRef.current) return;
      if (
        !parsed.ok
        || !parsed.baseline_archive
        || !parsed.comparison_archive
        || !parsed.comparability
        || !Array.isArray(parsed.rows)
      ) {
        throw new Error(parsed.error?.message || "无法比较所选历史归档。");
      }
      setArchiveComparison(parsed);
      setMessage(parsed.message || "历史归档比较已生成。");
    } catch (error) {
      if (request === archiveComparisonRequestRef.current) {
        setArchiveComparison(null);
        setArchiveError(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (request === archiveComparisonRequestRef.current) setArchiveComparisonBusy(false);
    }
  };

  const exportArchiveZip = async (
    archiveId: string,
    trigger: HTMLButtonElement,
  ) => {
    if (!archiveId || exportingArchiveId) return;
    const request = ++archiveExportRequestRef.current;
    const generation = projectGenerationRef.current;
    const requestProjectDir = projectDir;
    archiveExportTriggerRef.current = trigger;
    let selectedPath: string | null = null;
    try {
      selectedPath = await save({
        title: "导出批量筛选归档",
        defaultPath: screeningArchiveExportDefaultName(archiveId),
        filters: [{ name: "ZIP 压缩包", extensions: ["zip"] }],
      });
    } catch (error) {
      if (
        request === archiveExportRequestRef.current
        && generation === projectGenerationRef.current
      ) {
        setArchiveExportError(error instanceof Error ? error.message : String(error));
        window.requestAnimationFrame(() => archiveExportTriggerRef.current?.focus());
      }
      return;
    }
    if (
      request !== archiveExportRequestRef.current
      || generation !== projectGenerationRef.current
    ) return;
    if (!selectedPath) {
      window.requestAnimationFrame(() => archiveExportTriggerRef.current?.focus());
      return;
    }

    const outputPath = ensureZipExtension(selectedPath);
    setExportingArchiveId(archiveId);
    setArchiveExportError("");
    try {
      const invokeExport = async () => JSON.parse(await invoke<string>("export_screening_archive_zip", {
        projectDir: requestProjectDir,
        archiveId,
        outputPath,
        overwrite: false,
      })) as ScreeningArchiveExportResponse;

      // The save dialog and the actual write are separate operations. The GUI
      // therefore always uses no-overwrite publication and asks the user to
      // choose another name if the target exists at backend execution time.
      const parsed = await invokeExport();
      if (
        request !== archiveExportRequestRef.current
        || generation !== projectGenerationRef.current
      ) return;
      if (!parsed.ok && parsed.error?.code === "SCREENING_ARCHIVE_EXPORT_EXISTS") {
        const conflict = readScreeningArchiveExportConflict(parsed);
        const comparablePath = (value: string) => (
          value.trim().replace(/\//g, "\\").replace(/\\+$/, "").toLowerCase()
        );
        if (
          !conflict
          || comparablePath(conflict.destination_file) !== comparablePath(outputPath)
        ) {
          throw new Error("保存位置已有文件。为保护现有数据，DockStart 不会覆盖它；请重新导出并选择新文件名。");
        }
        throw new Error([
          "保存位置已有文件，DockStart 未覆盖它。",
          `文件：${conflict.destination_file}`,
          `大小：${formatArchiveExportBytes(conflict.destination_size_bytes)}`,
          `SHA256：${conflict.destination_sha256}`,
          "请重新导出并选择新文件名。",
        ].join(" "));
      }
      if (!parsed.ok) {
        const errorMessage = (
          parsed.error?.raw_error
          || parsed.error?.message
          || "批量筛选归档导出失败。"
        );
        const suggestion = parsed.error?.suggestion?.trim();
        throw new Error(suggestion ? `${errorMessage} ${suggestion}` : errorMessage);
      }
      const record = readScreeningArchiveExportRecord(parsed);
      if (!record) {
        throw new Error("归档已导出，但后端没有返回完整的 ZIP 校验信息。");
      }
      const comparablePath = (value: string) => (
        value.trim().replace(/\//g, "\\").replace(/\\+$/, "").toLowerCase()
      );
      if (
        record.archive_id !== archiveId
        || comparablePath(record.zip_file) !== comparablePath(outputPath)
        || comparablePath(parsed.project_dir || "") !== comparablePath(requestProjectDir)
      ) {
        throw new Error("归档已导出，但返回结果与本次归档或保存路径不一致。");
      }
      if (record.overwritten) {
        throw new Error("归档已导出，但返回的覆盖状态与本次操作不一致。");
      }
      setArchiveExportResult(record);
      setMessage(parsed.message || "批量筛选归档 ZIP 已导出。");
    } catch (error) {
      if (
        request === archiveExportRequestRef.current
        && generation === projectGenerationRef.current
      ) {
        setArchiveExportError(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (
        request === archiveExportRequestRef.current
        && generation === projectGenerationRef.current
      ) {
        setExportingArchiveId("");
        window.requestAnimationFrame(() => archiveExportTriggerRef.current?.focus());
      }
    }
  };

  useEffect(() => {
    setState(null);
    setLigandImportPreview(null);
    setSelectedLigandCandidateIds(new Set());
    setImportingLigands(false);
    setMessage("");
    setRawError("");
    setResultQuery("");
    setResultStatus("all");
    setResultSort("score");
    setTopOnly(false);
    setResultPageNumber(1);
    setSelectedPoseItemId("");
    setPanelTab("current");
    setArchives([]);
    setArchiveDetail(null);
    setArchiveError("");
    setArchiveListBusy(false);
    setArchiveDetailBusy(false);
    setLoadingArchiveId("");
    setArchivePageNumber(1);
    setComparisonSelection([]);
    setArchiveComparison(null);
    setArchiveComparisonBusy(false);
    setExportingArchiveId("");
    setArchiveExportResult(null);
    setArchiveExportError("");
    const generation = ++projectGenerationRef.current;
    statusRequestRef.current += 1;
    archiveListRequestRef.current += 1;
    archiveDetailRequestRef.current += 1;
    archiveComparisonRequestRef.current += 1;
    archiveExportRequestRef.current += 1;
    ligandImportRequestRef.current += 1;
    void Promise.all([refresh(true), refreshArchives(true)]).then(([active, history]) => {
      if (
        generation === projectGenerationRef.current
        && !active?.screening
        && history.some((item) => item.valid)
      ) {
        setPanelTab("history");
      }
    });
    return () => {
      if (generation === projectGenerationRef.current) projectGenerationRef.current += 1;
      statusRequestRef.current += 1;
      archiveListRequestRef.current += 1;
      archiveDetailRequestRef.current += 1;
      archiveComparisonRequestRef.current += 1;
      archiveExportRequestRef.current += 1;
      ligandImportRequestRef.current += 1;
    };
  }, [projectDir, refresh, refreshArchives]);

  const displayState = panelTab === "history" ? archiveDetail?.screening ?? null : state;
  const displayLabels = useMemo(() => {
    if (panelTab !== "history" || !archiveDetail) return stagedLabels;
    const labels: Record<string, string> = {
      ...(archiveDetail.staged_labels ?? {}),
    };
    for (const item of archiveDetail.staged ?? []) {
      const label = item.original_name
        || item.source_file?.split(/[\\/]/).pop()
        || item.file.split(/[\\/]/).pop()
        || item.file;
      if (item.file) labels[item.file] = label;
      if (item.source_file) labels[item.source_file] = label;
    }
    for (const item of archiveDetail.screening.items ?? []) {
      const label = item.display_label || archiveDetail.item_labels?.[item.item_id];
      if (!label) continue;
      if (item.source_file) labels[item.source_file] = label;
      if (item.ligand_file) labels[item.ligand_file] = label;
    }
    return labels;
  }, [archiveDetail, panelTab, stagedLabels]);

  const counts = useMemo(() => {
    const items = displayState?.items ?? [];
    return {
      total: items.length,
      succeeded: items.filter((item) => item.status === "succeeded").length,
      failed: items.filter((item) => item.status === "failed").length,
      pending: items.filter((item) => ["pending", "running", "interrupted"].includes(item.status)).length,
    };
  }, [displayState]);

  const allItems = useMemo(() => displayState?.items ?? [], [displayState?.items]);
  const resultRankMap = useMemo(() => buildScreeningRankMap(allItems), [allItems]);
  const displayedResultItems = useMemo(() => {
    const source = topOnly
      ? selectTopScreeningItems(allItems, displayState?.top_n ?? 20)
      : allItems;
    return sortScreeningItems(
      filterScreeningItems(source, {
        query: resultQuery,
        status: resultStatus,
        stagedLabels: displayLabels,
      }),
      resultSort,
      displayLabels,
    );
  }, [
    allItems,
    resultQuery,
    resultSort,
    resultStatus,
    displayLabels,
    displayState?.top_n,
    topOnly,
  ]);
  const resultPage = useMemo(
    () => paginateScreeningItems(displayedResultItems, resultPageNumber, RESULT_PAGE_SIZE),
    [displayedResultItems, resultPageNumber],
  );
  const selectedPoseItem = useMemo(
    () => allItems.find((item) => item.item_id === selectedPoseItemId) ?? null,
    [allItems, selectedPoseItemId],
  );
  const selectedArchive = archiveDetail?.archive ?? null;
  const outputPaths = panelTab === "history"
    ? {
        summary_csv: archiveDetail?.files?.summary_csv ?? "",
        top_n_csv: archiveDetail?.files?.top_n_csv ?? "",
        report_md: archiveDetail?.files?.report_md ?? "",
      }
    : {
        summary_csv: state?.outputs?.summary_csv ?? "",
        top_n_csv: state?.outputs?.top_n_csv ?? "",
        report_md: state?.outputs?.report_md ?? "",
      };
  const archivePageCount = Math.max(1, Math.ceil(archives.length / ARCHIVE_PAGE_SIZE));
  const safeArchivePage = Math.min(archivePageCount, Math.max(1, archivePageNumber));
  const archivePageStart = (safeArchivePage - 1) * ARCHIVE_PAGE_SIZE;
  const visibleArchives = archives.slice(archivePageStart, archivePageStart + ARCHIVE_PAGE_SIZE);
  const advancedVinaSummary = useMemo(
    () => buildScreeningAdvancedVinaSummary(vina),
    [vina],
  );
  const frozenAdvancedVinaSummary = useMemo(
    () => buildScreeningAdvancedVinaSummary(displayState?.vina ?? {}),
    [displayState?.vina],
  );
  const frozenVinaHasAllAdvancedFields = useMemo(
    () => hasCompleteScreeningAdvancedVinaSettings(
      displayState?.vina,
      displayState?.compatibility?.inferred_vina_fields,
    ),
    [displayState?.compatibility?.inferred_vina_fields, displayState?.vina],
  );
  const frozenVinaHasSeed = Object.prototype.hasOwnProperty.call(
    displayState?.vina ?? {},
    "seed",
  );
  const persistedInterruption = panelTab === "current" && displayState?.status === "interrupted"
    ? displayState.last_integrity_error ?? displayState.last_runtime_error ?? null
    : null;
  const frozenProtocolWarnings = Array.from(new Set(
    [
      ...(displayState?.parameter_warnings ?? []),
      ...(displayState?.grid_resource?.warnings ?? []),
    ].map((value) => value.trim()).filter(Boolean),
  ));

  useEffect(() => {
    if (resultPage.page !== resultPageNumber) setResultPageNumber(resultPage.page);
  }, [resultPage.page, resultPageNumber]);

  useEffect(() => {
    if (selectedPoseItemId && !selectedPoseItem) setSelectedPoseItemId("");
  }, [selectedPoseItem, selectedPoseItemId]);

  useEffect(() => {
    setSelectedPoseItemId("");
    setResultPageNumber(1);
  }, [panelTab, selectedArchive?.archive_id]);

  useEffect(() => {
    if (!selectedPoseItemId) return;
    const previousOverflow = document.body.style.overflow;
    const returnFocus = poseTriggerRef.current;
    document.body.style.overflow = "hidden";
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setSelectedPoseItemId("");
        return;
      }
      if (event.key !== "Tab" || !poseDialogRef.current) return;
      const focusable = Array.from(
        poseDialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((element) => !element.hasAttribute("hidden"));
      if (!focusable.length) {
        event.preventDefault();
        poseDialogRef.current.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
      if (returnFocus?.isConnected) window.requestAnimationFrame(() => returnFocus.focus());
    };
  }, [selectedPoseItemId]);

  const chooseLigands = async (directory: boolean) => {
    const selected = await open({
      multiple: !directory,
      directory,
      title: directory ? "选择配体库文件夹" : "选择一个或多个配体",
      ...(directory
        ? {}
        : { filters: [{ name: "配体结构", extensions: ["pdbqt", "sdf", "mol"] }] }),
    });
    const files = Array.isArray(selected) ? selected : selected ? [selected] : [];
    if (!files.length) return;
    const request = ++ligandImportRequestRef.current;
    const generation = projectGenerationRef.current;
    setBusy(true);
    setImportingLigands(true);
    setRawError("");
    try {
      const parsed = parseResponse(await invoke<string>("stage_screening_inputs", { projectDir, files }));
      if (
        request !== ligandImportRequestRef.current
        || generation !== projectGenerationRef.current
      ) return;
      if (!parsed.ok) throw new Error(parsed.error?.message || "配体导入失败。");
      const preview = normalizeLigandImportPreview(parsed);
      const selection = defaultLigandSelection(preview);
      setLigandImportPreview(preview);
      setSelectedLigandCandidateIds(selection);
      if (selectedLigandFiles(preview, selection).length > 1) {
        onBatchModeDetected?.();
      }
      setMessage(parsed.message || `已读取 ${preview.counts.total} 条配体记录。`);
    } catch (error) {
      if (
        request === ligandImportRequestRef.current
        && generation === projectGenerationRef.current
      ) {
        setRawError(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (
        request === ligandImportRequestRef.current
        && generation === projectGenerationRef.current
      ) {
        setBusy(false);
        setImportingLigands(false);
      }
    }
  };

  const toggleImportedLigand = (candidateId: string) => {
    if (!ligandImportPreview) return;
    setSelectedLigandCandidateIds((current) => {
      const next = toggleLigandSelection(
        ligandImportPreview,
        current,
        candidateId,
      );
      if (selectedLigandFiles(ligandImportPreview, next).length > 1) {
        onBatchModeDetected?.();
      }
      return next;
    });
  };

  const retryPreparation = async () => {
    if (
      !ligandImportPreview
      || ligandImportPreview.schemaVersion !== 2
      || !ligandImportPreview.revisionSha256
      || !retryableCandidateIds.length
    ) return;
    const request = ++ligandImportRequestRef.current;
    const generation = projectGenerationRef.current;
    setBusy(true);
    setRetryingPreparation(true);
    setRawError("");
    try {
      const parsed = parseResponse(await invoke<string>("retry_screening_preparation", {
        projectDir,
        candidateIds: retryableCandidateIds,
        expectedStagingRevisionSha256: ligandImportPreview.revisionSha256,
      }));
      if (
        request !== ligandImportRequestRef.current
        || generation !== projectGenerationRef.current
      ) return;
      if (!parsed.ok) throw new Error(parsed.error?.message || "配体准备重试失败。");
      const preview = normalizeLigandImportPreview(parsed);
      const selection = defaultLigandSelection(preview);
      setLigandImportPreview(preview);
      setSelectedLigandCandidateIds(selection);
      if (collectSelectedLigandCandidateIds(preview, selection).length > 1) {
        onBatchModeDetected?.();
      }
      setMessage(parsed.message || "已重新准备可重试的失败记录。");
    } catch (error) {
      if (
        request === ligandImportRequestRef.current
        && generation === projectGenerationRef.current
      ) {
        setRawError(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (
        request === ligandImportRequestRef.current
        && generation === projectGenerationRef.current
      ) {
        setBusy(false);
        setRetryingPreparation(false);
      }
    }
  };

  const create = async (): Promise<boolean> => {
    if (!receptorFile || !stagedFiles.length) return false;
    setBusy(true);
    setRawError("");
    try {
      const parsed = parseResponse(await invoke<string>("create_screening", {
        projectDir,
        receptorFile,
        ligandFiles: stagedFiles,
        ligandCandidateIds: ligandImportPreview?.schemaVersion === 2
          ? selectedCandidateIds
          : undefined,
        expectedStagingRevisionSha256: ligandImportPreview?.schemaVersion === 2
          ? ligandImportPreview.revisionSha256
          : undefined,
        boxJson: JSON.stringify(box),
        vinaJson: serializeScreeningVinaSettings(vina, cpuPerTask),
        maxRetries,
        topN,
      }));
      if (!parsed.ok || !parsed.screening) throw new Error(parsed.error?.message || "批量筛选任务创建失败。");
      setState(parsed.screening);
      setPanelTab("current");
      setMessage(parsed.message || "批量筛选任务已创建。");
      return true;
    } catch (error) {
      setRawError(error instanceof Error ? error.message : String(error));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const createAndRun = async () => {
    if (await create()) await run();
  };

  const run = async () => {
    setBusy(true);
    setRawError("");
    let timer: number | null = null;
    try {
      const task = await startScreeningTask(projectDir);
      setMessage("批量筛选已进入后台串行队列。离开当前页面不会中断任务。");
      timer = window.setInterval(() => void refresh(true), 1500);
      const finished = await waitForBackgroundTask(task.task_id);
      if (finished.status === "failed") throw new Error(finished.error || finished.message || "批量筛选失败。");
      await refresh(true);
      setMessage(finished.status === "cancelled" ? "批量筛选已安全取消。" : "批量筛选队列已结束。");
    } catch (error) {
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      if (timer !== null) window.clearInterval(timer);
      setBusy(false);
      void refresh(true);
    }
  };

  const invokeStateCommand = async (command: "request_screening_cancel" | "resume_screening" | "archive_screening") => {
    setBusy(command !== "request_screening_cancel");
    setRawError("");
    try {
      const parsed = parseResponse(await invoke<string>(command, { projectDir }));
      if (!parsed.ok) throw new Error(parsed.error?.message || "批量筛选操作失败。");
      setState(parsed.screening ?? null);
      if (command === "archive_screening") {
        const archivedId = archiveIdFromResponse(parsed);
        poseTriggerRef.current = null;
        setState(null);
        setLigandImportPreview(null);
        setSelectedLigandCandidateIds(new Set());
        setSelectedPoseItemId("");
        setArchiveDetail(null);
        setPanelTab("history");
        await refreshArchives(true);
        if (archivedId) await loadArchive(archivedId);
      }
      setMessage(parsed.message || "操作已完成。");
    } catch (error) {
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
      void refresh(true);
    }
  };

  const exportReport = async () => {
    setBusy(true);
    setRawError("");
    try {
      const parsed = parseResponse(await invoke<string>("export_screening_report", { projectDir }));
      if (!parsed.ok || !parsed.screening) {
        throw new Error(parsed.error?.message || "批量筛选实验记录生成失败。");
      }
      setState(parsed.screening);
      setMessage(parsed.message || "批量筛选实验记录已生成。");
    } catch (error) {
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const copyOutputPath = async (relativePath: string) => {
    if (!relativePath) return;
    try {
      await navigator.clipboard.writeText(absoluteProjectPath(projectDir, relativePath));
      setMessage("文件路径已复制。");
    } catch (error) {
      setRawError(error instanceof Error ? error.message : "无法复制文件路径。");
    }
  };

  const copyArchiveExportValue = async (value: string, label: string) => {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      setMessage(`${label}已复制。`);
    } catch (error) {
      setArchiveExportError(
        error instanceof Error ? error.message : `无法复制${label}。`,
      );
    }
  };

  const resetResultPage = () => setResultPageNumber(1);
  const visibleStatus = displayState?.status || "";
  const visibleStatusLabel = visibleStatus
    ? statusLabels[visibleStatus] || visibleStatus
    : archiveComparison
      ? "批次比较"
    : panelTab === "history"
      ? `${archives.length} 个归档`
      : "尚未创建";

  return (
    <section className="run-cockpit-card batch-screening-panel" aria-labelledby="batch-screening-title">
      <div className="run-cockpit-section-heading">
        <div>
          <span className="run-cockpit-kicker">多配体任务</span>
          <h2 id="batch-screening-title">配体队列与批量运行</h2>
        </div>
        <StatusBadge tone={tone(visibleStatus || "idle")}>{visibleStatusLabel}</StatusBadge>
      </div>

      <div className="batch-screening-content">
        <div className="batch-screening-tabs" role="group" aria-label="批量筛选记录">
          <button
            type="button"
            aria-pressed={panelTab === "current"}
            className={panelTab === "current" ? "is-active" : ""}
            onClick={() => {
              poseTriggerRef.current = null;
              setSelectedPoseItemId("");
              archiveComparisonRequestRef.current += 1;
              setArchiveComparison(null);
              setArchiveComparisonBusy(false);
              setPanelTab("current");
            }}
          >
            当前任务
            {state ? <span>{statusLabels[state.status] || state.status}</span> : null}
          </button>
          <button
            type="button"
            aria-pressed={panelTab === "history"}
            className={panelTab === "history" ? "is-active" : ""}
            onClick={() => {
              poseTriggerRef.current = null;
              setSelectedPoseItemId("");
              setPanelTab("history");
              if (!archives.length) void refreshArchives(true);
            }}
          >
            历史归档
            <span>{archives.length}</span>
          </button>
        </div>

        {panelTab === "current" ? (
          <p className="batch-screening-intro">全部配体共用上方受体、Box 与 Vina 参数，并按可恢复队列依次运行。CPU 表示每个配体任务的线程数。</p>
        ) : archiveComparison ? null : archiveDetail ? (
          <>
            <div className="batch-screening-archive-heading">
              <button
                type="button"
                onClick={() => {
                  poseTriggerRef.current = null;
                  setArchiveDetail(null);
                  setSelectedPoseItemId("");
                }}
              >
                <CaretLeft aria-hidden="true" size={16} />返回归档列表
              </button>
              <div>
                <span>只读历史记录</span>
                <strong>{archiveDetail.archive.screening_id || archiveDetail.archive.archive_id}</strong>
                <small>
                  归档于 {formatArchiveTime(archiveDetail.archive.archived_at)}
                  {archiveDetail.archive.integrity === "state_verified" ? " · 状态快照已核对" : ""}
                  {archiveDetail.attempt_integrity?.status === "verified" ? " · 逐次运行证据已核对" : ""}
                </small>
              </div>
              <ActionButton
                className="batch-screening-archive-heading-action"
                variant="secondary"
                disabled={Boolean(exportingArchiveId)}
                aria-label={`导出 ${archiveDetail.archive.archive_id} ZIP`}
                onClick={(event) => void exportArchiveZip(
                  archiveDetail.archive.archive_id,
                  event.currentTarget,
                )}
              >
                {exportingArchiveId === archiveDetail.archive.archive_id
                  ? <SpinnerGap className="run-monitor-spinner" size={15} />
                  : <DownloadSimple aria-hidden="true" size={16} />}
                {exportingArchiveId === archiveDetail.archive.archive_id ? "正在导出…" : "导出 ZIP"}
              </ActionButton>
            </div>
            {archiveDetail.attempt_integrity?.status
              && !["verified", "not_applicable"].includes(archiveDetail.attempt_integrity.status) ? (
              <p className="batch-screening-boundary" role="status">
                该历史记录缺少部分逐次运行证据；可以读取，但不能视为完整验证。
              </p>
            ) : null}
          </>
        ) : (
          <div className="batch-screening-history-heading">
            <div>
              <span>历史筛选</span>
              <strong>已归档的批量任务</strong>
              <small>归档内容只读，不会与当前任务的输入或输出混用。</small>
            </div>
            <ActionButton variant="text" disabled={archiveListBusy} onClick={() => void refreshArchives()}>
              {archiveListBusy ? <SpinnerGap className="run-monitor-spinner" size={15} /> : null}
              刷新归档
            </ActionButton>
          </div>
        )}

        {panelTab === "history" && archiveExportResult ? (
          <section className="batch-screening-export-result" role="status" aria-label="最近导出的批量归档">
            <DownloadSimple aria-hidden="true" size={22} />
            <div className="batch-screening-export-result-copy">
              <span>ZIP 已导出 · {archiveExportResult.archive_id}</span>
              <strong title={archiveExportResult.zip_file}>{archiveExportResult.zip_file}</strong>
              <small>
                {formatArchiveExportBytes(archiveExportResult.size_bytes)}
                {" · "}
                {archiveExportResult.entry_count} 个条目
                {" · "}
                {archiveExportIntegrityLabel(archiveExportResult.source_integrity)}
              </small>
              <code title={archiveExportResult.zip_sha256}>SHA256 {archiveExportResult.zip_sha256}</code>
              {archiveExportResult.warnings.map((warning, index) => (
                <em key={`${index}-${warning}`}>{warning}</em>
              ))}
            </div>
            <div className="batch-screening-export-result-actions">
              <ActionButton
                variant="text"
                onClick={() => void copyArchiveExportValue(archiveExportResult.zip_file, "ZIP 路径")}
              >
                <Copy aria-hidden="true" size={14} />复制路径
              </ActionButton>
              <ActionButton
                variant="text"
                onClick={() => void copyArchiveExportValue(archiveExportResult.zip_sha256, "ZIP SHA256")}
              >
                <Copy aria-hidden="true" size={14} />复制 SHA256
              </ActionButton>
            </div>
          </section>
        ) : null}
        {panelTab === "history" && archiveExportError ? (
          <p className="batch-screening-export-error" role="alert">{archiveExportError}</p>
        ) : null}

        {panelTab === "history" && archiveComparison ? (
          <ScreeningArchiveComparisonView
            comparison={archiveComparison}
            onBack={() => {
              archiveComparisonRequestRef.current += 1;
              setArchiveComparison(null);
              setArchiveComparisonBusy(false);
              setArchiveError("");
            }}
          />
        ) : panelTab === "history" && !archiveDetail ? (
          <div
            className="batch-screening-archive-list"
            aria-busy={Boolean(exportingArchiveId)}
            aria-live="polite"
          >
            {archives.some((archive) => archive.valid) ? (
              <div className="batch-screening-compare-toolbar">
                <Scales aria-hidden="true" size={20} />
                <div>
                  <strong>
                    {comparisonSelection.length === 0
                      ? "选择两个归档进行比较"
                      : comparisonSelection.length === 1
                        ? "已选择基线，请再选择对照"
                        : "基线与对照已选择"}
                  </strong>
                  <span>配体按冻结输入 SHA256 匹配；差值固定为“对照－基线”。</span>
                </div>
                <ActionButton
                  variant="secondary"
                  disabled={comparisonSelection.length !== 2 || archiveComparisonBusy}
                  onClick={() => void compareSelectedArchives()}
                >
                  {archiveComparisonBusy
                    ? <SpinnerGap className="run-monitor-spinner" size={15} />
                    : <Scales aria-hidden="true" size={16} />}
                  {archiveComparisonBusy ? "正在核对…" : "比较所选批次"}
                </ActionButton>
              </div>
            ) : null}
            {archiveListBusy && !archives.length ? (
              <div className="batch-screening-archive-empty">
                <SpinnerGap className="run-monitor-spinner" size={22} />
                <span>正在读取历史归档…</span>
              </div>
            ) : archives.length ? visibleArchives.map((archive) => (
              <article
                key={archive.archive_id}
                className={`batch-screening-archive-card ${archive.valid ? "" : "is-invalid"} ${comparisonSelection.includes(archive.archive_id) ? "is-selected" : ""}`}
              >
                <div className="batch-screening-archive-card-main">
                  {archive.valid ? (
                    <label className="batch-screening-archive-select">
                      <input
                        type="checkbox"
                        checked={comparisonSelection.includes(archive.archive_id)}
                        disabled={
                          archiveComparisonBusy
                          || archiveDetailBusy
                          || (
                            comparisonSelection.length >= 2
                            && !comparisonSelection.includes(archive.archive_id)
                          )
                        }
                        onChange={() => toggleComparisonArchive(archive.archive_id)}
                        aria-label={`选择 ${archive.screening_id || archive.archive_id} 用于批次比较`}
                      />
                      <span>
                        {comparisonSelection[0] === archive.archive_id
                          ? "基线"
                          : comparisonSelection[1] === archive.archive_id
                            ? "对照"
                            : "选择"}
                      </span>
                    </label>
                  ) : null}
                  <div>
                    <span>{formatArchiveTime(archive.archived_at)}</span>
                    <strong>{archive.screening_id || archive.archive_id}</strong>
                    <code>{archive.archive_id}</code>
                  </div>
                  <StatusBadge tone={archive.valid ? tone(archive.status || "idle") : "error"}>
                    {archive.valid ? statusLabels[archive.status || ""] || archive.status || "已归档" : "完整性异常"}
                  </StatusBadge>
                </div>
                {archive.valid ? (
                  <>
                    <div className="batch-screening-archive-stats">
                      <span>配体 <strong>{archive.counts.total}</strong></span>
                      <span>成功 <strong>{archive.counts.succeeded}</strong></span>
                      <span>失败 <strong>{archive.counts.failed}</strong></span>
                      <span>
                        最佳评分{" "}
                        <strong>
                          {typeof archive.best_affinity_kcal_mol === "number"
                            ? `${archive.best_affinity_kcal_mol.toFixed(3)} kcal/mol`
                            : "—"}
                        </strong>
                      </span>
                    </div>
                    <div className="batch-screening-archive-card-actions">
                      <span>{archive.report_available ? "含实验记录" : "未生成实验记录"}</span>
                      <ActionButton
                        variant="secondary"
                        disabled={Boolean(exportingArchiveId) || archiveDetailBusy}
                        aria-label={`导出 ${archive.archive_id} ZIP`}
                        onClick={(event) => void exportArchiveZip(
                          archive.archive_id,
                          event.currentTarget,
                        )}
                      >
                        {exportingArchiveId === archive.archive_id
                          ? <SpinnerGap className="run-monitor-spinner" size={15} />
                          : <DownloadSimple aria-hidden="true" size={16} />}
                        {exportingArchiveId === archive.archive_id ? "正在导出…" : "导出 ZIP"}
                      </ActionButton>
                      <ActionButton
                        variant="secondary"
                        disabled={archiveDetailBusy || Boolean(exportingArchiveId)}
                        onClick={() => void loadArchive(archive.archive_id)}
                      >
                        {loadingArchiveId === archive.archive_id
                          ? <SpinnerGap className="run-monitor-spinner" size={15} />
                          : null}
                        {loadingArchiveId === archive.archive_id ? "正在打开…" : "查看结果"}
                      </ActionButton>
                    </div>
                  </>
                ) : (
                  <p role="alert">{archive.error?.message || "归档状态或清单未通过完整性检查，已阻止打开。"}</p>
                )}
              </article>
            )) : (
              <div className="batch-screening-archive-empty">
                <TrayArrowDown size={24} />
                <strong>还没有历史归档</strong>
                <span>完成或取消当前批量任务后，可以将它归档到这里。</span>
              </div>
            )}
            {archives.length > ARCHIVE_PAGE_SIZE ? (
              <footer className="batch-screening-pagination batch-screening-archive-pagination">
                <span>
                  {archivePageStart + 1}–{Math.min(archivePageStart + visibleArchives.length, archives.length)} / {archives.length}
                </span>
                <div>
                  <button
                    type="button"
                    disabled={safeArchivePage <= 1}
                    onClick={() => setArchivePageNumber((current) => Math.max(1, current - 1))}
                    aria-label="上一页归档"
                  >
                    <CaretLeft size={16} />
                  </button>
                  <strong>{safeArchivePage} / {archivePageCount}</strong>
                  <button
                    type="button"
                    disabled={safeArchivePage >= archivePageCount}
                    onClick={() => setArchivePageNumber((current) => Math.min(archivePageCount, current + 1))}
                    aria-label="下一页归档"
                  >
                    <CaretRight size={16} />
                  </button>
                </div>
              </footer>
            ) : null}
            {archiveError ? <AdvancedDetails summary="查看历史归档诊断"><pre>{archiveError}</pre></AdvancedDetails> : null}
          </div>
        ) : displayState ? (
          <>
            <div className="batch-screening-metrics">
              <div><span>总数</span><strong>{counts.total}</strong></div>
              <div><span>成功</span><strong>{counts.succeeded}</strong></div>
              <div><span>待处理</span><strong>{counts.pending}</strong></div>
              <div><span>失败</span><strong>{counts.failed}</strong></div>
            </div>
            {persistedInterruption ? (
              <div className="batch-screening-persisted-warning" role="alert">
                <strong>
                  {displayState?.last_integrity_error ? "队列因完整性检查中断" : "队列运行中断"}
                </strong>
                <span>{persistedInterruption.message || persistedInterruption.code || "请查看诊断后再恢复队列。"}</span>
              </div>
            ) : null}
            <section className="batch-screening-protocol" aria-label={panelTab === "history" ? "归档冻结协议" : "当前队列冻结协议"}>
              <header>
                <div>
                  <span>{panelTab === "history" ? "归档冻结协议" : "队列冻结协议"}</span>
                  <strong>本次筛选使用的 Box 与 Vina 参数</strong>
                </div>
                <small>
                  {frozenVinaHasAllAdvancedFields
                    ? panelTab === "history"
                      ? "来自归档快照"
                      : "创建队列后不再随项目参数变化"
                    : "旧记录未完整保存高级字段，以下按 Vina 兼容默认值解释"}
                </small>
              </header>
              <dl className="batch-screening-protocol-core">
                <div>
                  <dt>Box 中心（Å）</dt>
                  <dd>
                    {(["x", "y", "z"] as const)
                      .map((axis) => formatProtocolNumber(displayState.box?.[`center_${axis}`]))
                      .join(" / ")}
                  </dd>
                </div>
                <div>
                  <dt>Box 尺寸（Å）</dt>
                  <dd>
                    {(["x", "y", "z"] as const)
                      .map((axis) => formatProtocolNumber(displayState.box?.[`size_${axis}`]))
                      .join(" × ")}
                  </dd>
                </div>
                <div>
                  <dt>基础 Vina</dt>
                  <dd>
                    {formatScreeningProtocol(displayState.vina?.scoring)} · exhaustiveness{" "}
                    {formatProtocolNumber(displayState.vina?.exhaustiveness)} · 构象{" "}
                    {formatProtocolNumber(displayState.vina?.num_modes)} · 能量范围{" "}
                    {formatProtocolNumber(displayState.vina?.energy_range)} kcal/mol
                  </dd>
                </div>
                <div>
                  <dt>执行</dt>
                  <dd>
                    CPU {formatProtocolNumber(displayState.vina?.cpu)} · seed{" "}
                    {!frozenVinaHasSeed
                      ? "未记录"
                      : displayState.vina?.seed == null
                      ? "随机"
                      : formatProtocolNumber(displayState.vina.seed)}{" "}
                    · 重试 {formatProtocolNumber(displayState.max_retries)} · Top{" "}
                    {formatProtocolNumber(displayState.top_n)}
                  </dd>
                </div>
              </dl>
              <dl className="batch-screening-protocol-advanced">
                {frozenAdvancedVinaSummary.map((item) => (
                  <div key={item.key}>
                    <dt>{item.label}</dt>
                    <dd>{item.value}</dd>
                  </div>
                ))}
              </dl>
            </section>
            {frozenProtocolWarnings.map((warning) => (
              <p className="batch-screening-persisted-warning" role="status" key={warning}>
                {warning}
              </p>
            ))}
            {panelTab === "current" ? (
              <div className="batch-screening-actions">
                {displayState.status === "ready" ? <ActionButton variant="primary" disabled={busy || disabled} onClick={() => void run()}><Play size={16} weight="fill" />开始串行批量筛选</ActionButton> : null}
                {displayState.status === "running" || displayState.status === "cancel_requested" ? <ActionButton variant="secondary" disabled={displayState.status === "cancel_requested"} onClick={() => void invokeStateCommand("request_screening_cancel")}><Stop size={16} />{displayState.status === "cancel_requested" ? "等待当前配体结束" : "安全取消"}</ActionButton> : null}
                {["canceled", "interrupted"].includes(displayState.status) ? <ActionButton variant="primary" disabled={busy || disabled} onClick={() => void invokeStateCommand("resume_screening")}><Play size={16} />恢复队列</ActionButton> : null}
                {terminal(displayState.status) ? (
                  <ActionButton variant="secondary" disabled={busy} onClick={() => void exportReport()}>
                    <FileText size={16} />{displayState.outputs?.report_md ? "更新实验记录" : "生成实验记录"}
                  </ActionButton>
                ) : null}
                {terminal(displayState.status) ? <ActionButton variant="secondary" disabled={busy} onClick={() => void invokeStateCommand("archive_screening")}><TrayArrowDown size={16} />归档本次筛选</ActionButton> : null}
                <ActionButton variant="text" disabled={busy} onClick={() => void refresh()}>{busy ? <SpinnerGap className="run-monitor-spinner" size={15} /> : null}刷新状态</ActionButton>
              </div>
            ) : null}
            {outputPaths.summary_csv ? (
              <div className="batch-screening-output">
                <CheckCircle aria-hidden="true" size={18} weight="fill" />
                <div>
                  <strong>结果文件已写入项目</strong>
                  {[
                    ["完整汇总", outputPaths.summary_csv],
                    ["Top N", outputPaths.top_n_csv],
                    ["实验记录", outputPaths.report_md],
                  ].filter((entry): entry is [string, string] => Boolean(entry[1])).map(([label, path]) => (
                    <span className="batch-screening-output-row" key={label}>
                      <i>{label}</i>
                      <code>{path}</code>
                      <button type="button" onClick={() => void copyOutputPath(path)} title={`复制${label}路径`} aria-label={`复制${label}路径`}>
                        <Copy size={15} />
                      </button>
                    </span>
                  ))}
                </div>
              </div>
            ) : null}
            <section className="batch-screening-workspace" aria-labelledby="batch-screening-results-title">
              <header className="batch-screening-workspace-header">
                <div>
                  <span>{panelTab === "history" ? "归档结果" : "完整结果"}</span>
                  <h3 id="batch-screening-results-title">{panelTab === "history" ? "历史配体结果" : "配体结果工作区"}</h3>
                </div>
                <small>
                  {displayedResultItems.length === allItems.length
                    ? `共 ${allItems.length} 项`
                    : `显示 ${displayedResultItems.length} / ${allItems.length} 项`}
                </small>
              </header>

              <div className="batch-screening-result-controls">
                <label className="batch-screening-search">
                  <MagnifyingGlass aria-hidden="true" size={17} />
                  <input
                    type="search"
                    aria-label="搜索配体结果"
                    value={resultQuery}
                    placeholder="搜索配体名称、编号或错误"
                    onChange={(event) => {
                      setResultQuery(event.target.value);
                      resetResultPage();
                    }}
                  />
                </label>
                <div className="batch-screening-status-filter" role="group" aria-label="结果状态筛选">
                  {([
                    ["all", "全部"],
                    ["succeeded", `成功 ${counts.succeeded}`],
                    ["failed", `失败 ${counts.failed}`],
                    ["pending", `待处理 ${counts.pending}`],
                  ] as Array<[ScreeningResultStatusFilter, string]>).map(([value, label]) => (
                    <button
                      type="button"
                      key={value}
                      className={resultStatus === value ? "is-active" : ""}
                      aria-pressed={resultStatus === value}
                      onClick={() => {
                        setResultStatus(value);
                        resetResultPage();
                      }}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <label className="batch-screening-sort">
                  <span>排序</span>
                  <select
                    value={resultSort}
                    onChange={(event) => {
                      setResultSort(event.target.value as ScreeningResultSort);
                      resetResultPage();
                    }}
                  >
                    <option value="score">最佳评分</option>
                    <option value="order">导入顺序</option>
                    <option value="name">配体名称</option>
                    <option value="status">任务状态</option>
                  </select>
                </label>
                <label className="batch-screening-top-toggle">
                  <input
                    type="checkbox"
                    checked={topOnly}
                    onChange={(event) => {
                      setTopOnly(event.target.checked);
                      resetResultPage();
                    }}
                  />
                  仅看 Top {displayState.top_n ?? 20}
                </label>
              </div>

              <div
                className="batch-screening-table-wrap"
                role="region"
                aria-label="配体结果表，可横向滚动"
                tabIndex={0}
              >
                <table className="batch-screening-results">
                  <thead>
                    <tr>
                      <th scope="col">排名 / 顺序</th>
                      <th scope="col">配体</th>
                      <th scope="col">状态</th>
                      <th scope="col">尝试</th>
                      <th scope="col">最佳评分</th>
                      <th scope="col"><span aria-label="操作">操作</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {resultPage.items.map((item) => {
                      const rank = resultRankMap.get(item.item_id);
                      const displayName = item.display_label || screeningItemDisplayName(item, displayLabels);
                      return (
                        <tr key={item.item_id}>
                          <td>
                            <strong>{rank ? `#${rank}` : "—"}</strong>
                            <small>导入 {item.order ?? "—"}</small>
                          </td>
                          <td>
                            <span title={item.source_file || item.ligand_file || item.item_id}>{displayName}</span>
                            <small>{item.item_id}</small>
                            {item.last_error ? <em title={item.last_error}>{item.last_error}</em> : null}
                          </td>
                          <td><StatusBadge tone={tone(item.status)}>{statusLabels[item.status] || item.status}</StatusBadge></td>
                          <td>{item.attempt_count ?? 0}</td>
                          <td>
                            {typeof item.best_affinity_kcal_mol === "number" && Number.isFinite(item.best_affinity_kcal_mol)
                              ? <code>{item.best_affinity_kcal_mol.toFixed(3)} kcal/mol</code>
                              : "—"}
                          </td>
                          <td>
                            {item.status === "succeeded" ? (
                              <button
                                type="button"
                                className="batch-screening-view-button"
                                aria-haspopup="dialog"
                                onClick={(event) => {
                                  poseTriggerRef.current = event.currentTarget;
                                  setSelectedPoseItemId(item.item_id);
                                }}
                              >
                                <Eye aria-hidden="true" size={16} />查看构象
                              </button>
                            ) : "—"}
                          </td>
                        </tr>
                      );
                    })}
                    {!resultPage.items.length ? (
                      <tr className="batch-screening-empty-row">
                        <td colSpan={6}>当前筛选条件下没有配体结果。</td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>

              <footer className="batch-screening-pagination">
                <span>{resultPage.total ? `${resultPage.start}–${resultPage.end} / ${resultPage.total}` : "0 项"}</span>
                <div>
                  <button
                    type="button"
                    disabled={resultPage.page <= 1}
                    onClick={() => setResultPageNumber((current) => Math.max(1, current - 1))}
                    aria-label="上一页"
                  >
                    <CaretLeft size={16} />
                  </button>
                  <strong>{resultPage.page} / {resultPage.pageCount}</strong>
                  <button
                    type="button"
                    disabled={resultPage.page >= resultPage.pageCount}
                    onClick={() => setResultPageNumber((current) => current + 1)}
                    aria-label="下一页"
                  >
                    <CaretRight size={16} />
                  </button>
                </div>
              </footer>
            </section>
            {displayState.outputs?.sdf && !displayState.outputs.sdf.generated ? <p className="batch-screening-boundary">未生成 SDF：{displayState.outputs.sdf.reason}</p> : null}
          </>
        ) : (
          <>
            <section className="batch-screening-import">
              <header>
                <div>
                  <span>配体库</span>
                  <strong>
                    {ligandImportPreview
                      ? `已选 ${selectedCandidateIds.length} / ${ligandImportPreview.counts.ready} 个可用配体`
                      : "导入配体文件或文件夹"}
                  </strong>
                  <small>PDBQT 可直接加入；SDF 与 MOL 会逐条准备并列出失败记录。</small>
                </div>
                <div className="batch-screening-import-actions">
                  <ActionButton
                    variant="secondary"
                    disabled={disabled || busy}
                    onClick={() => void chooseLigands(false)}
                  >
                    <FolderOpen size={16} />
                    选择文件
                  </ActionButton>
                  <ActionButton
                    variant="secondary"
                    disabled={disabled || busy}
                    onClick={() => void chooseLigands(true)}
                  >
                    <FolderOpen size={16} />
                    选择文件夹
                  </ActionButton>
                </div>
              </header>

              {ligandImportPreview ? (
                <>
                  <div className="batch-screening-import-metrics" aria-label="配体导入统计">
                    <div><span>记录</span><strong>{ligandImportPreview.counts.total}</strong></div>
                    <div><span>可用</span><strong>{ligandImportPreview.counts.ready}</strong></div>
                    <div><span>重复</span><strong>{ligandImportPreview.counts.duplicate}</strong></div>
                    <div><span>需审查</span><strong>{ligandImportPreview.counts.reviewRequired}</strong></div>
                    <div><span>失败</span><strong>{ligandImportPreview.counts.invalid}</strong></div>
                    <div><span>已选</span><strong>{selectedCandidateIds.length}</strong></div>
                  </div>
                  <div className="batch-screening-import-toolbar">
                    <span>
                      {topologyCoverage?.status === "complete"
                        ? `已选记录的原始拓扑已全部冻结（${topologyCoverage.verified}/${topologyCoverage.selected}）`
                        : topologyCoverage?.status === "partial"
                          ? `原始拓扑部分可追溯（${topologyCoverage.verified}/${topologyCoverage.selected}）`
                          : "所选记录没有可验证的原始拓扑"}
                    </span>
                    <div>
                      <button
                        type="button"
                        disabled={busy || !retryableCandidateIds.length}
                        onClick={() => void retryPreparation()}
                      >
                        重试准备{retryableCandidateIds.length ? `（${retryableCandidateIds.length}）` : ""}
                      </button>
                      <button
                        type="button"
                        disabled={!ligandImportPreview.counts.ready}
                        onClick={() => {
                          const next = selectAllReadyLigands(ligandImportPreview);
                          setSelectedLigandCandidateIds(next);
                          if (selectedLigandFiles(ligandImportPreview, next).length > 1) {
                            onBatchModeDetected?.();
                          }
                        }}
                      >
                        全选可用
                      </button>
                      <button
                        type="button"
                        disabled={!selectedLigandCandidateIds.size}
                        onClick={() => setSelectedLigandCandidateIds(new Set())}
                      >
                        清空选择
                      </button>
                    </div>
                  </div>
                  <div className="batch-screening-import-table-wrap">
                    <table className="batch-screening-import-table">
                      <thead>
                        <tr>
                          <th aria-label="选择" />
                          <th>配体</th>
                          <th>来源</th>
                          <th>状态</th>
                        </tr>
                      </thead>
                      <tbody>
                        {ligandImportPreview.candidates.map((candidate) => (
                          <tr key={candidate.id} data-status={candidate.status}>
                            <td>
                              <input
                                type="checkbox"
                                aria-label={`选择 ${candidate.displayName}`}
                                checked={selectedLigandCandidateIds.has(candidate.id)}
                                disabled={candidate.status !== "ready" || !candidate.stagedFile}
                                onChange={() => toggleImportedLigand(candidate.id)}
                              />
                            </td>
                            <td>
                              <strong>{candidate.displayName}</strong>
                              <small>{candidate.sourceFormat.toUpperCase()} · {ligandImportSizeLabel(candidate.sizeBytes)}</small>
                              <small>
                                {candidate.chemicalFacts?.status === "verified"
                                  ? `形式电荷 ${candidate.chemicalFacts.formalCharge} · 重原子 ${candidate.chemicalFacts.heavyAtomCount} · 可旋转键 ${candidate.chemicalFacts.rotatableBondCount}`
                                  : "化学事实不可用"}
                              </small>
                            </td>
                            <td title={candidate.sourceFile}>
                              <strong>{candidate.originalName}</strong>
                              <small>记录 #{candidate.recordIndex}</small>
                            </td>
                            <td>
                              <span className={`batch-screening-import-status is-${candidate.status}`}>
                                {ligandImportStatusLabel(candidate.status)}
                              </span>
                              <small>
                                {candidate.issue?.message
                                  || (candidate.status === "duplicate"
                                    ? "与已保留记录相同"
                                    : "可加入筛选队列")}
                              </small>
                              {candidate.preparationAttempts.length ? (
                                <small>
                                  已准备 {candidate.preparationAttempts.length} 次
                                  {candidate.retryable ? " · 可重试" : ""}
                                </small>
                              ) : null}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {ligandImportPreview.failureManifest ? (
                    <AdvancedDetails
                      summary={`准备失败清单：${ligandImportPreview.failureManifest.failureCount} 条`}
                    >
                      <dl className="batch-screening-protocol-list">
                        <div>
                          <dt>JSON</dt>
                          <dd><code>{ligandImportPreview.failureManifest.jsonFile}</code></dd>
                        </div>
                        <div>
                          <dt>JSON SHA256</dt>
                          <dd><code>{ligandImportPreview.failureManifest.jsonSha256}</code></dd>
                        </div>
                        <div>
                          <dt>CSV</dt>
                          <dd><code>{ligandImportPreview.failureManifest.csvFile}</code></dd>
                        </div>
                        <div>
                          <dt>CSV SHA256</dt>
                          <dd><code>{ligandImportPreview.failureManifest.csvSha256}</code></dd>
                        </div>
                      </dl>
                    </AdvancedDetails>
                  ) : null}
                </>
              ) : null}
            </section>
            <div className="batch-screening-create-grid">
              <label><span>单任务 CPU</span><input type="number" min={1} max={MAX_SCREENING_CPU} step={1} value={cpuPerTask} disabled={disabled || busy} onChange={(event) => setCpuPerTask(clampIntegerInput(event.target.value, 1, MAX_SCREENING_CPU, 1))} /><small>每次只运行一个配体</small></label>
              <label><span>失败重试</span><input type="number" min={0} max={MAX_SCREENING_RETRIES} step={1} value={maxRetries} disabled={disabled || busy} onChange={(event) => setMaxRetries(clampIntegerInput(event.target.value, 0, MAX_SCREENING_RETRIES, 0))} /><small>只重试失败项</small></label>
              <label><span>汇总 Top N</span><input type="number" min={1} max={MAX_SCREENING_LIGANDS} step={1} value={topN} disabled={disabled || busy} onChange={(event) => setTopN(clampIntegerInput(event.target.value, 1, MAX_SCREENING_LIGANDS, 1))} /><small>用于结果排行</small></label>
            </div>
            <div className="batch-screening-library" aria-label="批量筛选高级 Vina 参数">
              <div>
                <span>高级 Vina 参数</span>
                <strong>6 项设置将在创建队列时冻结</strong>
                <small>{advancedVinaSummary.map((item) => `${item.label} ${item.value}`).join(" · ")}</small>
                <small>显式未结合体系能量只用于刚性单配体姿势评分，不进入批量筛选。</small>
              </div>
            </div>
            <div className="batch-screening-actions">
              <ActionButton variant="secondary" disabled={disabled || busy || !receptorFile || !stagedFiles.length} onClick={() => void create()}>仅创建队列</ActionButton>
              <ActionButton variant="primary" disabled={disabled || busy || !receptorFile || !stagedFiles.length} onClick={() => void createAndRun()}><Play size={16} weight="fill" />创建并开始对接</ActionButton>
            </div>
          </>
        )}

        {disabled && disabledReason ? <p className="batch-screening-boundary">{disabledReason}</p> : null}
        {message ? <p className="batch-screening-message" role="status">{message}</p> : null}
        {rawError ? <AdvancedDetails summary="查看批量筛选诊断"><pre>{rawError}</pre></AdvancedDetails> : null}
      </div>
      {selectedPoseItem?.status === "succeeded" ? (
        <div
          className="batch-screening-pose-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setSelectedPoseItemId("");
          }}
        >
          <div
            ref={poseDialogRef}
            className="batch-screening-pose-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="batch-screening-pose-title"
            aria-describedby="batch-screening-pose-description"
            tabIndex={-1}
          >
            <header>
              <div>
                <span>批量筛选构象</span>
                <h2 id="batch-screening-pose-title">{selectedPoseItem.display_label || screeningItemDisplayName(selectedPoseItem, displayLabels)}</h2>
                <p>
                  {selectedPoseItem.best_affinity_kcal_mol != null
                    ? `最佳评分 ${selectedPoseItem.best_affinity_kcal_mol.toFixed(3)} kcal/mol`
                    : "最佳输出构象"}
                  <i aria-hidden="true">·</i>
                  {selectedPoseItem.item_id}
                </p>
              </div>
              <button type="button" autoFocus onClick={() => setSelectedPoseItemId("")} aria-label="关闭构象查看">
                <X size={22} />
              </button>
            </header>
            <div className="batch-screening-pose-body">
              <Suspense fallback={<div className="batch-screening-pose-loading"><SpinnerGap className="run-monitor-spinner" size={24} />正在加载 3D 查看器…</div>}>
                <PoseStructurePreview
                  projectDir={projectDir}
                  screeningItemId={selectedPoseItem.item_id}
                  screeningArchiveId={panelTab === "history" ? selectedArchive?.archive_id : undefined}
                  mode={1}
                  poseLabel="最佳构象（Mode 1）"
                  className="batch-screening-pose-viewer"
                />
              </Suspense>
            </div>
            <footer>
              <p id="batch-screening-pose-description">
                {panelTab === "history"
                  ? "显示所选归档冻结的受体与该配体最佳输出的 Mode 1；归档身份、路径和文件完整性会在加载前核对。"
                  : "显示本次筛选冻结的受体与该配体最佳输出的 Mode 1；文件完整性会在加载前核对。"}
              </p>
              <ActionButton variant="secondary" onClick={() => setSelectedPoseItemId("")}>关闭</ActionButton>
            </footer>
          </div>
        </div>
      ) : null}
      <OperationLoadingDialog
        open={Boolean(exportingArchiveId)}
        title="正在导出批量筛选归档"
        message="正在重新核对归档并写入 ZIP 文件。"
        detail={`原归档不会被修改${exportingArchiveId ? ` · ${exportingArchiveId}` : ""}`}
      />
      <OperationLoadingDialog
        open={importingLigands || retryingPreparation}
        title={retryingPreparation ? "正在重试配体准备" : "正在导入配体库"}
        message={retryingPreparation
          ? "正在重新准备可重试的失败记录并生成新的 staging revision。"
          : "正在逐条读取、准备并核对配体记录。"}
        detail="完成后可选择进入队列的配体"
      />
    </section>
  );
}
