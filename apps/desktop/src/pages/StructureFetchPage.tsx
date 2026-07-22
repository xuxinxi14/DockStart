import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { CaretDown, CaretUp, X } from "@phosphor-icons/react";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import CommandResultPanel from "../components/CommandResultPanel";
import { BodyGrid, MainPanel, PageHero, PageShell } from "../components/layout/PageLayout";
import StatusBadge from "../components/StatusBadge";
import WarningCallout from "../components/WarningCallout";
import type {
  CandidateStructurePreviewResponse,
  DockStartProject,
  PreparationStatusResponse,
  PreparationTarget,
  ProjectResponse,
  RawStructureStatus,
  RunFileStatus,
  StructureSearchCandidate,
  StructureSearchResponse,
} from "../types";
import {
  startPdbFetchTask,
  startPreparationTask,
  startPubchemFetchTask,
  waitForBackgroundTask,
  type BackgroundTaskStatus,
} from "../utils/backgroundTasks";
import { PageOperationScope, type PageOperationToken } from "../utils/pageOperationScope";
import { runRawToPreparedWorkflow } from "../utils/rawToPreparedWorkflow";
import { writeDockingWorkspaceMode } from "../utils/dockingMode";

const CandidateStructurePreview = lazy(() => import("../components/CandidateStructurePreview"));

type StructureFetchPageProps = {
  project: DockStartProject;
  onBack: () => void;
  onOpenImportPdbqt: (project: DockStartProject) => void;
  onOpenPreparation: (project: DockStartProject) => void;
  onProjectChange: (project: DockStartProject) => void;
};

type RcsbQueryType = "auto" | "pdb_id" | "keyword";
type PubchemQueryType = "auto" | "cid" | "name" | "keyword";
type BusyAction =
  | "refresh"
  | "search-receptor"
  | "search-ligand"
  | "prepare-receptor"
  | "prepare-ligand"
  | null;

type CandidatePreviewState = {
  candidateId: string;
  label: string;
  response: CandidateStructurePreviewResponse;
};

type CandidateDetailRow = {
  label: string;
  value: string;
  wide?: boolean;
};

const DEFAULT_SEARCH_LIMIT = 8;
const MAX_SEARCH_LIMIT = 20;
const SEARCH_HISTORY_LIMIT = 8;
const RCSB_SEARCH_HISTORY_KEY = "dockstart.search-history.rcsb";
const PUBCHEM_SEARCH_HISTORY_KEY = "dockstart.search-history.pubchem";

type SearchHistoryInputProps = {
  disabled: boolean;
  history: string[];
  id: string;
  onChange: (value: string) => void;
  onDelete: (value: string) => void;
  placeholder: string;
  value: string;
};

function readSearchHistory(storageKey: string): string[] {
  if (typeof window === "undefined") return [];
  try {
    const stored = window.localStorage.getItem(storageKey);
    if (!stored) return [];
    const parsed = JSON.parse(stored);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item): item is string => typeof item === "string" && Boolean(item.trim()))
      .slice(0, SEARCH_HISTORY_LIMIT);
  } catch {
    return [];
  }
}

function writeSearchHistory(storageKey: string, history: string[]) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(history));
  } catch {
    // 搜索历史是辅助功能；存储不可用时不阻断结构搜索。
  }
}

function useSearchHistory(storageKey: string) {
  const [history, setHistory] = useState<string[]>(() => readSearchHistory(storageKey));

  const remember = useCallback((rawValue: string) => {
    const value = rawValue.trim();
    if (!value) return;
    setHistory((current) => {
      const next = [
        value,
        ...current.filter((item) => item.localeCompare(value, undefined, { sensitivity: "accent" }) !== 0),
      ].slice(0, SEARCH_HISTORY_LIMIT);
      writeSearchHistory(storageKey, next);
      return next;
    });
  }, [storageKey]);

  const remove = useCallback((value: string) => {
    setHistory((current) => {
      const next = current.filter((item) => item !== value);
      writeSearchHistory(storageKey, next);
      return next;
    });
  }, [storageKey]);

  return { history, remember, remove };
}

function SearchHistoryInput({
  disabled,
  history,
  id,
  onChange,
  onDelete,
  placeholder,
  value,
}: SearchHistoryInputProps) {
  const [isOpen, setIsOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const menuId = `${id}-history`;

  return (
    <div
      className="structure-search-history"
      ref={rootRef}
      onBlur={(event) => {
        if (!rootRef.current?.contains(event.relatedTarget as Node | null)) setIsOpen(false);
      }}
    >
      <input
        aria-controls={isOpen && history.length ? menuId : undefined}
        aria-expanded={isOpen && history.length > 0}
        aria-haspopup="dialog"
        autoComplete="off"
        disabled={disabled}
        id={id}
        name={`dockstart-${id}`}
        placeholder={placeholder}
        spellCheck={false}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onFocus={() => setIsOpen(history.length > 0)}
        onKeyDown={(event) => {
          if (event.key === "Escape") setIsOpen(false);
        }}
      />
      {isOpen && history.length ? (
        <div className="structure-search-history-menu" id={menuId} role="dialog" aria-label="历史输入">
          <span className="structure-search-history-label">历史输入</span>
          <ul>
            {history.map((item) => (
              <li key={item}>
                <button
                  className="structure-search-history-value"
                  type="button"
                  title={item}
                  onClick={() => {
                    onChange(item);
                    setIsOpen(false);
                  }}
                >
                  {item}
                </button>
                <button
                  aria-label={`删除历史输入 ${item}`}
                  className="structure-search-history-delete"
                  type="button"
                  title="删除记录"
                  onClick={() => onDelete(item)}
                >
                  <X aria-hidden="true" size={15} weight="bold" />
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function parseProjectResponse(rawPayload: string): ProjectResponse {
  const parsed = JSON.parse(rawPayload) as Partial<ProjectResponse>;
  return {
    ok: Boolean(parsed.ok),
    project_dir: parsed.project_dir,
    project: parsed.project ?? null,
    files: parsed.files ?? [],
    receptor: parsed.receptor,
    ligand: parsed.ligand,
    raw_file: parsed.raw_file,
    source: parsed.source,
    source_id: parsed.source_id,
    query_type: parsed.query_type,
    format: parsed.format,
    url: parsed.url,
    deleted_file: parsed.deleted_file,
    message: parsed.message,
    error: parsed.error,
  };
}

function parsePreparationResponse(rawPayload: string): PreparationStatusResponse {
  const parsed = JSON.parse(rawPayload) as Partial<PreparationStatusResponse>;
  return {
    ok: Boolean(parsed.ok),
    project_dir: parsed.project_dir ?? "",
    project: parsed.project ?? null,
    preparation: parsed.preparation ?? null,
    tools: parsed.tools,
    files: parsed.files,
    target: parsed.target,
    ready: parsed.ready,
    missing_tools: parsed.missing_tools ?? [],
    message: parsed.message,
    error: parsed.error,
  };
}

function parseSearchResponse(rawPayload: string): StructureSearchResponse {
  const parsed = JSON.parse(rawPayload) as Partial<StructureSearchResponse>;
  return {
    ok: Boolean(parsed.ok),
    provider: parsed.provider ?? "",
    query: parsed.query ?? "",
    query_type: parsed.query_type ?? "",
    requested_limit: Number(parsed.requested_limit ?? 0),
    total_count: Number(parsed.total_count ?? 0),
    returned_count: Number(parsed.returned_count ?? 0),
    truncated: Boolean(parsed.truncated),
    selection_required: parsed.selection_required !== false,
    candidates: Array.isArray(parsed.candidates) ? parsed.candidates : [],
    message: parsed.message ?? "",
    error: parsed.error,
  };
}

function parseCandidatePreviewResponse(rawPayload: string): CandidateStructurePreviewResponse {
  const parsed = JSON.parse(rawPayload) as Partial<CandidateStructurePreviewResponse>;
  return {
    ok: Boolean(parsed.ok),
    provider: parsed.provider ?? "",
    source_id: parsed.source_id ?? "",
    format: parsed.format ?? "",
    content: parsed.content ?? "",
    size_bytes: Number(parsed.size_bytes ?? 0),
    message: parsed.message ?? "",
    warnings: Array.isArray(parsed.warnings) ? parsed.warnings : [],
    error: parsed.error,
  };
}

function statusLabel(status: RunFileStatus["status"] | undefined): string {
  if (status === "ok") return "已完成";
  if (status === "empty") return "需检查";
  if (status === "error") return "失败";
  return "缺失";
}

function statusTone(status: RunFileStatus["status"] | undefined): "ok" | "warning" | "error" | "muted" {
  if (status === "ok") return "ok";
  if (status === "error") return "error";
  if (status === "empty") return "warning";
  return "muted";
}

function findStatus(files: RunFileStatus[], key: string): RawStructureStatus | null {
  return (files.find((file) => file.key === key) as RawStructureStatus | undefined) ?? null;
}

function formatBytes(value: number | undefined): string {
  const bytes = Number(value ?? 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function valueOrEmpty(value: string | undefined): string {
  return value && value.trim() ? value : "未记录";
}

function candidateMetadata(candidate: StructureSearchCandidate): string[] {
  const metadata = candidate.metadata ?? {};
  const details: string[] = [];
  if (candidate.provider === "rcsb") {
    const resolution = Number(metadata.resolution_angstrom);
    if (Number.isFinite(resolution) && resolution > 0) details.push(`分辨率 ${resolution.toFixed(2)} Å`);
    const polymerCount = Number(metadata.polymer_entity_count);
    if (Number.isFinite(polymerCount) && polymerCount > 0) details.push(`${polymerCount} 个聚合物实体`);
    const releaseDate = String(metadata.initial_release_date ?? "").slice(0, 10);
    if (releaseDate) details.push(`发布于 ${releaseDate}`);
  } else {
    const formula = String(metadata.molecular_formula ?? "").trim();
    const weight = String(metadata.molecular_weight ?? "").trim();
    if (formula) details.push(`分子式 ${formula}`);
    if (weight) details.push(`分子量 ${weight}`);
    if (metadata.metadata_status === "resolves_on_selection") details.push("选中后由 PubChem 解析标准记录");
  }
  return details;
}

function parseSdfProperties(content: string): Record<string, string> {
  const properties: Record<string, string> = {};
  const lines = content.split(/\r?\n/);
  for (let index = 0; index < lines.length; index += 1) {
    const match = lines[index].match(/^>\s*<([^>]+)>/);
    if (!match) continue;
    const values: string[] = [];
    for (index += 1; index < lines.length && lines[index].trim(); index += 1) {
      values.push(lines[index].trim());
    }
    properties[match[1]] = values.join(" ");
  }
  return properties;
}

function detailValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "未提供";
  return String(value);
}

function signedCharge(value: unknown): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return detailValue(value);
  return numeric > 0 ? `+${numeric}` : String(numeric);
}

function candidateDetailRows(
  candidate: StructureSearchCandidate,
  preview?: CandidateStructurePreviewResponse,
): CandidateDetailRow[] {
  const metadata = candidate.metadata ?? {};
  if (candidate.provider === "rcsb") {
    const resolution = Number(metadata.resolution_angstrom);
    const score = Number(metadata.search_score);
    return [
      { label: "PDB ID", value: candidate.source_id },
      { label: "结构标题", value: candidate.title || candidate.source_id, wide: true },
      { label: "实验方法", value: detailValue(metadata.experimental_method) },
      {
        label: "分辨率",
        value: Number.isFinite(resolution) && resolution > 0 ? `${resolution.toFixed(2)} Å` : "未提供",
      },
      { label: "聚合物实体", value: detailValue(metadata.polymer_entity_count) },
      { label: "非聚合物实体", value: detailValue(metadata.nonpolymer_entity_count) },
      { label: "沉积原子数", value: detailValue(metadata.deposited_atom_count) },
      { label: "首次发布日期", value: detailValue(String(metadata.initial_release_date ?? "").slice(0, 10)) },
      { label: "关键词", value: detailValue(metadata.keywords), wide: true },
      { label: "搜索相关度", value: Number.isFinite(score) ? score.toFixed(3) : "未提供" },
      { label: "下载格式", value: candidate.selection.format.toUpperCase() },
    ];
  }

  const properties = preview?.format === "sdf" ? parseSdfProperties(preview.content) : {};
  const componentCount = Number(properties.PUBCHEM_COMPONENT_COUNT);
  const recordType = Number.isFinite(componentCount)
    ? componentCount > 1
      ? "多组分记录（可能为盐或复合物）"
      : "单组分化合物"
    : detailValue(metadata.record_type);
  return [
    { label: "名称", value: candidate.title || candidate.source_id, wide: true },
    {
      label: "PubChem CID",
      value: detailValue(properties.PUBCHEM_COMPOUND_CID || (candidate.selection.query_type === "cid" ? candidate.source_id : "")),
    },
    {
      label: "分子式",
      value: detailValue(properties.PUBCHEM_MOLECULAR_FORMULA || metadata.molecular_formula),
    },
    {
      label: "分子量",
      value: detailValue(properties.PUBCHEM_MOLECULAR_WEIGHT || metadata.molecular_weight),
    },
    { label: "记录总电荷", value: signedCharge(properties.PUBCHEM_TOTAL_CHARGE) },
    { label: "结构类型", value: recordType },
    {
      label: "组分数",
      value: Number.isFinite(componentCount) ? String(componentCount) : "未提供",
    },
    { label: "重原子数", value: detailValue(properties.PUBCHEM_HEAVY_ATOM_COUNT) },
    { label: "可旋转键", value: detailValue(properties.PUBCHEM_CACTVS_ROTATABLE_BOND) },
    {
      label: "氢键供体 / 受体",
      value: `${detailValue(properties.PUBCHEM_CACTVS_HBOND_DONOR)} / ${detailValue(properties.PUBCHEM_CACTVS_HBOND_ACCEPTOR)}`,
    },
    { label: "XLogP", value: detailValue(properties.PUBCHEM_XLOGP3_AA) },
    { label: "TPSA", value: properties.PUBCHEM_CACTVS_TPSA ? `${properties.PUBCHEM_CACTVS_TPSA} Å²` : "未提供" },
    { label: "IUPAC 名称", value: detailValue(properties.PUBCHEM_IUPAC_NAME), wide: true },
    {
      label: "InChIKey",
      value: detailValue(properties.PUBCHEM_IUPAC_INCHIKEY || metadata.inchi_key),
      wide: true,
    },
    {
      label: "SMILES",
      value: detailValue(properties.PUBCHEM_SMILES || metadata.isomeric_smiles),
      wide: true,
    },
  ];
}

function errorDetails(error: { message?: string; suggestion?: string; raw_error?: string } | null | undefined): string {
  return [error?.message, error?.suggestion, error?.raw_error].filter(Boolean).join("\n");
}

function validatedLimit(value: string): number | null {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 1 && parsed <= MAX_SEARCH_LIMIT ? parsed : null;
}

function candidatePreviewKey(
  target: PreparationTarget,
  candidate: StructureSearchCandidate,
  receptorFormat: string,
): string {
  return `${target}:${candidate.candidate_id}:${target === "receptor" ? receptorFormat : "sdf"}:${JSON.stringify(candidate.selection)}`;
}

function normalizedCandidateSourceId(value: string, queryType: string): string {
  const trimmed = value.trim();
  if (queryType === "pdb_id") return trimmed.toUpperCase();
  if (queryType === "cid" && /^\d+$/.test(trimmed)) return trimmed.replace(/^0+(?=\d)/, "");
  return trimmed;
}

function candidateAcquisitionMismatches(
  response: ProjectResponse,
  status: RawStructureStatus | null,
  expected: { source: string; sourceId: string; queryType: string; format: string },
): string[] {
  const mismatches: string[] = [];
  const responseSourceId = normalizedCandidateSourceId(response.source_id ?? "", expected.queryType);
  const statusSourceId = normalizedCandidateSourceId(status?.source_id ?? "", expected.queryType);
  const expectedSourceId = normalizedCandidateSourceId(expected.sourceId, expected.queryType);
  const responseFormat = (response.format ?? "").trim().toLowerCase();
  const projectFileRef = expected.source === "rcsb_pdb" ? response.project?.receptor : response.project?.ligand;
  const responseQueryType = response.query_type ?? projectFileRef?.query_type ?? "";
  const rawFile = response.raw_file?.trim() ?? "";

  if (response.source !== expected.source) mismatches.push(`下载来源应为 ${expected.source}，实际为 ${response.source || "未返回"}`);
  if (responseSourceId !== expectedSourceId) mismatches.push(`候选 ID 应为 ${expectedSourceId}，实际为 ${responseSourceId || "未返回"}`);
  if (responseQueryType !== expected.queryType) mismatches.push(`查询类型应为 ${expected.queryType}，实际为 ${responseQueryType || "未返回"}`);
  if (responseFormat !== expected.format) mismatches.push(`文件格式应为 ${expected.format}，实际为 ${responseFormat || "未返回"}`);
  if (!rawFile || !rawFile.toLowerCase().endsWith(`.${expected.format}`)) {
    mismatches.push(`raw 文件记录缺失，或扩展名不是 .${expected.format}`);
  }
  if (!status || status.status !== "ok" || !status.exists || !status.record_consistent) {
    mismatches.push("项目中的 raw 文件状态未通过存在性与记录一致性检查");
  } else {
    if (status.source !== expected.source) mismatches.push(`raw 状态来源应为 ${expected.source}，实际为 ${status.source || "未记录"}`);
    if (statusSourceId !== expectedSourceId) mismatches.push(`raw 状态候选 ID 应为 ${expectedSourceId}，实际为 ${statusSourceId || "未记录"}`);
    if (status.query_type !== expected.queryType) mismatches.push(`raw 状态查询类型应为 ${expected.queryType}，实际为 ${status.query_type || "未记录"}`);
    if (!status.raw_file || status.raw_file !== rawFile) mismatches.push("下载响应与项目 raw 状态指向的文件不一致");
  }
  return mismatches;
}

export default function StructureFetchPage({
  project: initialProject,
  onBack,
  onOpenImportPdbqt,
  onOpenPreparation,
  onProjectChange,
}: StructureFetchPageProps) {
  const [project, setProject] = useState<DockStartProject>(initialProject);
  const [files, setFiles] = useState<RunFileStatus[]>([]);
  const [receptorRaw, setReceptorRaw] = useState<RawStructureStatus | null>(null);
  const [ligandRaw, setLigandRaw] = useState<RawStructureStatus | null>(null);
  const [rcsbQuery, setRcsbQuery] = useState("");
  const [rcsbQueryType, setRcsbQueryType] = useState<RcsbQueryType>("auto");
  const [rcsbLimit, setRcsbLimit] = useState(String(DEFAULT_SEARCH_LIMIT));
  const [pdbFormat, setPdbFormat] = useState("pdb");
  const [rcsbResults, setRcsbResults] = useState<StructureSearchResponse | null>(null);
  const [receptorPreview, setReceptorPreview] = useState<CandidatePreviewState | null>(null);
  const [overwritePdb, setOverwritePdb] = useState(false);
  const [deleteReceptorRawFile, setDeleteReceptorRawFile] = useState(false);
  const [pubchemQueryType, setPubchemQueryType] = useState<PubchemQueryType>("auto");
  const [pubchemQuery, setPubchemQuery] = useState("");
  const [pubchemLimit, setPubchemLimit] = useState(String(DEFAULT_SEARCH_LIMIT));
  const [pubchemResults, setPubchemResults] = useState<StructureSearchResponse | null>(null);
  const [ligandPreview, setLigandPreview] = useState<CandidatePreviewState | null>(null);
  const [overwritePubchem, setOverwritePubchem] = useState(false);
  const [deleteLigandRawFile, setDeleteLigandRawFile] = useState(false);
  const rcsbSearchHistory = useSearchHistory(RCSB_SEARCH_HISTORY_KEY);
  const pubchemSearchHistory = useSearchHistory(PUBCHEM_SEARCH_HISTORY_KEY);
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [busyAction, setBusyAction] = useState<BusyAction>(null);
  const [previewingCandidateId, setPreviewingCandidateId] = useState("");
  const [expandedCandidateIds, setExpandedCandidateIds] = useState<Set<string>>(() => new Set());
  const [candidateDetailSources, setCandidateDetailSources] = useState<Record<string, CandidateStructurePreviewResponse>>({});
  const [candidateDetailLoadingIds, setCandidateDetailLoadingIds] = useState<Set<string>>(() => new Set());
  const [candidateDetailErrors, setCandidateDetailErrors] = useState<Record<string, string>>({});
  const [operationScope] = useState(() => new PageOperationScope());
  const rcsbSearchGenerationRef = useRef(0);
  const pubchemSearchGenerationRef = useRef(0);
  const searchRequestSequenceRef = useRef(0);
  const activeSearchRequestRef = useRef(0);
  const previewMountedRef = useRef(true);
  const previewGenerationRef = useRef<Record<PreparationTarget, number>>({
    receptor: 0,
    ligand: 0,
  });
  const previewCacheRef = useRef(new Map<string, CandidatePreviewState>());
  const previewPromiseRef = useRef(new Map<string, Promise<CandidatePreviewState>>());

  useLayoutEffect(() => {
    operationScope.activate();
    previewMountedRef.current = true;
    return () => {
      rcsbSearchGenerationRef.current += 1;
      pubchemSearchGenerationRef.current += 1;
      activeSearchRequestRef.current = 0;
      previewMountedRef.current = false;
      previewGenerationRef.current.receptor += 1;
      previewGenerationRef.current.ligand += 1;
      previewPromiseRef.current.clear();
      operationScope.dispose();
    };
  }, [operationScope]);

  useEffect(() => {
    setProject(initialProject);
  }, [initialProject]);

  const applyProjectResponse = useCallback(
    (
      response: ProjectResponse,
      fallbackMessage: string,
      announce = true,
      token?: PageOperationToken,
    ) => {
      if (token && !operationScope.isCurrent(token)) return false;
      if (response.ok) {
        if (response.project) {
          setProject(response.project);
          onProjectChange(response.project);
        }
        const nextFiles = response.files ?? [];
        setFiles(nextFiles);
        setReceptorRaw(response.receptor ?? findStatus(nextFiles, "receptor_raw"));
        setLigandRaw(response.ligand ?? findStatus(nextFiles, "ligand_raw"));
        if (announce) {
          setMessage(response.message ?? fallbackMessage);
          setRawError("");
        }
        return true;
      }
      if (announce) {
        setMessage(response.error?.message ?? "原始结构操作失败。");
        setRawError(errorDetails(response.error));
      }
      return false;
    },
    [onProjectChange, operationScope],
  );

  const readRawStatus = useCallback(async () => {
    const rawPayload = await invoke<string>("get_raw_files_status", {
      projectDir: project.project_dir,
    });
    return parseProjectResponse(rawPayload);
  }, [project.project_dir]);

  const refreshRawStatus = useCallback(async (token: PageOperationToken, announce = false) => {
    const rawPayload = await invoke<string>("get_raw_files_status", {
      projectDir: project.project_dir,
    });
    const response = operationScope.parseIfCurrent(token, rawPayload, parseProjectResponse);
    if (!response) return null;
    applyProjectResponse(response, "原始结构状态已刷新。", announce, token);
    return response;
  }, [applyProjectResponse, operationScope, project.project_dir]);

  const reloadStatus = useCallback(async () => {
    const token = operationScope.begin();
    setIsBusy(true);
    setBusyAction("refresh");
    try {
      await refreshRawStatus(token, true);
    } catch (error) {
      operationScope.commit(token, () => {
        setMessage("无法读取原始结构状态。");
        setRawError(error instanceof Error ? error.message : String(error));
      });
    } finally {
      if (operationScope.finish(token)) {
        setBusyAction(null);
        setIsBusy(false);
      }
    }
  }, [operationScope, refreshRawStatus]);

  useEffect(() => {
    void reloadStatus();
  }, [reloadStatus]);

  const waitForTask = useCallback(
    // Do not pass token.signal: navigation stops UI observation, but an
    // explicitly started raw -> PDBQT workflow must keep supervising its
    // native tasks until the preparation stage has been started and finished.
    async (started: BackgroundTaskStatus, token: PageOperationToken) => waitForBackgroundTask(
      started.task_id,
      (task) => {
        operationScope.commit(token, () => {
          setMessage(task.progress.message || task.message);
          if (task.error) setRawError(task.error);
        });
      },
    ),
    [operationScope],
  );

  const prepareRaw = useCallback(async (
    target: PreparationTarget,
    token: PageOperationToken,
    overwritePrepared: boolean,
  ) => {
    const label = target === "receptor" ? "受体" : "配体";
    operationScope.commit(token, () => {
      setBusyAction(target === "receptor" ? "prepare-receptor" : "prepare-ligand");
      setMessage(`${label}原始结构已保存，正在自动转换为 PDBQT…`);
      setRawError("");
    });

    const started = await startPreparationTask(
      project.project_dir,
      target,
      overwritePrepared,
    );
    if (started.deduplicated) {
      operationScope.commit(token, () => {
        setMessage(`${label}已有一个结构转换任务正在运行。为避免把旧输入误认为本次输入，本次不会复用该任务；请等待任务结束后刷新状态并重试。`);
        setRawError(`检测到既有后台任务：${started.task_id || "任务 ID 未返回"}。本次未继续自动转换。`);
      });
      return false;
    }
    const completed = await waitForTask(started, token);
    if (completed.status === "cancelled") {
      operationScope.commit(token, () => {
        setMessage(`${label}自动转换任务已取消；原始结构仍保留在项目 raw/ 目录。`);
      });
      return false;
    }
    if (!completed.result_json) {
      throw new Error(completed.error || completed.message || `${label}自动转换任务没有返回结果。`);
    }

    const response = operationScope.parseIfCurrent(token, completed.result_json, parsePreparationResponse);
    if (response?.project) {
      setProject(response.project);
      onProjectChange(response.project);
    }
    if (completed.status !== "finished" || response?.ok === false) {
      operationScope.commit(token, () => {
        setMessage(
          `${label}原始结构已保留，但自动转换为 PDBQT 失败。请打开“格式转换与 PDBQT 准备”查看日志、检查工具链后重试。`,
        );
        setRawError(errorDetails(response?.error) || completed.error || response?.message || "自动转换失败。");
      });
      return false;
    }

    operationScope.commit(token, () => {
      setMessage(`${label}已下载或导入，并自动转换为 PDBQT。请继续人工检查结构、质子化和电荷是否合理。`);
      setRawError("");
    });
    return true;
  }, [onProjectChange, operationScope, project.project_dir, waitForTask]);

  const searchCandidates = async (provider: "rcsb" | "pubchem") => {
    const isRcsb = provider === "rcsb";
    const query = (isRcsb ? rcsbQuery : pubchemQuery).trim();
    const limit = validatedLimit(isRcsb ? rcsbLimit : pubchemLimit);
    if (!query) return;
    if (limit === null) {
      setMessage(`候选数量必须是 1 到 ${MAX_SEARCH_LIMIT} 之间的整数。`);
      setRawError("");
      return;
    }
    if (isRcsb) rcsbSearchHistory.remember(query);
    else pubchemSearchHistory.remember(query);

    const generationRef = isRcsb ? rcsbSearchGenerationRef : pubchemSearchGenerationRef;
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    const requestId = searchRequestSequenceRef.current + 1;
    searchRequestSequenceRef.current = requestId;
    activeSearchRequestRef.current = requestId;
    const token = operationScope.begin();

    setIsBusy(true);
    setBusyAction(isRcsb ? "search-receptor" : "search-ligand");
    if (isRcsb) {
      previewGenerationRef.current.receptor += 1;
      setReceptorPreview(null);
    } else {
      previewGenerationRef.current.ligand += 1;
      setLigandPreview(null);
    }
    setMessage(isRcsb ? "正在搜索 RCSB PDB 候选结构…" : "正在搜索 PubChem 候选化合物…");
    setRawError("");
    try {
      const rawPayload = await invoke<string>(
        isRcsb ? "search_rcsb_candidates" : "search_pubchem_candidates",
        {
          query,
          limit,
          queryType: isRcsb ? rcsbQueryType : pubchemQueryType,
        },
      );
      if (
        !operationScope.isCurrent(token)
        || generationRef.current !== generation
        || activeSearchRequestRef.current !== requestId
      ) return;
      const response = operationScope.parseIfCurrent(token, rawPayload, parseSearchResponse);
      if (!response) return;
      if (isRcsb) setRcsbResults(response);
      else setPubchemResults(response);
      if (!response.ok) {
        setMessage(response.error?.message ?? "候选搜索失败。");
        setRawError(errorDetails(response.error));
        return;
      }
      setMessage(response.message || `找到 ${response.returned_count} 个候选，请明确选择后再下载。`);
      const firstCandidate = response.candidates[0];
      if (firstCandidate) {
        const target: PreparationTarget = isRcsb ? "receptor" : "ligand";
        window.setTimeout(() => {
          if (previewMountedRef.current) {
            void previewCandidate(target, firstCandidate, response.candidates);
          }
        }, 0);
      }
    } catch (error) {
      if (
        !operationScope.isCurrent(token)
        || generationRef.current !== generation
        || activeSearchRequestRef.current !== requestId
      ) return;
      setMessage(isRcsb ? "无法搜索 RCSB PDB 候选结构。" : "无法搜索 PubChem 候选化合物。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      if (activeSearchRequestRef.current === requestId && operationScope.finish(token)) {
        activeSearchRequestRef.current = 0;
        setBusyAction(null);
        setIsBusy(false);
      }
    }
  };

  const requestCandidatePreview = useCallback((
    target: PreparationTarget,
    candidate: StructureSearchCandidate,
  ): Promise<CandidatePreviewState> => {
    const isReceptor = target === "receptor";
    const label = `${candidate.source_id} · ${candidate.title || candidate.source_id}`;
    const selection = isReceptor
      ? { ...candidate.selection, format: pdbFormat }
      : candidate.selection;
    const key = candidatePreviewKey(target, candidate, pdbFormat);
    const cached = previewCacheRef.current.get(key);
    if (cached) return Promise.resolve(cached);
    const pending = previewPromiseRef.current.get(key);
    if (pending) return pending;

    const request = invoke<string>("preview_structure_candidate", {
      selectionJson: JSON.stringify(selection),
    }).then((rawPayload) => {
      const response = parseCandidatePreviewResponse(rawPayload);
      if (!response.ok) {
        throw new Error(errorDetails(response.error) || response.message || "候选结构预览失败。");
      }
      const state = { candidateId: candidate.candidate_id, label, response };
      previewCacheRef.current.set(key, state);
      return state;
    }).finally(() => {
      previewPromiseRef.current.delete(key);
    });
    previewPromiseRef.current.set(key, request);
    return request;
  }, [pdbFormat]);

  const previewCandidate = useCallback(async (
    target: PreparationTarget,
    candidate: StructureSearchCandidate,
    candidates: StructureSearchCandidate[] = [],
  ) => {
    const isReceptor = target === "receptor";
    const key = candidatePreviewKey(target, candidate, pdbFormat);
    const generation = previewGenerationRef.current[target] + 1;
    previewGenerationRef.current[target] = generation;
    setPreviewingCandidateId(key);
    setMessage(`正在加载 ${candidate.source_id} 的临时 3D 预览…`);
    setRawError("");
    try {
      const state = await requestCandidatePreview(target, candidate);
      if (
        !previewMountedRef.current
        || previewGenerationRef.current[target] !== generation
      ) return;
      if (isReceptor) setReceptorPreview(state);
      else setLigandPreview(state);
      setMessage(state.response.message || `${candidate.source_id} 已加载到临时预览。`);

      const candidateIndex = candidates.findIndex((item) => item.candidate_id === candidate.candidate_id);
      const nextCandidate = candidateIndex >= 0 ? candidates[candidateIndex + 1] : undefined;
      if (nextCandidate) {
        void requestCandidatePreview(target, nextCandidate).catch(() => {
          // Adjacent-result warmup is best effort; explicit selection still
          // reports an actionable error if the candidate cannot be fetched.
        });
      }
    } catch (error) {
      if (
        !previewMountedRef.current
        || previewGenerationRef.current[target] !== generation
      ) return;
      setMessage("无法加载候选结构的临时 3D 预览。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      if (
        previewMountedRef.current
        && previewGenerationRef.current[target] === generation
      ) {
        setPreviewingCandidateId("");
      }
    }
  }, [pdbFormat, requestCandidatePreview]);

  const toggleCandidateDetails = useCallback(async (
    target: PreparationTarget,
    candidate: StructureSearchCandidate,
  ) => {
    const candidateId = candidate.candidate_id;
    if (expandedCandidateIds.has(candidateId)) {
      setExpandedCandidateIds((current) => {
        const next = new Set(current);
        next.delete(candidateId);
        return next;
      });
      return;
    }
    setExpandedCandidateIds((current) => new Set(current).add(candidateId));
    if (
      candidate.provider !== "pubchem"
      || candidateDetailSources[candidateId]
      || candidateDetailLoadingIds.has(candidateId)
    ) return;

    setCandidateDetailLoadingIds((current) => new Set(current).add(candidateId));
    setCandidateDetailErrors((current) => ({ ...current, [candidateId]: "" }));
    try {
      const state = await requestCandidatePreview(target, candidate);
      if (!previewMountedRef.current) return;
      setCandidateDetailSources((current) => ({
        ...current,
        [candidateId]: state.response,
      }));
    } catch (error) {
      if (!previewMountedRef.current) return;
      setCandidateDetailErrors((current) => ({
        ...current,
        [candidateId]: error instanceof Error ? error.message : String(error),
      }));
    } finally {
      if (previewMountedRef.current) {
        setCandidateDetailLoadingIds((current) => {
          const next = new Set(current);
          next.delete(candidateId);
          return next;
        });
      }
    }
  }, [candidateDetailLoadingIds, candidateDetailSources, expandedCandidateIds, requestCandidatePreview]);

  const selectAndPrepareCandidate = async (target: PreparationTarget, candidate: StructureSearchCandidate) => {
    const isReceptor = target === "receptor";
    const label = isReceptor ? "受体" : "配体";
    const selection = candidate.selection;
    const ligandQueryType = selection.query_type === "cid" ? "cid" : "name";
    const expectedQueryType = isReceptor ? "pdb_id" : ligandQueryType;
    const expectedSourceId = isReceptor
      ? selection.pdb_id || candidate.source_id
      : selection.query || candidate.source_id;
    const expectedFormat = isReceptor ? pdbFormat : "sdf";
    const expectedSource = isReceptor ? "rcsb_pdb" : "pubchem";
    const token = operationScope.begin();
    setIsBusy(true);
    setBusyAction(isReceptor ? "prepare-receptor" : "prepare-ligand");
    setMessage(`正在下载已选择的${label}候选：${candidate.source_id}…`);
    setRawError("");
    try {
      await runRawToPreparedWorkflow({
        acquireRaw: async () => {
          const started = isReceptor
            ? await startPdbFetchTask(
              project.project_dir,
              expectedSourceId,
              expectedFormat,
              overwritePdb,
            )
            : await startPubchemFetchTask(
              project.project_dir,
              expectedSourceId,
              ligandQueryType,
              expectedFormat,
              overwritePubchem,
            );
          if (started.deduplicated) {
            operationScope.commit(token, () => {
              setMessage(`${label}已有一个结构获取任务正在运行。为避免把旧候选误认为 ${candidate.source_id}，本次不会复用该任务；请等待任务结束后刷新并重试。`);
              setRawError(`检测到既有后台任务：${started.task_id || "任务 ID 未返回"}。本次未下载或自动转换所选候选。`);
            });
            return false;
          }
          const completed = await waitForTask(started, token);
          if (completed.status === "cancelled") {
            operationScope.commit(token, () => {
              setMessage(`${label}结构获取任务已取消。`);
            });
            return false;
          }
          if (!completed.result_json) {
            throw new Error(completed.error || completed.message || `${label}结构获取任务没有返回结果。`);
          }

          // Structure-fetch responses contain project metadata only, never the
          // molecular payload. Parsing them is required to verify that the raw
          // file belongs to the candidate the user explicitly selected.
          const response = parseProjectResponse(completed.result_json);
          if (!response.ok) {
            applyProjectResponse(response, `${label}原始结构下载失败。`, true, token);
            return false;
          }
          applyProjectResponse(response, `${label}原始结构已下载。`, true, token);

          const refreshed = await readRawStatus();
          if (!refreshed.ok) {
            throw new Error(errorDetails(refreshed.error) || `${label} raw 状态读取失败，已停止自动转换。`);
          }
          applyProjectResponse(refreshed, "原始结构状态已刷新。", false, token);
          const status = isReceptor
            ? refreshed.receptor ?? findStatus(refreshed.files ?? [], "receptor_raw")
            : refreshed.ligand ?? findStatus(refreshed.files ?? [], "ligand_raw");
          const mismatches = candidateAcquisitionMismatches(response, status, {
            source: expectedSource,
            sourceId: expectedSourceId,
            queryType: expectedQueryType,
            format: expectedFormat,
          });
          if (mismatches.length) {
            operationScope.commit(token, () => {
              setMessage(`${label}候选下载结果与本次选择不一致，已停止自动转换。raw 文件会保留，等待人工检查。`);
              setRawError(mismatches.map((item) => `- ${item}`).join("\n"));
            });
            return false;
          }
          return true;
        },
        observerIsCurrent: () => operationScope.isCurrent(token),
        prepareRaw: (overwritePrepared) => prepareRaw(target, token, overwritePrepared),
      });
      if (operationScope.isCurrent(token)) {
        await refreshRawStatus(token, false);
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      operationScope.commit(token, () => {
        setMessage(`无法完成${label}候选的下载与自动转换。若 raw 文件已经写入，它会被保留；可打开“格式转换与 PDBQT 准备”检查并重试。`);
        setRawError(error instanceof Error ? error.message : String(error));
      });
    } finally {
      if (operationScope.finish(token)) {
        setBusyAction(null);
        setIsBusy(false);
      }
    }
  };

  const importLocalRaw = async (target: PreparationTarget) => {
    const isReceptor = target === "receptor";
    const label = isReceptor ? "受体" : "配体";
    const token = operationScope.begin();
    setMessage("");
    setRawError("");
    try {
      const selected = await open({
        directory: false,
        multiple: !isReceptor,
        title: isReceptor ? "选择受体 PDB / CIF" : "选择一个或多个配体 SDF / MOL",
        filters: [isReceptor
          ? { name: "受体原始结构", extensions: ["pdb", "cif"] }
          : { name: "配体原始结构", extensions: ["sdf", "mol"] }],
      });
      const selectedPaths = Array.isArray(selected) ? selected : selected ? [selected] : [];
      const sourcePath = selectedPaths[0] ?? "";
      if (!sourcePath) return;

      operationScope.commit(token, () => {
        setIsBusy(true);
        setBusyAction(isReceptor ? "prepare-receptor" : "prepare-ligand");
        setMessage(`正在导入${label}原始结构…`);
      });
      if (!isReceptor && selectedPaths.length > 1) {
        const staged = JSON.parse(await invoke<string>("stage_screening_inputs", {
          projectDir: project.project_dir,
          files: selectedPaths,
        })) as { ok?: boolean; staged?: Array<{ file?: string }>; error?: { message?: string; raw_error?: string } };
        if (!staged.ok || !staged.staged?.length) {
          throw new Error(staged.error?.raw_error || staged.error?.message || "多个配体自动准备失败。");
        }
        const firstSnapshot = `${project.project_dir}\\${String(staged.staged[0].file || "").replace(/\//g, "\\")}`;
        const imported = parseProjectResponse(await invoke<string>("import_ligand_pdbqt", {
          projectDir: project.project_dir,
          sourcePath: firstSnapshot,
        }));
        if (!imported.ok) throw new Error(imported.error?.raw_error || imported.error?.message || "无法载入首个配体预览。");
        applyProjectResponse(imported, "多个配体已准备。", true, token);
        writeDockingWorkspaceMode(project.project_dir, "batch");
        operationScope.commit(token, () => setMessage(`已准备 ${staged.staged?.length} 个配体并自动进入多配体模式；首个配体用于搜索范围预览。`));
        await refreshRawStatus(token, false);
        return;
      }
      await runRawToPreparedWorkflow({
        acquireRaw: async () => {
          const rawPayload = await invoke<string>(
            isReceptor ? "import_receptor_raw_file" : "import_ligand_raw_file",
            { projectDir: project.project_dir, sourcePath },
          );
          const response = parseProjectResponse(rawPayload);
          if (!response.ok) {
            applyProjectResponse(response, `无法导入${label}原始结构。`, true, token);
            return false;
          }
          applyProjectResponse(response, `${label}原始结构已导入。`, true, token);
          return true;
        },
        observerIsCurrent: () => operationScope.isCurrent(token),
        prepareRaw: (overwritePrepared) => prepareRaw(target, token, overwritePrepared),
      });
      if (operationScope.isCurrent(token)) {
        if (!isReceptor) writeDockingWorkspaceMode(project.project_dir, "single");
        await refreshRawStatus(token, false);
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      operationScope.commit(token, () => {
        setMessage(`无法完成${label}原始结构的导入与自动转换。若 raw 文件已经写入，它会被保留。`);
        setRawError(error instanceof Error ? error.message : String(error));
      });
    } finally {
      if (operationScope.finish(token)) {
        setBusyAction(null);
        setIsBusy(false);
      }
    }
  };

  const clearRawRecord = async (role: PreparationTarget) => {
    const label = role === "receptor" ? "受体" : "配体";
    const deleteFile = role === "receptor" ? deleteReceptorRawFile : deleteLigandRawFile;
    if (!window.confirm(`确定清除${label} raw 记录吗？Vina 输入文件不会被删除。`)) return;

    const token = operationScope.begin();
    setIsBusy(true);
    setMessage("");
    setRawError("");
    try {
      const command = role === "receptor" ? "clear_receptor_raw_record" : "clear_ligand_raw_record";
      const rawPayload = await invoke<string>(command, {
        projectDir: project.project_dir,
        deleteFile,
      });
      const response = operationScope.parseIfCurrent(token, rawPayload, parseProjectResponse);
      if (!response) return;
      applyProjectResponse(response, `${label} raw 记录已清除。`, true, token);
    } catch (error) {
      operationScope.commit(token, () => {
        setMessage(`无法清除${label} raw 记录。`);
        setRawError(error instanceof Error ? error.message : String(error));
      });
    } finally {
      if (operationScope.finish(token)) setIsBusy(false);
    }
  };

  const receptorStatus = receptorRaw ?? findStatus(files, "receptor_raw");
  const ligandStatus = ligandRaw ?? findStatus(files, "ligand_raw");
  const receptorBusy = isBusy && (busyAction === "refresh" || Boolean(busyAction?.endsWith("-receptor")));
  const ligandBusy = isBusy && (busyAction === "refresh" || Boolean(busyAction?.endsWith("-ligand")));

  const renderTechnicalDetails = (status: RawStructureStatus | null, fallbackRawFile: string) => (
    <dl className="meta-list">
      <div>
        <dt>来源</dt>
        <dd>{valueOrEmpty(status?.source)}</dd>
      </div>
      <div>
        <dt>查询</dt>
        <dd>{valueOrEmpty(status?.query_type)} · {valueOrEmpty(status?.source_id)}</dd>
      </div>
      <div>
        <dt>raw_file</dt>
        <dd><code>{status?.raw_file || fallbackRawFile || "未记录"}</code></dd>
      </div>
      <div>
        <dt>大小 / 修改时间</dt>
        <dd>{formatBytes(status?.size_bytes ?? status?.size)} · {valueOrEmpty(status?.modified_at)}</dd>
      </div>
      <div>
        <dt>绝对路径</dt>
        <dd><code>{valueOrEmpty(status?.absolute_path)}</code></dd>
      </div>
      <div>
        <dt>记录一致性</dt>
        <dd>{status?.record_consistent ? "一致" : "需检查"}</dd>
      </div>
    </dl>
  );

  const renderCandidates = (target: PreparationTarget, response: StructureSearchResponse | null) => {
    if (!response?.ok) return null;
    const label = target === "receptor" ? "受体" : "配体";
    const preview = target === "receptor" ? receptorPreview : ligandPreview;
    const statusId = `${target}-operation-status`;
    return (
      <section
        className={`structure-candidate-panel${preview ? " has-preview" : ""}`}
        aria-label={`${label}候选列表`}
      >
        <div className="structure-candidate-heading">
          <div>
            <strong>搜索结果</strong>
            <span>查询“{response.query}” · 显示 {response.returned_count} / {response.total_count} 个候选</span>
          </div>
          <StatusBadge tone={response.candidates.length ? "info" : "warning"}>
            {response.candidates.length ? "等待选择" : "无结果"}
          </StatusBadge>
        </div>
        <div className="structure-candidate-body">
          {response.candidates.length ? (
            <div className="structure-candidate-list">
              {response.candidates.map((candidate) => {
                const details = candidateMetadata(candidate);
                const previewKey = candidatePreviewKey(target, candidate, pdbFormat);
                const isSelected = preview?.candidateId === candidate.candidate_id;
                const isExpanded = expandedCandidateIds.has(candidate.candidate_id);
                const isLoadingDetails = candidateDetailLoadingIds.has(candidate.candidate_id);
                const detailRows = candidateDetailRows(candidate, candidateDetailSources[candidate.candidate_id]);
                const detailsId = `${target}-${candidate.candidate_id.replace(/[^A-Za-z0-9_-]/g, "-")}-details`;
                return (
                  <article
                    className={`structure-candidate-item${isSelected ? " is-selected" : ""}`}
                    key={candidate.candidate_id}
                  >
                    <div className="structure-candidate-copy">
                      <div className="structure-candidate-title">
                        <strong>{candidate.source_id}</strong>
                        <span>{candidate.title || candidate.source_id}</span>
                      </div>
                      {candidate.subtitle ? <p>{candidate.subtitle}</p> : null}
                      {details.length ? (
                        <ul className="structure-candidate-meta" aria-label="候选元数据">
                          {details.map((detail) => <li key={detail}>{detail}</li>)}
                        </ul>
                      ) : null}
                    </div>
                    <div className="structure-candidate-actions">
                      <ActionButton
                        className="structure-candidate-details-toggle"
                        aria-expanded={isExpanded}
                        aria-controls={detailsId}
                        disabled={isBusy || isLoadingDetails}
                        onClick={() => void toggleCandidateDetails(target, candidate)}
                      >
                        {isLoadingDetails ? "读取参数中…" : isExpanded ? "收起详细参数" : "展开详细参数"}
                        {isExpanded ? <CaretUp size={14} /> : <CaretDown size={14} />}
                      </ActionButton>
                      <ActionButton
                        aria-describedby={statusId}
                        disabled={isBusy}
                        aria-label={`临时预览 ${candidate.source_id}`}
                        onClick={() => void previewCandidate(target, candidate, response.candidates)}
                      >
                        {previewingCandidateId === previewKey ? "加载中…" : "3D 预览"}
                      </ActionButton>
                      <ActionButton
                        aria-describedby={statusId}
                        variant="primary"
                        disabled={isBusy}
                        aria-label={`选择 ${candidate.source_id} 并自动准备${label} PDBQT`}
                        onClick={() => void selectAndPrepareCandidate(target, candidate)}
                      >
                        选择并准备
                      </ActionButton>
                    </div>
                    {isExpanded ? (
                      <section className="structure-candidate-details" id={detailsId} aria-label={`${candidate.source_id} 详细参数`}>
                        <header>
                          <strong>详细参数</strong>
                          <span>{candidate.provider === "rcsb" ? "RCSB 结构记录" : "PubChem 化合物记录"}</span>
                        </header>
                        {isLoadingDetails ? <p className="structure-candidate-details-state">正在读取标准化合物记录…</p> : null}
                        {candidateDetailErrors[candidate.candidate_id] ? (
                          <p className="structure-candidate-details-state is-error">
                            详细参数读取失败；基础搜索信息仍可使用。{candidateDetailErrors[candidate.candidate_id]}
                          </p>
                        ) : null}
                        <dl>
                          {detailRows.map((row) => (
                            <div className={row.wide ? "is-wide" : ""} key={row.label}>
                              <dt>{row.label}</dt>
                              <dd>{row.value}</dd>
                            </div>
                          ))}
                        </dl>
                        {candidate.provider === "pubchem" ? (
                          <p className="structure-candidate-detail-note">
                            总电荷和组分信息来自 PubChem 记录；DockStart 不据此推断生理 pH 下的质子化状态。
                          </p>
                        ) : null}
                      </section>
                    ) : null}
                  </article>
                );
              })}
            </div>
          ) : (
            <p className="structure-candidate-empty">没有找到候选。请调整关键词、查询类型或候选数量后重试。</p>
          )}
          {preview ? (
            <section className="structure-candidate-preview-panel" aria-label={`${preview.label} 临时 3D 预览`}>
              <header>
                <div><span>只读 3D 预览</span><strong>{preview.label}</strong></div>
                <StatusBadge tone="info">未写入项目</StatusBadge>
              </header>
              <Suspense fallback={<div className="run-preview-loading">正在加载 3D 查看器…</div>}>
                <CandidateStructurePreview
                  content={preview.response.content}
                  format={preview.response.format}
                  label={preview.label}
                />
              </Suspense>
              <p>原始 PDB / mmCIF / SDF 可直接用于临时 3D 选择预览；只有点击“选择并准备”后才会写入项目并生成 PDBQT。</p>
            </section>
          ) : null}
        </div>
        <p className="structure-candidate-note">
          DockStart 不会默认下载第一项。可逐项加载只读 3D 预览；只有明确点击“选择并准备”的候选才会写入项目并转换。
        </p>
      </section>
    );
  };

  return (
    <PageShell labelledBy="structure-fetch-title">
      <PageHero
        eyebrow="结构来源 · SOURCE"
        title="搜索、选择并准备结构"
        titleId="structure-fetch-title"
        description="先查看 RCSB / PubChem 候选，再明确选择目标；下载或本地导入后会立即尝试转换为 Vina 使用的 PDBQT。"
        actions={
          <>
            <ActionButton variant="text" onClick={onBack}>返回</ActionButton>
            <ActionButton onClick={() => void reloadStatus()} disabled={isBusy}>
              {busyAction === "refresh" ? "正在刷新…" : "刷新状态"}
            </ActionButton>
          </>
        }
      />

      <BodyGrid className="structure-source-body-grid">
        <MainPanel>
          <div className="main-panel-content structure-source-content">
            <WarningCallout title="自动转换不等于科学检查">
              <p>下载或导入后会立即尝试生成 PDBQT，但仍需人工检查受体链、水、金属、辅因子，以及配体质子化、电荷和构象。</p>
            </WarningCallout>

            <div className="structure-source-grid">
              <article
                aria-busy={receptorBusy}
                aria-labelledby="receptor-source-title"
                className="task-card structure-source-card"
                data-layout="task-card"
                data-source-role="receptor"
              >
                <div className="structure-source-summary">
                  <div className="structure-source-summary-header">
                    <span className="structure-source-step">01 · RECEPTOR</span>
                    <h2 id="receptor-source-title">受体</h2>
                  </div>
                  <span aria-live="polite" className="viewer-sr-only" id="receptor-operation-status">
                    {receptorBusy ? message || "正在处理受体结构。" : ""}
                  </span>
                  <StatusBadge tone={statusTone(receptorStatus?.status)}>
                    {`raw ${statusLabel(receptorStatus?.status)}`}
                  </StatusBadge>
                  <div className="structure-source-status-item">
                    <span>当前 raw 文件</span>
                    <code title={receptorStatus?.raw_file || project.receptor.raw_file || "未记录 raw 文件"}>
                      {receptorStatus?.raw_file || project.receptor.raw_file || "未记录"}
                    </code>
                  </div>
                  <div className="structure-source-status-item">
                    <span>Vina 输入</span>
                    <strong>{project.receptor.file ? "PDBQT 已准备" : "PDBQT 未准备"}</strong>
                  </div>
                </div>

                <section className="structure-source-workspace" aria-labelledby="receptor-source-workspace-title">
                  <header className="structure-source-workspace-header">
                    <div>
                      <span>在线结构库</span>
                      <h3 id="receptor-source-workspace-title">搜索 RCSB 并预览受体</h3>
                    </div>
                    <p>可先查看候选原始结构；明确选择后才会写入项目并转换。</p>
                  </header>

                  <div className="structure-search-controls">
                    <div className="field-stack structure-search-query">
                      <label htmlFor="rcsb-query">RCSB PDB ID 或关键词</label>
                      <SearchHistoryInput
                        disabled={isBusy}
                        history={rcsbSearchHistory.history}
                        id="rcsb-query"
                        onDelete={rcsbSearchHistory.remove}
                        value={rcsbQuery}
                        onChange={(value) => {
                          rcsbSearchGenerationRef.current += 1;
                          previewGenerationRef.current.receptor += 1;
                          setRcsbQuery(value);
                          setRcsbResults(null);
                          setReceptorPreview(null);
                          setPreviewingCandidateId("");
                        }}
                        placeholder="例如 1IEP 或 c-Abl imatinib"
                      />
                    </div>
                    <div className="field-stack">
                      <label htmlFor="rcsb-query-type">搜索方式</label>
                      <select
                        disabled={isBusy}
                        id="rcsb-query-type"
                        value={rcsbQueryType}
                        onChange={(event) => {
                          rcsbSearchGenerationRef.current += 1;
                          previewGenerationRef.current.receptor += 1;
                          setRcsbQueryType(event.target.value as RcsbQueryType);
                          setRcsbResults(null);
                          setReceptorPreview(null);
                          setPreviewingCandidateId("");
                        }}
                      >
                        <option value="auto">自动识别</option>
                        <option value="pdb_id">PDB ID</option>
                        <option value="keyword">关键词</option>
                      </select>
                    </div>
                    <div className="field-stack">
                      <label htmlFor="rcsb-limit">候选数量</label>
                      <input
                        disabled={isBusy}
                        id="rcsb-limit"
                        type="number"
                        min="1"
                        max={MAX_SEARCH_LIMIT}
                        step="1"
                        value={rcsbLimit}
                        onChange={(event) => {
                          rcsbSearchGenerationRef.current += 1;
                          previewGenerationRef.current.receptor += 1;
                          setRcsbLimit(event.target.value);
                          setRcsbResults(null);
                          setReceptorPreview(null);
                          setPreviewingCandidateId("");
                        }}
                      />
                    </div>
                    <div className="field-stack">
                      <label htmlFor="pdb-format">下载格式</label>
                      <select disabled={isBusy} id="pdb-format" value={pdbFormat} onChange={(event) => {
                        previewGenerationRef.current.receptor += 1;
                        setPdbFormat(event.target.value);
                        setReceptorPreview(null);
                        setPreviewingCandidateId("");
                      }}>
                        <option value="pdb">PDB</option>
                        <option value="cif">mmCIF</option>
                      </select>
                    </div>
                    <div className="structure-search-submit">
                      <ActionButton
                      aria-describedby="receptor-operation-status"
                      variant="primary"
                      disabled={isBusy || !rcsbQuery.trim()}
                      onClick={() => void searchCandidates("rcsb")}
                      >
                        {busyAction === "search-receptor" ? "正在搜索…" : "搜索受体候选"}
                      </ActionButton>
                    </div>
                  </div>

                  {renderCandidates("receptor", rcsbResults)}
                </section>

                <div className="structure-source-actions">
                  <div className="structure-source-actions-copy">
                    <span>本地文件</span>
                    <strong>导入 PDB / CIF</strong>
                    <p>选择本机原始结构后，立即尝试生成受体 PDBQT。</p>
                  </div>
                  <ActionButton
                    aria-describedby="receptor-operation-status"
                    className="structure-source-import-button"
                    disabled={isBusy}
                    onClick={() => void importLocalRaw("receptor")}
                  >
                    {busyAction === "prepare-receptor" ? "正在处理…" : "导入并自动转换"}
                  </ActionButton>
                  <label className="checkbox-row structure-source-overwrite">
                    <input
                      type="checkbox"
                      checked={overwritePdb}
                      disabled={isBusy}
                      onChange={(event) => setOverwritePdb(event.target.checked)}
                    />
                    在线候选允许覆盖同名 raw 文件
                  </label>
                  <AdvancedDetails className="structure-source-manage" summary="管理受体原始结构">
                    <label className="checkbox-row">
                      <input
                        type="checkbox"
                        checked={deleteReceptorRawFile}
                        disabled={isBusy}
                        onChange={(event) => setDeleteReceptorRawFile(event.target.checked)}
                      />
                      清除记录时同时删除项目中的 raw 文件
                    </label>
                    <ActionButton variant="text" disabled={isBusy || !(receptorStatus?.raw_file || project.receptor.raw_file)} onClick={() => void clearRawRecord("receptor")}>
                      清除受体记录
                    </ActionButton>
                    {renderTechnicalDetails(receptorStatus, project.receptor.raw_file)}
                  </AdvancedDetails>
                </div>
              </article>

              <article
                aria-busy={ligandBusy}
                aria-labelledby="ligand-source-title"
                className="task-card structure-source-card"
                data-layout="task-card"
                data-source-role="ligand"
              >
                <div className="structure-source-summary">
                  <div className="structure-source-summary-header">
                    <span className="structure-source-step">02 · LIGAND</span>
                    <h2 id="ligand-source-title">配体</h2>
                  </div>
                  <span aria-live="polite" className="viewer-sr-only" id="ligand-operation-status">
                    {ligandBusy ? message || "正在处理配体结构。" : ""}
                  </span>
                  <StatusBadge tone={statusTone(ligandStatus?.status)}>
                    {`raw ${statusLabel(ligandStatus?.status)}`}
                  </StatusBadge>
                  <div className="structure-source-status-item">
                    <span>当前 raw 文件</span>
                    <code title={ligandStatus?.raw_file || project.ligand.raw_file || "未记录 raw 文件"}>
                      {ligandStatus?.raw_file || project.ligand.raw_file || "未记录"}
                    </code>
                  </div>
                  <div className="structure-source-status-item">
                    <span>Vina 输入</span>
                    <strong>{project.ligand.file ? "PDBQT 已准备" : "PDBQT 未准备"}</strong>
                  </div>
                </div>

                <section className="structure-source-workspace" aria-labelledby="ligand-source-workspace-title">
                  <header className="structure-source-workspace-header">
                    <div>
                      <span>在线化合物库</span>
                      <h3 id="ligand-source-workspace-title">搜索 PubChem 并预览配体</h3>
                    </div>
                    <p>名称搜索会返回多个候选，可先预览再选择目标化合物。</p>
                  </header>

                  <div className="structure-search-controls">
                    <div className="field-stack structure-search-query">
                      <label htmlFor="pubchem-query">PubChem CID、名称或关键词</label>
                      <SearchHistoryInput
                        disabled={isBusy}
                        history={pubchemSearchHistory.history}
                        id="pubchem-query"
                        onDelete={pubchemSearchHistory.remove}
                        value={pubchemQuery}
                        onChange={(value) => {
                          pubchemSearchGenerationRef.current += 1;
                          previewGenerationRef.current.ligand += 1;
                          setPubchemQuery(value);
                          setPubchemResults(null);
                          setLigandPreview(null);
                          setPreviewingCandidateId("");
                        }}
                        placeholder="例如 5291 或 imatinib"
                      />
                    </div>
                    <div className="field-stack">
                      <label htmlFor="pubchem-query-type">搜索方式</label>
                      <select
                        disabled={isBusy}
                        id="pubchem-query-type"
                        value={pubchemQueryType}
                        onChange={(event) => {
                          pubchemSearchGenerationRef.current += 1;
                          previewGenerationRef.current.ligand += 1;
                          setPubchemQueryType(event.target.value as PubchemQueryType);
                          setPubchemResults(null);
                          setLigandPreview(null);
                          setPreviewingCandidateId("");
                        }}
                      >
                        <option value="auto">自动识别</option>
                        <option value="cid">CID</option>
                        <option value="name">名称</option>
                        <option value="keyword">关键词</option>
                      </select>
                    </div>
                    <div className="field-stack">
                      <label htmlFor="pubchem-limit">候选数量</label>
                      <input
                        disabled={isBusy}
                        id="pubchem-limit"
                        type="number"
                        min="1"
                        max={MAX_SEARCH_LIMIT}
                        step="1"
                        value={pubchemLimit}
                        onChange={(event) => {
                          pubchemSearchGenerationRef.current += 1;
                          previewGenerationRef.current.ligand += 1;
                          setPubchemLimit(event.target.value);
                          setPubchemResults(null);
                          setLigandPreview(null);
                          setPreviewingCandidateId("");
                        }}
                      />
                    </div>
                    <div className="structure-search-submit">
                      <ActionButton
                        aria-describedby="ligand-operation-status"
                        variant="primary"
                        disabled={isBusy || !pubchemQuery.trim()}
                        onClick={() => void searchCandidates("pubchem")}
                      >
                        {busyAction === "search-ligand" ? "正在搜索…" : "搜索配体候选"}
                      </ActionButton>
                    </div>
                  </div>

                  {renderCandidates("ligand", pubchemResults)}
                </section>

                <div className="structure-source-actions">
                  <div className="structure-source-actions-copy">
                    <span>本地文件</span>
                    <strong>导入 SDF / MOL</strong>
                    <p>选择本机原始结构后，立即尝试生成配体 PDBQT。</p>
                  </div>
                  <ActionButton
                    aria-describedby="ligand-operation-status"
                    className="structure-source-import-button"
                    disabled={isBusy}
                    onClick={() => void importLocalRaw("ligand")}
                  >
                    {busyAction === "prepare-ligand" ? "正在处理…" : "导入并自动转换"}
                  </ActionButton>
                  <label className="checkbox-row structure-source-overwrite">
                    <input
                      type="checkbox"
                      checked={overwritePubchem}
                      disabled={isBusy}
                      onChange={(event) => setOverwritePubchem(event.target.checked)}
                    />
                    在线候选允许覆盖同名 raw 文件
                  </label>
                  <AdvancedDetails className="structure-source-manage" summary="管理配体原始结构">
                    <label className="checkbox-row">
                      <input
                        type="checkbox"
                        checked={deleteLigandRawFile}
                        disabled={isBusy}
                        onChange={(event) => setDeleteLigandRawFile(event.target.checked)}
                      />
                      清除记录时同时删除项目中的 raw 文件
                    </label>
                    <ActionButton variant="text" disabled={isBusy || !(ligandStatus?.raw_file || project.ligand.raw_file)} onClick={() => void clearRawRecord("ligand")}>
                      清除配体记录
                    </ActionButton>
                    {renderTechnicalDetails(ligandStatus, project.ligand.raw_file)}
                  </AdvancedDetails>
                </div>
              </article>
            </div>

            <aside className="structure-source-guidance" aria-label="结构选择与转换说明">
              <section>
                <span>选择规则</span>
                <strong>预览不会修改项目</strong>
                <p>只有明确点击某个候选的“选择并准备”，DockStart 才下载该结构并启动转换。</p>
              </section>
              <section>
                <span>转换失败怎么办</span>
                <strong>raw 文件会保留</strong>
                <p>打开“格式转换与 PDBQT 准备”检查工具链、stderr 和日志，或改为导入已有 PDBQT。</p>
              </section>
            </aside>

            <div className="next-step-strip">
              <div>
                <strong>自动转换已启用</strong>
                <p>选择在线候选或导入本地原始结构后，DockStart 会立即尝试生成 PDBQT；失败时 raw 文件仍会保留。</p>
              </div>
              <div className="button-row end">
                <ActionButton onClick={() => onOpenImportPdbqt(project)}>导入已有 PDBQT</ActionButton>
                <ActionButton variant="primary" onClick={() => onOpenPreparation(project)}>查看转换状态与日志</ActionButton>
              </div>
            </div>

            <CommandResultPanel title="结构搜索与准备结果" message={message} rawError={rawError} />
          </div>
        </MainPanel>
      </BodyGrid>
    </PageShell>
  );
}
