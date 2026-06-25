import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { DockStartProject, ProjectResponse, RunFileStatus } from "../types";

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
    raw_file: parsed.raw_file,
    source: parsed.source,
    source_id: parsed.source_id,
    format: parsed.format,
    url: parsed.url,
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

function findStatus(files: RunFileStatus[], key: string): RunFileStatus | null {
  return files.find((file) => file.key === key) ?? null;
}

export default function StructureFetchPage({
  project: initialProject,
  onBack,
  onOpenImportPdbqt,
  onProjectChange,
}: StructureFetchPageProps) {
  const [project, setProject] = useState<DockStartProject>(initialProject);
  const [files, setFiles] = useState<RunFileStatus[]>([]);
  const [pdbId, setPdbId] = useState("");
  const [pdbFormat, setPdbFormat] = useState("pdb");
  const [overwritePdb, setOverwritePdb] = useState(false);
  const [pubchemCid, setPubchemCid] = useState("");
  const [overwritePubchem, setOverwritePubchem] = useState(false);
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
        if (response.files) {
          setFiles(response.files);
        }
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

  const receptorStatus = findStatus(files, "receptor_raw");
  const ligandStatus = findStatus(files, "ligand_raw");

  return (
    <section className="project-page" aria-labelledby="structure-fetch-title">
      <button className="text-button" type="button" onClick={onBack}>
        返回创建项目
      </button>

      <div className="page-heading">
        <p className="eyebrow">StructureFetchPage</p>
        <h1 id="structure-fetch-title">下载原始结构文件</h1>
        <p>
          当前页面只下载原始结构文件，不会自动生成 PDBQT。运行 Vina 仍需
          prepared/receptor.pdbqt 和 prepared/ligand.pdbqt。
        </p>
      </div>

      <div className="project-summary">
        <span>当前项目</span>
        <strong>{project.project_name}</strong>
        <code>{project.project_dir}</code>
      </div>

      <div className="import-grid">
        <article className="import-card">
          <div className="tool-card-header">
            <h2>受体 raw 文件</h2>
            <span className={`status-badge ${statusClass(receptorStatus?.status)}`}>
              {statusText(receptorStatus?.status)}
            </span>
          </div>
          <p>{receptorStatus?.path || project.receptor.raw_file || "尚未下载受体原始结构。"}</p>
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
          <button className="secondary-button" type="button" disabled={isBusy} onClick={() => void fetchPdb()}>
            下载 RCSB PDB
          </button>
        </article>

        <article className="import-card">
          <div className="tool-card-header">
            <h2>配体 raw 文件</h2>
            <span className={`status-badge ${statusClass(ligandStatus?.status)}`}>
              {statusText(ligandStatus?.status)}
            </span>
          </div>
          <p>{ligandStatus?.path || project.ligand.raw_file || "尚未下载配体原始结构。"}</p>
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
          <button className="secondary-button" type="button" disabled={isBusy} onClick={() => void fetchPubchem()}>
            下载 PubChem SDF
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
        raw 文件只记录来源和原始下载结果。DockStart 当前不会调用 RDKit、Meeko、Open Babel、PLIP、MGLTools，也不会自动准备 docking 输入。
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
