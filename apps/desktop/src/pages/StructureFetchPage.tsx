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
  | "preview-receptor"
  | "preview-ligand"
  | "prepare-receptor"
  | "prepare-ligand"
  | null;

type CandidatePreviewState = {
  candidateId: string;
  label: string;
  response: CandidateStructurePreviewResponse;
};

const DEFAULT_SEARCH_LIMIT = 8;
const MAX_SEARCH_LIMIT = 20;

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

function errorDetails(error: { message?: string; suggestion?: string; raw_error?: string } | null | undefined): string {
  return [error?.message, error?.suggestion, error?.raw_error].filter(Boolean).join("\n");
}

function validatedLimit(value: string): number | null {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 1 && parsed <= MAX_SEARCH_LIMIT ? parsed : null;
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
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [busyAction, setBusyAction] = useState<BusyAction>(null);
  const [previewingCandidateId, setPreviewingCandidateId] = useState("");
  const [operationScope] = useState(() => new PageOperationScope());
  const rcsbSearchGenerationRef = useRef(0);
  const pubchemSearchGenerationRef = useRef(0);
  const searchRequestSequenceRef = useRef(0);
  const activeSearchRequestRef = useRef(0);

  useLayoutEffect(() => {
    operationScope.activate();
    return () => {
      rcsbSearchGenerationRef.current += 1;
      pubchemSearchGenerationRef.current += 1;
      activeSearchRequestRef.current = 0;
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
    const query = isRcsb ? rcsbQuery : pubchemQuery;
    const limit = validatedLimit(isRcsb ? rcsbLimit : pubchemLimit);
    if (limit === null) {
      setMessage(`候选数量必须是 1 到 ${MAX_SEARCH_LIMIT} 之间的整数。`);
      setRawError("");
      return;
    }

    const generationRef = isRcsb ? rcsbSearchGenerationRef : pubchemSearchGenerationRef;
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    const requestId = searchRequestSequenceRef.current + 1;
    searchRequestSequenceRef.current = requestId;
    activeSearchRequestRef.current = requestId;
    const token = operationScope.begin();

    setIsBusy(true);
    setBusyAction(isRcsb ? "search-receptor" : "search-ligand");
    if (isRcsb) setReceptorPreview(null);
    else setLigandPreview(null);
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

  const previewCandidate = async (target: PreparationTarget, candidate: StructureSearchCandidate) => {
    const isReceptor = target === "receptor";
    const label = `${candidate.source_id} · ${candidate.title || candidate.source_id}`;
    const selection = isReceptor
      ? { ...candidate.selection, format: pdbFormat }
      : candidate.selection;
    const token = operationScope.begin();
    setIsBusy(true);
    setBusyAction(isReceptor ? "preview-receptor" : "preview-ligand");
    setPreviewingCandidateId(candidate.candidate_id);
    if (isReceptor) setReceptorPreview(null);
    else setLigandPreview(null);
    setMessage(`正在加载 ${candidate.source_id} 的临时 3D 预览…`);
    setRawError("");
    try {
      const rawPayload = await invoke<string>("preview_structure_candidate", {
        selectionJson: JSON.stringify(selection),
      });
      const response = operationScope.parseIfCurrent(token, rawPayload, parseCandidatePreviewResponse);
      if (!response) return;
      if (!response.ok) {
        setMessage(response.error?.message ?? "候选结构预览失败。");
        setRawError(errorDetails(response.error));
        return;
      }
      const state = { candidateId: candidate.candidate_id, label, response };
      if (isReceptor) setReceptorPreview(state);
      else setLigandPreview(state);
      setMessage(response.message || `${candidate.source_id} 已加载到临时预览。`);
    } catch (error) {
      operationScope.commit(token, () => {
        setMessage("无法加载候选结构的临时 3D 预览。");
        setRawError(error instanceof Error ? error.message : String(error));
      });
    } finally {
      if (operationScope.finish(token)) {
        setPreviewingCandidateId("");
        setBusyAction(null);
        setIsBusy(false);
      }
    }
  };

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
        multiple: false,
        title: isReceptor ? "选择受体 PDB / CIF" : "选择配体 SDF / MOL",
        filters: [isReceptor
          ? { name: "受体原始结构", extensions: ["pdb", "cif"] }
          : { name: "配体原始结构", extensions: ["sdf", "mol"] }],
      });
      const sourcePath = Array.isArray(selected) ? selected[0] ?? "" : selected ?? "";
      if (!sourcePath) return;

      operationScope.commit(token, () => {
        setIsBusy(true);
        setBusyAction(isReceptor ? "prepare-receptor" : "prepare-ligand");
        setMessage(`正在导入${label}原始结构…`);
      });
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
                return (
                  <article className="structure-candidate-item" key={candidate.candidate_id}>
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
                        aria-describedby={statusId}
                        disabled={isBusy}
                        aria-label={`临时预览 ${candidate.source_id}`}
                        onClick={() => void previewCandidate(target, candidate)}
                      >
                        {previewingCandidateId === candidate.candidate_id ? "加载中…" : "3D 预览"}
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
                      <input
                        disabled={isBusy}
                        id="rcsb-query"
                        value={rcsbQuery}
                        onChange={(event) => {
                          rcsbSearchGenerationRef.current += 1;
                          setRcsbQuery(event.target.value);
                          setRcsbResults(null);
                          setReceptorPreview(null);
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
                          setRcsbQueryType(event.target.value as RcsbQueryType);
                          setRcsbResults(null);
                          setReceptorPreview(null);
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
                          setRcsbLimit(event.target.value);
                          setRcsbResults(null);
                          setReceptorPreview(null);
                        }}
                      />
                    </div>
                    <div className="field-stack">
                      <label htmlFor="pdb-format">下载格式</label>
                      <select disabled={isBusy} id="pdb-format" value={pdbFormat} onChange={(event) => {
                        setPdbFormat(event.target.value);
                        setReceptorPreview(null);
                      }}>
                        <option value="pdb">PDB</option>
                        <option value="cif">mmCIF</option>
                      </select>
                    </div>
                  </div>
                  <div className="button-row">
                    <ActionButton
                      aria-describedby="receptor-operation-status"
                      variant="primary"
                      disabled={isBusy || !rcsbQuery.trim()}
                      onClick={() => void searchCandidates("rcsb")}
                    >
                      {busyAction === "search-receptor" ? "正在搜索…" : "搜索受体候选"}
                    </ActionButton>
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
                      <input
                        disabled={isBusy}
                        id="pubchem-query"
                        value={pubchemQuery}
                        onChange={(event) => {
                          pubchemSearchGenerationRef.current += 1;
                          setPubchemQuery(event.target.value);
                          setPubchemResults(null);
                          setLigandPreview(null);
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
                          setPubchemQueryType(event.target.value as PubchemQueryType);
                          setPubchemResults(null);
                          setLigandPreview(null);
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
                          setPubchemLimit(event.target.value);
                          setPubchemResults(null);
                          setLigandPreview(null);
                        }}
                      />
                    </div>
                  </div>
                  <div className="button-row">
                    <ActionButton
                      aria-describedby="ligand-operation-status"
                      variant="primary"
                      disabled={isBusy || !pubchemQuery.trim()}
                      onClick={() => void searchCandidates("pubchem")}
                    >
                      {busyAction === "search-ligand" ? "正在搜索…" : "搜索配体候选"}
                    </ActionButton>
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
