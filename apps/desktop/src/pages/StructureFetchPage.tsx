import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import CommandResultPanel from "../components/CommandResultPanel";
import WarningCallout from "../components/WarningCallout";
import type { DockStartProject, ProjectResponse, RawStructureStatus, RunFileStatus } from "../types";

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
  const [pubchemQueryType, setPubchemQueryType] = useState<"cid" | "name" | "smiles">("cid");
  const [pubchemQuery, setPubchemQuery] = useState("");
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
      applyProjectResponse(parseProjectResponse(rawPayload), "原始结构文件状态已重新读取。");
    } catch (error) {
      setMessage("前端未能调用原始结构文件状态命令。");
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
        query: pubchemQuery,
        queryType: pubchemQueryType,
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
        ? `确定清除${label}原始结构记录，并删除项目 raw/ 目录内对应文件吗？已有 Vina 输入文件不会被删除。`
        : `确定清除${label}原始结构记录吗？原始结构文件和 Vina 输入文件都会保留。`,
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
        <p className="eyebrow">原始结构</p>
        <h1 id="structure-fetch-title">获取原始结构文件</h1>
        <p>
          原始结构文件来自结构数据库，不能直接运行 Vina。本页只获取和管理原始结构，不会自动生成 PDBQT。运行 Vina 仍需
          prepared/receptor.pdbqt 和 prepared/ligand.pdbqt。
        </p>
      </div>

      <div className="project-summary">
        <span>项目</span>
        <strong>{project.project_name}</strong>
        <code>{project.project_dir}</code>
      </div>

      <WarningCallout title="raw 不等于 Vina 输入">
        <p>
          原始结构文件是从 RCSB/PubChem 下载的结构来源；Vina 输入文件才是 AutoDock Vina 可以读取的 PDBQT。
          下载 raw 后仍需要进入“准备 PDBQT”或手动导入 prepared/receptor.pdbqt 与 prepared/ligand.pdbqt。
        </p>
      </WarningCallout>

      <div className="import-grid raw-fetch-grid">
        <article className="import-card">
          <div className="tool-card-header">
            <h2>受体</h2>
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
          {overwritePdb ? <div className="warning-note inline-note">已开启覆盖：重新下载会替换同名原始结构文件。</div> : null}
          <button className="secondary-button" type="button" disabled={isBusy} onClick={() => void fetchPdb()}>
            下载 RCSB PDB
          </button>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={deleteReceptorRawFile}
              onChange={(event) => setDeleteReceptorRawFile(event.target.checked)}
            />
            清除记录时同时删除原始结构文件
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
            <h2>配体</h2>
            <span className={`status-badge ${statusClass(ligandStatus?.status)}`}>
              {statusText(ligandStatus?.status)}
            </span>
          </div>
          {renderRawStatus(ligandStatus, project.ligand.raw_file)}
          <label htmlFor="pubchem-query-type">PubChem 查询类型</label>
          <select
            id="pubchem-query-type"
            value={pubchemQueryType}
            onChange={(event) => setPubchemQueryType(event.target.value as "cid" | "name" | "smiles")}
          >
            <option value="cid">CID</option>
            <option value="name">名称</option>
            <option value="smiles">SMILES（暂未支持）</option>
          </select>
          <label htmlFor="pubchem-query">
            {pubchemQueryType === "cid" ? "PubChem CID" : pubchemQueryType === "name" ? "PubChem 名称" : "SMILES"}
          </label>
          <input
            id="pubchem-query"
            type="text"
            value={pubchemQuery}
            onChange={(event) => setPubchemQuery(event.target.value)}
            placeholder={pubchemQueryType === "cid" ? "例如 2244" : pubchemQueryType === "name" ? "例如 aspirin" : "例如 CCO"}
          />
          <p className="placeholder-note">
            {pubchemQueryType === "smiles"
              ? "SMILES 查询当前只返回暂未支持提示；不会调用 RDKit，也不会生成 3D 或 PDBQT。"
              : "当前会下载 PubChem SDF 原始文件；不会生成 3D 或转 PDBQT。"}
          </p>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={overwritePubchem}
              onChange={(event) => setOverwritePubchem(event.target.checked)}
            />
            覆盖已有 raw 配体文件
          </label>
          {overwritePubchem ? <div className="warning-note inline-note">已开启覆盖：重新下载会替换同名原始结构文件。</div> : null}
          <button className="secondary-button" type="button" disabled={isBusy} onClick={() => void fetchPubchem()}>
            下载 PubChem SDF
          </button>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={deleteLigandRawFile}
              onChange={(event) => setDeleteLigandRawFile(event.target.checked)}
            />
            清除记录时同时删除原始结构文件
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
          导入 Vina 输入
        </button>
        <button className="secondary-button" type="button" onClick={() => onOpenPreparation(project)}>
          准备 Vina 输入
        </button>
      </div>

      <div className="ready-note">
        <span>下载原始结构文件后，下一步仍是准备 Vina 输入文件，并在导入页选择 prepared/receptor.pdbqt 与 prepared/ligand.pdbqt。</span>
        <button className="secondary-button" type="button" onClick={() => onOpenImportPdbqt(project)}>
          去导入 Vina 输入文件
        </button>
        <button className="secondary-button" type="button" onClick={() => onOpenPreparation(project)}>
          查看自动准备入口
        </button>
      </div>

      <WarningCallout title="下一步：准备 PDBQT">
        <p>
          下载页本身只保存原始结构文件，不会直接准备 Vina 输入。后续可到“准备 Vina 输入”使用已检测到的 RDKit/Meeko
          尝试准备 PDBQT；DockStart 当前仍不接入 Open Babel、PLIP 或 MGLTools。raw 记录可以清除，但
          prepared/receptor.pdbqt 和 prepared/ligand.pdbqt 不会因此删除。
        </p>
      </WarningCallout>

      <CommandResultPanel title="结构获取结果" message={message} rawError={rawError} />
    </section>
  );
}
