import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import CommandResultPanel from "../components/CommandResultPanel";
import { BodyGrid, MainPanel, PageHero, PageShell, RightRail, RightRailSection } from "../components/layout/PageLayout";
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
  const activeTaskAbortRef = useRef<AbortController | null>(null);

  useEffect(() => () => activeTaskAbortRef.current?.abort(), []);

  useEffect(() => {
    setProject(initialProject);
  }, [initialProject]);

  const applyProjectResponse = useCallback(
    (response: ProjectResponse, fallbackMessage: string, announce = true) => {
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
        return;
      }
      if (announce) {
        setMessage(response.error?.message ?? "原始结构操作失败。");
        setRawError(errorDetails(response.error));
      }
    },
    [onProjectChange],
  );

  const refreshRawStatus = useCallback(async (announce = false) => {
    const rawPayload = await invoke<string>("get_raw_files_status", {
      projectDir: project.project_dir,
    });
    const response = parseProjectResponse(rawPayload);
    applyProjectResponse(response, "原始结构状态已刷新。", announce);
    return response;
  }, [applyProjectResponse, project.project_dir]);

  const reloadStatus = useCallback(async () => {
    setIsBusy(true);
    setBusyAction("refresh");
    try {
      await refreshRawStatus(true);
    } catch (error) {
      setMessage("无法读取原始结构状态。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyAction(null);
      setIsBusy(false);
    }
  }, [refreshRawStatus]);

  useEffect(() => {
    void reloadStatus();
  }, [reloadStatus]);

  const waitForTask = useCallback(
    async (started: BackgroundTaskStatus, controller: AbortController) => waitForBackgroundTask(
      started.task_id,
      (task) => {
        setMessage(task.progress.message || task.message);
        if (task.error) setRawError(task.error);
      },
      controller.signal,
    ),
    [],
  );

  const prepareRaw = useCallback(async (target: PreparationTarget, controller: AbortController) => {
    const label = target === "receptor" ? "受体" : "配体";
    setBusyAction(target === "receptor" ? "prepare-receptor" : "prepare-ligand");
    setMessage(`${label}原始结构已保存，正在自动转换为 PDBQT…`);
    setRawError("");

    const started = await startPreparationTask(project.project_dir, target, true);
    const completed = await waitForTask(started, controller);
    if (completed.status === "cancelled") {
      setMessage(`${label}自动转换任务已取消；原始结构仍保留在项目 raw/ 目录。`);
      return false;
    }
    if (!completed.result_json) {
      throw new Error(completed.error || completed.message || `${label}自动转换任务没有返回结果。`);
    }

    const response = parsePreparationResponse(completed.result_json);
    if (response.project) {
      setProject(response.project);
      onProjectChange(response.project);
    }
    if (!response.ok) {
      setMessage(
        `${label}原始结构已保留，但自动转换为 PDBQT 失败。请打开“格式转换与 PDBQT 准备”查看日志、检查工具链后重试。`,
      );
      setRawError(errorDetails(response.error) || completed.error || response.message || "自动转换失败。");
      return false;
    }

    setMessage(`${label}已下载或导入，并自动转换为 PDBQT。请继续人工检查结构、质子化和电荷是否合理。`);
    setRawError("");
    return true;
  }, [onProjectChange, project.project_dir, waitForTask]);

  const searchCandidates = async (provider: "rcsb" | "pubchem") => {
    const isRcsb = provider === "rcsb";
    const query = isRcsb ? rcsbQuery : pubchemQuery;
    const limit = validatedLimit(isRcsb ? rcsbLimit : pubchemLimit);
    if (limit === null) {
      setMessage(`候选数量必须是 1 到 ${MAX_SEARCH_LIMIT} 之间的整数。`);
      setRawError("");
      return;
    }

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
      const response = parseSearchResponse(rawPayload);
      if (isRcsb) setRcsbResults(response);
      else setPubchemResults(response);
      if (!response.ok) {
        setMessage(response.error?.message ?? "候选搜索失败。");
        setRawError(errorDetails(response.error));
        return;
      }
      setMessage(response.message || `找到 ${response.returned_count} 个候选，请明确选择后再下载。`);
    } catch (error) {
      setMessage(isRcsb ? "无法搜索 RCSB PDB 候选结构。" : "无法搜索 PubChem 候选化合物。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyAction(null);
      setIsBusy(false);
    }
  };

  const previewCandidate = async (target: PreparationTarget, candidate: StructureSearchCandidate) => {
    const isReceptor = target === "receptor";
    const label = `${candidate.source_id} · ${candidate.title || candidate.source_id}`;
    const selection = isReceptor
      ? { ...candidate.selection, format: pdbFormat }
      : candidate.selection;
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
      const response = parseCandidatePreviewResponse(rawPayload);
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
      setMessage("无法加载候选结构的临时 3D 预览。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setPreviewingCandidateId("");
      setBusyAction(null);
      setIsBusy(false);
    }
  };

  const selectAndPrepareCandidate = async (target: PreparationTarget, candidate: StructureSearchCandidate) => {
    const isReceptor = target === "receptor";
    const label = isReceptor ? "受体" : "配体";
    activeTaskAbortRef.current?.abort();
    const controller = new AbortController();
    activeTaskAbortRef.current = controller;
    setIsBusy(true);
    setBusyAction(isReceptor ? "prepare-receptor" : "prepare-ligand");
    setMessage(`正在下载已选择的${label}候选：${candidate.source_id}…`);
    setRawError("");
    try {
      const selection = candidate.selection;
      const started = isReceptor
        ? await startPdbFetchTask(
          project.project_dir,
          selection.pdb_id || candidate.source_id,
          pdbFormat,
          overwritePdb,
        )
        : await startPubchemFetchTask(
          project.project_dir,
          selection.query || candidate.source_id,
          selection.query_type === "cid" ? "cid" : "name",
          "sdf",
          overwritePubchem,
        );
      const completed = await waitForTask(started, controller);
      if (completed.status === "cancelled") {
        setMessage(`${label}结构获取任务已取消。`);
        return;
      }
      if (!completed.result_json) {
        throw new Error(completed.error || completed.message || `${label}结构获取任务没有返回结果。`);
      }
      const response = parseProjectResponse(completed.result_json);
      if (!response.ok) {
        applyProjectResponse(response, `${label}原始结构下载失败。`);
        return;
      }
      applyProjectResponse(response, `${label}原始结构已下载。`);
      await prepareRaw(target, controller);
      await refreshRawStatus(false);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setMessage(`无法完成${label}候选的下载与自动转换。若 raw 文件已经写入，它会被保留；可打开“格式转换与 PDBQT 准备”检查并重试。`);
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      if (activeTaskAbortRef.current === controller) activeTaskAbortRef.current = null;
      setBusyAction(null);
      setIsBusy(false);
    }
  };

  const importLocalRaw = async (target: PreparationTarget) => {
    const isReceptor = target === "receptor";
    const label = isReceptor ? "受体" : "配体";
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

      activeTaskAbortRef.current?.abort();
      const controller = new AbortController();
      activeTaskAbortRef.current = controller;
      setIsBusy(true);
      setBusyAction(isReceptor ? "prepare-receptor" : "prepare-ligand");
      try {
        setMessage(`正在导入${label}原始结构…`);
        const rawPayload = await invoke<string>(
          isReceptor ? "import_receptor_raw_file" : "import_ligand_raw_file",
          { projectDir: project.project_dir, sourcePath },
        );
        const response = parseProjectResponse(rawPayload);
        if (!response.ok) {
          applyProjectResponse(response, `无法导入${label}原始结构。`);
          return;
        }
        applyProjectResponse(response, `${label}原始结构已导入。`);
        await prepareRaw(target, controller);
        await refreshRawStatus(false);
      } finally {
        if (activeTaskAbortRef.current === controller) activeTaskAbortRef.current = null;
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setMessage(`无法完成${label}原始结构的导入与自动转换。若 raw 文件已经写入，它会被保留。`);
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyAction(null);
      setIsBusy(false);
    }
  };

  const clearRawRecord = async (role: PreparationTarget) => {
    const label = role === "receptor" ? "受体" : "配体";
    const deleteFile = role === "receptor" ? deleteReceptorRawFile : deleteLigandRawFile;
    if (!window.confirm(`确定清除${label} raw 记录吗？Vina 输入文件不会被删除。`)) return;

    setIsBusy(true);
    setMessage("");
    setRawError("");
    try {
      const command = role === "receptor" ? "clear_receptor_raw_record" : "clear_ligand_raw_record";
      const rawPayload = await invoke<string>(command, {
        projectDir: project.project_dir,
        deleteFile,
      });
      applyProjectResponse(parseProjectResponse(rawPayload), `${label} raw 记录已清除。`);
    } catch (error) {
      setMessage(`无法清除${label} raw 记录。`);
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  const receptorStatus = receptorRaw ?? findStatus(files, "receptor_raw");
  const ligandStatus = ligandRaw ?? findStatus(files, "ligand_raw");

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
    return (
      <section className="structure-candidate-panel" aria-label={`${label}候选列表`}>
        <div className="structure-candidate-heading">
          <div>
            <strong>搜索结果</strong>
            <span>显示 {response.returned_count} / {response.total_count} 个候选</span>
          </div>
          <StatusBadge tone={response.candidates.length ? "info" : "warning"}>
            {response.candidates.length ? "等待选择" : "无结果"}
          </StatusBadge>
        </div>
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
                      disabled={isBusy}
                      aria-label={`临时预览 ${candidate.source_id}`}
                      onClick={() => void previewCandidate(target, candidate)}
                    >
                      {previewingCandidateId === candidate.candidate_id ? "加载中…" : "3D 预览"}
                    </ActionButton>
                    <ActionButton
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

      <BodyGrid>
        <MainPanel>
          <div className="main-panel-content structure-source-content">
            <WarningCallout title="自动转换不等于科学检查">
              <p>下载或导入后会立即尝试生成 PDBQT，但仍需人工检查受体链、水、金属、辅因子，以及配体质子化、电荷和构象。</p>
            </WarningCallout>

            <div className="two-column-grid structure-source-grid">
              <article className="task-card structure-source-card" data-layout="task-card">
                <div className="section-card-header">
                  <div>
                    <span className="structure-source-step">01 · RECEPTOR</span>
                    <h2>搜索或导入受体</h2>
                  </div>
                  <StatusBadge tone={statusTone(receptorStatus?.status)}>{statusLabel(receptorStatus?.status)}</StatusBadge>
                </div>
                <p className="muted-path">{receptorStatus?.raw_file || project.receptor.raw_file || "未记录 raw 文件"}</p>

                <div className="structure-search-controls">
                  <div className="field-stack structure-search-query">
                    <label htmlFor="rcsb-query">RCSB PDB ID 或关键词</label>
                    <input
                      id="rcsb-query"
                      value={rcsbQuery}
                      onChange={(event) => {
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
                      id="rcsb-query-type"
                      value={rcsbQueryType}
                      onChange={(event) => {
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
                      id="rcsb-limit"
                      type="number"
                      min="1"
                      max={MAX_SEARCH_LIMIT}
                      step="1"
                      value={rcsbLimit}
                      onChange={(event) => {
                        setRcsbLimit(event.target.value);
                        setRcsbResults(null);
                        setReceptorPreview(null);
                      }}
                    />
                  </div>
                  <div className="field-stack">
                    <label htmlFor="pdb-format">下载格式</label>
                    <select id="pdb-format" value={pdbFormat} onChange={(event) => {
                      setPdbFormat(event.target.value);
                      setReceptorPreview(null);
                    }}>
                      <option value="pdb">PDB</option>
                      <option value="cif">mmCIF</option>
                    </select>
                  </div>
                </div>

                <label className="checkbox-row">
                  <input type="checkbox" checked={overwritePdb} onChange={(event) => setOverwritePdb(event.target.checked)} />
                  若同名 raw 文件已存在，允许覆盖
                </label>
                <div className="button-row">
                  <ActionButton variant="primary" disabled={isBusy || !rcsbQuery.trim()} onClick={() => void searchCandidates("rcsb")}>
                    {busyAction === "search-receptor" ? "正在搜索…" : "搜索受体候选"}
                  </ActionButton>
                  <ActionButton disabled={isBusy} onClick={() => void importLocalRaw("receptor")}>
                    {busyAction === "prepare-receptor" ? "正在处理…" : "导入并自动转换 PDB / CIF"}
                  </ActionButton>
                </div>

                {renderCandidates("receptor", rcsbResults)}

                <AdvancedDetails summary="管理受体原始结构">
                  <label className="checkbox-row">
                    <input type="checkbox" checked={deleteReceptorRawFile} onChange={(event) => setDeleteReceptorRawFile(event.target.checked)} />
                    清除记录时同时删除项目中的 raw 文件
                  </label>
                  <ActionButton variant="text" disabled={isBusy || !(receptorStatus?.raw_file || project.receptor.raw_file)} onClick={() => void clearRawRecord("receptor")}>
                    清除受体记录
                  </ActionButton>
                  {renderTechnicalDetails(receptorStatus, project.receptor.raw_file)}
                </AdvancedDetails>
              </article>

              <article className="task-card structure-source-card" data-layout="task-card">
                <div className="section-card-header">
                  <div>
                    <span className="structure-source-step">02 · LIGAND</span>
                    <h2>搜索或导入配体</h2>
                  </div>
                  <StatusBadge tone={statusTone(ligandStatus?.status)}>{statusLabel(ligandStatus?.status)}</StatusBadge>
                </div>
                <p className="muted-path">{ligandStatus?.raw_file || project.ligand.raw_file || "未记录 raw 文件"}</p>

                <div className="structure-search-controls">
                  <div className="field-stack structure-search-query">
                    <label htmlFor="pubchem-query">PubChem CID、名称或关键词</label>
                    <input
                      id="pubchem-query"
                      value={pubchemQuery}
                      onChange={(event) => {
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
                      id="pubchem-query-type"
                      value={pubchemQueryType}
                      onChange={(event) => {
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
                      id="pubchem-limit"
                      type="number"
                      min="1"
                      max={MAX_SEARCH_LIMIT}
                      step="1"
                      value={pubchemLimit}
                      onChange={(event) => {
                        setPubchemLimit(event.target.value);
                        setPubchemResults(null);
                        setLigandPreview(null);
                      }}
                    />
                  </div>
                </div>

                <label className="checkbox-row">
                  <input type="checkbox" checked={overwritePubchem} onChange={(event) => setOverwritePubchem(event.target.checked)} />
                  若同名 raw 文件已存在，允许覆盖
                </label>
                <div className="button-row">
                  <ActionButton variant="primary" disabled={isBusy || !pubchemQuery.trim()} onClick={() => void searchCandidates("pubchem")}>
                    {busyAction === "search-ligand" ? "正在搜索…" : "搜索配体候选"}
                  </ActionButton>
                  <ActionButton disabled={isBusy} onClick={() => void importLocalRaw("ligand")}>
                    {busyAction === "prepare-ligand" ? "正在处理…" : "导入并自动转换 SDF / MOL"}
                  </ActionButton>
                </div>

                {renderCandidates("ligand", pubchemResults)}

                <AdvancedDetails summary="管理配体原始结构">
                  <label className="checkbox-row">
                    <input type="checkbox" checked={deleteLigandRawFile} onChange={(event) => setDeleteLigandRawFile(event.target.checked)} />
                    清除记录时同时删除项目中的 raw 文件
                  </label>
                  <ActionButton variant="text" disabled={isBusy || !(ligandStatus?.raw_file || project.ligand.raw_file)} onClick={() => void clearRawRecord("ligand")}>
                    清除配体记录
                  </ActionButton>
                  {renderTechnicalDetails(ligandStatus, project.ligand.raw_file)}
                </AdvancedDetails>
              </article>
            </div>

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

        <RightRail>
          <RightRailSection title="当前输入状态">
            <dl className="mode-context-list">
              <div>
                <dt>受体 raw</dt>
                <dd>{statusLabel(receptorStatus?.status)}</dd>
              </div>
              <div>
                <dt>受体 PDBQT</dt>
                <dd>{project.receptor.file ? "已准备" : "未准备"}</dd>
              </div>
              <div>
                <dt>配体 raw</dt>
                <dd>{statusLabel(ligandStatus?.status)}</dd>
              </div>
              <div>
                <dt>配体 PDBQT</dt>
                <dd>{project.ligand.file ? "已准备" : "未准备"}</dd>
              </div>
            </dl>
          </RightRailSection>

          <RightRailSection title="选择规则">
            <p>搜索和临时 3D 预览都不会写入项目。明确选择某一项后，DockStart 才下载该结构并自动转换。</p>
          </RightRailSection>

          <RightRailSection title="转换失败怎么办">
            <p>原始结构会保留在 raw/。打开“格式转换与 PDBQT 准备”查看工具链、stderr 和日志，也可改为导入已准备好的 PDBQT。</p>
          </RightRailSection>
        </RightRail>
      </BodyGrid>
    </PageShell>
  );
}
