import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { DockStartProject, ProjectResponse, RawStructureStatus, RunFileStatus } from "../types";

type StructureFetchPageProps = {
  project: DockStartProject;
  onBack: () => void;
  onOpenImportPdbqt: (project: DockStartProject) => void;
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
    format: parsed.format,
    url: parsed.url,
    deleted_file: parsed.deleted_file,
    message: parsed.message,
    error: parsed.error,
  };
}

function statusText(status: RunFileStatus["status"] | undefined): string {
  if (status === "ok") {
    return "已下载";
  }
  if (status === "empty") {
    return "文件为空";
  }
  if (status === "error") {
    return "状态错误";
  }
  return "未下载";
}

function statusClass(status: RunFileStatus["status"] | undefined): string {
  if (status === "ok") {
    return "status-ok";
  }
  if (status === "missing") {
    return "status-missing";
  }
  return "status-error";
}

function findStatus(files: RunFileStatus[], key: string): RawStructureStatus | null {
  return (files.find((file) => file.key === key) as RawStructureStatus | undefined) ?? null;
}

function formatBytes(value: number | undefined): string {
  const bytes = Number(value ?? 0);
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0 B";
  }
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function valueOrEmpty(value: string | undefined): string {
  return value && value.trim() ? value : "未记录";
}

export default function StructureFetchPage({
  project: initialProject,
  onBack,
  onOpenImportPdbqt,
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
  const [pubchemCid, setPubchemCid] = useState("");
  const [overwritePubchem, setOverwritePubchem] = useState(false);
  const [deleteLigandRawFile, setDeleteLigandRawFile] = useState(false);
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);

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
      applyProjectResponse(parseProjectResponse(rawPayload), "raw 文件状态已重新读取。");
    } catch (error) {
      setMessage("前端未能调用 raw 文件状态命令。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  }, [applyProjectResponse, project.project_dir]);

  useEffect(() => {
    void reloadStatus();
  }, [reloadStatus]);

  const fetchPdb = async () => {
    setIsBusy(true);
    setMessage("");
    setRawError("");
    try {
      const rawPayload = await invoke<string>("fetch_pdb_structure", {
        projectDir: project.project_dir,
        pdbId,
        format: pdbFormat,
        overwrite: overwritePdb,
      });
      const response = parseProjectResponse(rawPayload);
      applyProjectResponse(response, "RCSB PDB 原始受体结构已下载。");
      if (response.ok) {
        await reloadStatus();
      }
    } catch (error) {
      setMessage("前端未能调用 RCSB PDB 下载命令。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  const fetchPubchem = async () => {
    setIsBusy(true);
    setMessage("");
    setRawError("");
    try {
      const rawPayload = await invoke<string>("fetch_pubchem_ligand", {
        projectDir: project.project_dir,
        cid: pubchemCid,
        format: "sdf",
        overwrite: overwritePubchem,
      });
      const response = parseProjectResponse(rawPayload);
      applyProjectResponse(response, "PubChem 原始配体 SDF 已下载。");
      if (response.ok) {
        await reloadStatus();
      }
    } catch (error) {
      setMessage("前端未能调用 PubChem 下载命令。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  const clearRawRecord = async (role: "receptor" | "ligand") => {
    const label = role === "receptor" ? "受体" : "配体";
    const deleteFile = role === "receptor" ? deleteReceptorRawFile : deleteLigandRawFile;
    const confirmed = window.confirm(
      deleteFile
        ? `确定清除${label} raw 记录，并删除项目 raw/ 目录内对应 raw 文件吗？prepared PDBQT 不会被删除。`
        : `确定清除${label} raw 记录吗？raw 文件和 prepared PDBQT 都会保留。`,
    );
    if (!confirmed) {
      return;
    }

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
      setMessage(`前端未能调用${label} raw 清除命令。`);
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  const receptorStatus = receptorRaw ?? findStatus(files, "receptor_raw");
  const ligandStatus = ligandRaw ?? findStatus(files, "ligand_raw");

  const renderRawStatus = (status: RawStructureStatus | null, fallbackRawFile: string) => (
    <dl className="tool-meta raw-status-list">
      <div>
        <dt>来源</dt>
        <dd>{valueOrEmpty(status?.source)}</dd>
      </div>
      <div>
        <dt>查询 ID</dt>
        <dd>{valueOrEmpty(status?.source_id)}</dd>
      </div>
      <div>
        <dt>查询类型</dt>
        <dd>{valueOrEmpty(status?.query_type)}</dd>
      </div>
      <div>
        <dt>raw_file 记录</dt>
        <dd>
          <code>{status?.raw_file || fallbackRawFile || "未记录"}</code>
        </dd>
      </div>
      <div>
        <dt>文件是否存在</dt>
        <dd>{status?.exists ? "存在" : "不存在"}</dd>
      </div>
      <div>
        <dt>文件大小</dt>
        <dd>{formatBytes(status?.size_bytes ?? status?.size)}</dd>
      </div>
      <div>
        <dt>修改时间</dt>
        <dd>{valueOrEmpty(status?.modified_at)}</dd>
      </div>
      <div>
        <dt>绝对路径</dt>
        <dd>
          <code>{valueOrEmpty(status?.absolute_path)}</code>
        </dd>
      </div>
      <div>
        <dt>记录一致性</dt>
        <dd>{status?.record_consistent ? "记录一致" : "需要检查"}</dd>
      </div>
    </dl>
  );

  return (
    <section className="project-page" aria-labelledby="structure-fetch-title">
      <button className="text-button" type="button" onClick={onBack}>
        返回创建项目
      </button>

      <div className="page-heading">
        <p className="eyebrow">StructureFetchPage</p>
        <h1 id="structure-fetch-title">下载原始结构文件</h1>
        <p>
          当前页面只下载 raw 原始结构文件，不会自动生成 PDBQT。运行 Vina 仍需
          prepared/receptor.pdbqt 和 prepared/ligand.pdbqt。
        </p>
      </div>

      <div className="project-summary">
        <span>当前项目</span>
        <strong>{project.project_name}</strong>
        <code>{project.project_dir}</code>
      </div>

      <div className="disclaimer-note">
        raw 文件是从 RCSB/PubChem 下载的原始结构；prepared PDBQT 是 AutoDock Vina 可以读取的对接输入。
        下载 raw 后仍需要手动准备并导入 receptor.pdbqt 与 ligand.pdbqt。
      </div>

      <div className="import-grid raw-fetch-grid">
        <article className="import-card">
          <div className="tool-card-header">
            <h2>受体 raw 文件</h2>
            <span className={`status-badge ${statusClass(receptorStatus?.status)}`}>
              {statusText(receptorStatus?.status)}
            </span>
          </div>
          {renderRawStatus(receptorStatus, project.receptor.raw_file)}
          <label htmlFor="pdb-id">RCSB PDB ID</label>
          <input
            id="pdb-id"
            type="text"
            value={pdbId}
            onChange={(event) => setPdbId(event.target.value)}
            placeholder="例如 1HSG"
          />
          <label htmlFor="pdb-format">下载格式</label>
          <select id="pdb-format" value={pdbFormat} onChange={(event) => setPdbFormat(event.target.value)}>
            <option value="pdb">pdb</option>
            <option value="cif">cif</option>
          </select>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={overwritePdb}
              onChange={(event) => setOverwritePdb(event.target.checked)}
            />
            覆盖已有 raw 受体文件
          </label>
          {overwritePdb ? <div className="warning-note inline-note">已开启覆盖：重新下载会替换同名 raw 文件。</div> : null}
          <button className="secondary-button" type="button" disabled={isBusy} onClick={() => void fetchPdb()}>
            下载 RCSB PDB
          </button>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={deleteReceptorRawFile}
              onChange={(event) => setDeleteReceptorRawFile(event.target.checked)}
            />
            清除记录时同时删除 raw 文件
          </label>
          <button
            className="text-button inline"
            type="button"
            disabled={isBusy || !(receptorStatus?.raw_file || project.receptor.raw_file)}
            onClick={() => void clearRawRecord("receptor")}
          >
            清除受体 raw 记录
          </button>
        </article>

        <article className="import-card">
          <div className="tool-card-header">
            <h2>配体 raw 文件</h2>
            <span className={`status-badge ${statusClass(ligandStatus?.status)}`}>
              {statusText(ligandStatus?.status)}
            </span>
          </div>
          {renderRawStatus(ligandStatus, project.ligand.raw_file)}
          <label htmlFor="pubchem-cid">PubChem CID</label>
          <input
            id="pubchem-cid"
            type="text"
            value={pubchemCid}
            onChange={(event) => setPubchemCid(event.target.value)}
            placeholder="例如 2244"
          />
          <p className="placeholder-note">本轮只支持 CID，并下载 SDF 原始文件；不会生成 3D 或转 PDBQT。</p>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={overwritePubchem}
              onChange={(event) => setOverwritePubchem(event.target.checked)}
            />
            覆盖已有 raw 配体文件
          </label>
          {overwritePubchem ? <div className="warning-note inline-note">已开启覆盖：重新下载会替换同名 raw 文件。</div> : null}
          <button className="secondary-button" type="button" disabled={isBusy} onClick={() => void fetchPubchem()}>
            下载 PubChem SDF
          </button>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={deleteLigandRawFile}
              onChange={(event) => setDeleteLigandRawFile(event.target.checked)}
            />
            清除记录时同时删除 raw 文件
          </label>
          <button
            className="text-button inline"
            type="button"
            disabled={isBusy || !(ligandStatus?.raw_file || project.ligand.raw_file)}
            onClick={() => void clearRawRecord("ligand")}
          >
            清除配体 raw 记录
          </button>
        </article>
      </div>

      <div className="toolbar project-toolbar">
        <button className="text-button inline" type="button" disabled={isBusy} onClick={() => void reloadStatus()}>
          重新加载 raw 状态
        </button>
        <button className="secondary-button" type="button" onClick={() => onOpenImportPdbqt(project)}>
          进入 PDBQT 导入页
        </button>
      </div>

      <div className="warning-note">
        DockStart 当前不会调用 RDKit、Meeko、Open Babel、PLIP、MGLTools，也不会自动准备 docking 输入。raw 记录可以清除，
        但 prepared/receptor.pdbqt 和 prepared/ligand.pdbqt 不会因此删除。
      </div>

      {message ? <p className="settings-message">{message}</p> : null}
      {rawError ? (
        <details className="raw-error">
          <summary>查看 raw_error</summary>
          <pre>{rawError}</pre>
        </details>
      ) : null}
    </section>
  );
}
