import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import CommandResultPanel from "../components/CommandResultPanel";
import { BodyGrid, MainPanel, PageHero, PageShell, RightRail, RightRailSection } from "../components/layout/PageLayout";
import StatusBadge from "../components/StatusBadge";
import WarningCallout from "../components/WarningCallout";
import type { DockStartProject, ProjectResponse, RawStructureStatus, RunFileStatus } from "../types";
import {
  startPdbFetchTask,
  startPubchemFetchTask,
  waitForBackgroundTask,
  type BackgroundTaskStatus,
} from "../utils/backgroundTasks";

type StructureFetchPageProps = {
  project: DockStartProject;
  onBack: () => void;
  onOpenImportPdbqt: (project: DockStartProject) => void;
  onOpenPreparation: (project: DockStartProject) => void;
  onProjectChange: (project: DockStartProject) => void;
};

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
  const [pdbId, setPdbId] = useState("");
  const [pdbFormat, setPdbFormat] = useState("pdb");
  const [overwritePdb, setOverwritePdb] = useState(false);
  const [deleteReceptorRawFile, setDeleteReceptorRawFile] = useState(false);
  const [pubchemQueryType, setPubchemQueryType] = useState<"cid" | "name">("cid");
  const [pubchemQuery, setPubchemQuery] = useState("");
  const [overwritePubchem, setOverwritePubchem] = useState(false);
  const [deleteLigandRawFile, setDeleteLigandRawFile] = useState(false);
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const activeTaskAbortRef = useRef<AbortController | null>(null);

  useEffect(() => () => activeTaskAbortRef.current?.abort(), []);

  useEffect(() => {
    setProject(initialProject);
  }, [initialProject]);

  const applyProjectResponse = useCallback(
    (response: ProjectResponse, fallbackMessage: string) => {
      if (response.ok) {
        if (response.project) {
          setProject(response.project);
          onProjectChange(response.project);
        }
        const nextFiles = response.files ?? [];
        setFiles(nextFiles);
        setReceptorRaw(response.receptor ?? findStatus(nextFiles, "receptor_raw"));
        setLigandRaw(response.ligand ?? findStatus(nextFiles, "ligand_raw"));
        setMessage(response.message ?? fallbackMessage);
        setRawError("");
        return;
      }
      setMessage(response.error?.message ?? "原始结构操作失败。");
      setRawError(response.error?.raw_error ?? "");
    },
    [onProjectChange],
  );

  const reloadStatus = useCallback(async () => {
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>("get_raw_files_status", {
        projectDir: project.project_dir,
      });
      applyProjectResponse(parseProjectResponse(rawPayload), "原始结构状态已刷新。");
    } catch (error) {
      setMessage("无法读取原始结构状态。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  }, [applyProjectResponse, project.project_dir]);

  useEffect(() => {
    void reloadStatus();
  }, [reloadStatus]);

  const waitForFetchTask = useCallback(
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

  const fetchPdb = async () => {
    setIsBusy(true);
    setMessage("");
    setRawError("");
    activeTaskAbortRef.current?.abort();
    const controller = new AbortController();
    activeTaskAbortRef.current = controller;
    try {
      const started = await startPdbFetchTask(project.project_dir, pdbId, pdbFormat, overwritePdb);
      const completed = await waitForFetchTask(started, controller);
      if (completed.status === "cancelled") {
        setMessage("受体结构获取任务已取消。");
        return;
      }
      if (completed.status === "failed" || !completed.result_json) {
        throw new Error(completed.error || completed.message || "受体结构获取任务没有返回结果。");
      }
      const response = parseProjectResponse(completed.result_json);
      applyProjectResponse(response, "受体原始结构已下载。");
      if (response.ok) await reloadStatus();
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setMessage("无法下载 RCSB 受体结构。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      if (activeTaskAbortRef.current === controller) activeTaskAbortRef.current = null;
      setIsBusy(false);
    }
  };

  const fetchPubchem = async () => {
    setIsBusy(true);
    setMessage("");
    setRawError("");
    activeTaskAbortRef.current?.abort();
    const controller = new AbortController();
    activeTaskAbortRef.current = controller;
    try {
      const started = await startPubchemFetchTask(
        project.project_dir,
        pubchemQuery,
        pubchemQueryType,
        "sdf",
        overwritePubchem,
      );
      const completed = await waitForFetchTask(started, controller);
      if (completed.status === "cancelled") {
        setMessage("配体结构获取任务已取消。");
        return;
      }
      if (completed.status === "failed" || !completed.result_json) {
        throw new Error(completed.error || completed.message || "配体结构获取任务没有返回结果。");
      }
      const response = parseProjectResponse(completed.result_json);
      applyProjectResponse(response, "配体原始结构已下载。");
      if (response.ok) await reloadStatus();
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setMessage("无法下载 PubChem 配体结构。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      if (activeTaskAbortRef.current === controller) activeTaskAbortRef.current = null;
      setIsBusy(false);
    }
  };

  const importLocalRaw = async (role: "receptor" | "ligand") => {
    const isReceptor = role === "receptor";
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
      setIsBusy(true);
      const rawPayload = await invoke<string>(
        isReceptor ? "import_receptor_raw_file" : "import_ligand_raw_file",
        { projectDir: project.project_dir, sourcePath },
      );
      const response = parseProjectResponse(rawPayload);
      applyProjectResponse(response, `${label}原始结构已导入。`);
      if (response.ok) await reloadStatus();
    } catch (error) {
      setMessage(`无法导入${label}原始结构。`);
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  const clearRawRecord = async (role: "receptor" | "ligand") => {
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

  return (
    <PageShell labelledBy="structure-fetch-title">
      <PageHero
        eyebrow="结构来源 · SOURCE"
        title="获取或导入原始结构"
        titleId="structure-fetch-title"
        description="在线搜索 RCSB / PubChem，或从电脑导入原始结构；完成后再转换为 Vina 使用的 PDBQT。"
        actions={
          <>
          <ActionButton variant="text" onClick={onBack}>返回</ActionButton>
          <ActionButton onClick={() => void reloadStatus()} disabled={isBusy}>刷新状态</ActionButton>
          </>
        }
      />

      <BodyGrid>
        <MainPanel>
          <div className="main-panel-content">
            <WarningCallout title="这里获取的是原始结构，不是最终 PDBQT">
              <p>下一步会将受体 PDB/CIF 与配体 SDF/MOL 准备并转换为 PDBQT；这不只是修改文件扩展名。</p>
            </WarningCallout>

            <div className="two-column-grid">
              <article className="task-card" data-layout="task-card">
                <div className="section-card-header">
                  <h2>受体</h2>
                  <StatusBadge tone={statusTone(receptorStatus?.status)}>{statusLabel(receptorStatus?.status)}</StatusBadge>
                </div>
                <p className="muted-path">{receptorStatus?.raw_file || project.receptor.raw_file || "未记录 raw 文件"}</p>
                <div className="field-stack">
                  <label htmlFor="pdb-id">在线搜索：RCSB PDB ID</label>
                  <input id="pdb-id" value={pdbId} onChange={(event) => setPdbId(event.target.value)} placeholder="例如 1HSG" />
                </div>
                <div className="field-stack">
                  <label htmlFor="pdb-format">格式</label>
                  <select id="pdb-format" value={pdbFormat} onChange={(event) => setPdbFormat(event.target.value)}>
                    <option value="pdb">pdb</option>
                    <option value="cif">cif</option>
                  </select>
                </div>
                <label className="checkbox-row">
                  <input type="checkbox" checked={overwritePdb} onChange={(event) => setOverwritePdb(event.target.checked)} />
                  覆盖已有 raw 文件
                </label>
                <div className="button-row">
                  <ActionButton variant="primary" disabled={isBusy || !pdbId.trim()} onClick={() => void fetchPdb()}>
                    下载受体
                  </ActionButton>
                  <ActionButton disabled={isBusy} onClick={() => void importLocalRaw("receptor")}>
                    从电脑导入 PDB / CIF
                  </ActionButton>
                </div>
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

              <article className="task-card" data-layout="task-card">
                <div className="section-card-header">
                  <h2>配体</h2>
                  <StatusBadge tone={statusTone(ligandStatus?.status)}>{statusLabel(ligandStatus?.status)}</StatusBadge>
                </div>
                <p className="muted-path">{ligandStatus?.raw_file || project.ligand.raw_file || "未记录 raw 文件"}</p>
                <div className="field-stack">
                  <label htmlFor="pubchem-query-type">在线搜索：PubChem 查询方式</label>
                  <select
                    id="pubchem-query-type"
                    value={pubchemQueryType}
                    onChange={(event) => setPubchemQueryType(event.target.value as "cid" | "name")}
                  >
                    <option value="cid">CID</option>
                    <option value="name">名称</option>
                  </select>
                </div>
                <div className="field-stack">
                  <label htmlFor="pubchem-query">PubChem 查询</label>
                  <input
                    id="pubchem-query"
                    value={pubchemQuery}
                    onChange={(event) => setPubchemQuery(event.target.value)}
                    placeholder={pubchemQueryType === "cid" ? "例如 2244" : "例如 aspirin"}
                  />
                </div>
                <label className="checkbox-row">
                  <input type="checkbox" checked={overwritePubchem} onChange={(event) => setOverwritePubchem(event.target.checked)} />
                  覆盖已有 raw 文件
                </label>
                <div className="button-row">
                  <ActionButton variant="primary" disabled={isBusy || !pubchemQuery.trim()} onClick={() => void fetchPubchem()}>
                    下载配体
                  </ActionButton>
                  <ActionButton disabled={isBusy} onClick={() => void importLocalRaw("ligand")}>
                    从电脑导入 SDF / MOL
                  </ActionButton>
                </div>
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
                <strong>下一步：转换为 PDBQT</strong>
                <p>使用 Assisted 工具链转换原始结构，或直接导入已经准备好的 PDBQT。</p>
              </div>
              <div className="button-row end">
                <ActionButton onClick={() => onOpenImportPdbqt(project)}>导入 PDBQT</ActionButton>
                <ActionButton variant="primary" onClick={() => onOpenPreparation(project)}>开始格式转换</ActionButton>
              </div>
            </div>

            <CommandResultPanel title="结构获取结果" message={message} rawError={rawError} />
          </div>
        </MainPanel>

        <RightRail>
          <RightRailSection title="原始结构状态">
            <dl className="mode-context-list">
              <div>
                <dt>受体</dt>
                <dd>{statusLabel(receptorStatus?.status)}</dd>
              </div>
              <div>
                <dt>配体</dt>
                <dd>{statusLabel(ligandStatus?.status)}</dd>
              </div>
            </dl>
          </RightRailSection>

          <RightRailSection title="下一步">
            <p>两个原始结构就绪后，进入“格式转换与 PDBQT 准备”。已有 PDBQT 也可直接导入并跳过转换。</p>
          </RightRailSection>
        </RightRail>
      </BodyGrid>
    </PageShell>
  );
}
