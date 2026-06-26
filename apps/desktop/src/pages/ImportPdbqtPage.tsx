import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import PathInput from "../components/PathInput";
import type { DockStartProject, ProjectFileRef, ProjectResponse } from "../types";

type ImportPdbqtPageProps = {
  project: DockStartProject;
  onBack: () => void;
  onOpenStructureFetch: (project: DockStartProject) => void;
  onOpenBoxSetup: (project: DockStartProject) => void;
  onProjectChange: (project: DockStartProject) => void;
};

function parseProjectResponse(rawPayload: string): ProjectResponse {
  const parsed = JSON.parse(rawPayload) as Partial<ProjectResponse>;
  return {
    ok: Boolean(parsed.ok),
    project_dir: parsed.project_dir,
    project: parsed.project ?? null,
    message: parsed.message,
    error: parsed.error,
  };
}

function fileStatus(fileRef: ProjectFileRef): string {
  return fileRef.file ? `已导入：${fileRef.file}` : "未导入";
}

export default function ImportPdbqtPage({
  project: initialProject,
  onBack,
  onOpenStructureFetch,
  onOpenBoxSetup,
  onProjectChange,
}: ImportPdbqtPageProps) {
  const [project, setProject] = useState<DockStartProject>(initialProject);
  const [receptorPath, setReceptorPath] = useState("");
  const [ligandPath, setLigandPath] = useState("");
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);

  useEffect(() => {
    setProject(initialProject);
  }, [initialProject]);

  const applyProjectResponse = (response: ProjectResponse, fallbackMessage: string) => {
    if (response.ok && response.project) {
      setProject(response.project);
      onProjectChange(response.project);
      setMessage(response.message ?? fallbackMessage);
      setRawError("");
      return;
    }
    setMessage(response.error?.message ?? "项目操作失败。");
    setRawError(response.error?.raw_error ?? "");
  };

  const reloadProject = useCallback(async () => {
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>("load_project", {
        projectDir: project.project_dir,
      });
      applyProjectResponse(parseProjectResponse(rawPayload), "项目已重新加载。");
    } catch (error) {
      setMessage("前端未能调用项目读取命令。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  }, [project.project_dir]);

  const importFile = async (role: "receptor" | "ligand") => {
    setIsBusy(true);
    setMessage("");
    setRawError("");
    try {
      const rawPayload = await invoke<string>(
        role === "receptor" ? "import_receptor_pdbqt" : "import_ligand_pdbqt",
        {
          projectDir: project.project_dir,
          sourcePath: role === "receptor" ? receptorPath : ligandPath,
        },
      );
      applyProjectResponse(
        parseProjectResponse(rawPayload),
        role === "receptor" ? "受体 PDBQT 已导入。" : "配体 PDBQT 已导入。",
      );
    } catch (error) {
      setMessage(role === "receptor" ? "前端未能调用受体导入命令。" : "前端未能调用配体导入命令。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  const readyForBox = Boolean(project.receptor.file && project.ligand.file);

  return (
    <section className="project-page" aria-labelledby="import-pdbqt-title">
      <button className="text-button" type="button" onClick={onBack}>
        返回创建项目
      </button>

      <div className="page-heading">
        <p className="eyebrow">ImportPdbqtPage</p>
        <h1 id="import-pdbqt-title">导入 PDBQT 文件</h1>
        <p>
          第一版只接受已经准备好的 .pdbqt 文件。导入时会复制到项目 prepared
          目录，并更新 project.json。
          如果你还没有 PDBQT，可以先下载 PDB / PubChem 原始结构文件；但 raw 文件不能直接运行 Vina，后续仍需准备成 PDBQT。
        </p>
      </div>

      <div className="project-summary">
        <span>当前项目</span>
        <strong>{project.project_name}</strong>
        <code>{project.project_dir}</code>
      </div>

      <div className="disclaimer-note">
        raw 文件通常是 PDB、CIF 或 SDF；prepared PDBQT 是经过外部工具准备后的 Vina 输入。
        DockStart 当前不会自动把 raw 转成 PDBQT，请在外部完成准备后再导入。
      </div>

      <div className="import-grid">
        <article className="import-card">
          <div className="tool-card-header">
            <h2>受体 receptor.pdbqt</h2>
            <span className={`status-badge ${project.receptor.file ? "status-ok" : "status-missing"}`}>
              {project.receptor.file ? "已导入" : "未导入"}
            </span>
          </div>
          <p>{fileStatus(project.receptor)}</p>
          <PathInput
            value={receptorPath}
            onChange={setReceptorPath}
            mode="file"
            title="选择受体 PDBQT 文件"
            placeholder="输入 receptor.pdbqt 源文件路径"
            ariaLabel="受体 PDBQT 源文件路径"
            filters={[{ name: "PDBQT", extensions: ["pdbqt"] }]}
          />
          <button
            className="secondary-button"
            type="button"
            disabled={isBusy}
            onClick={() => void importFile("receptor")}
          >
            导入受体
          </button>
        </article>

        <article className="import-card">
          <div className="tool-card-header">
            <h2>配体 ligand.pdbqt</h2>
            <span className={`status-badge ${project.ligand.file ? "status-ok" : "status-missing"}`}>
              {project.ligand.file ? "已导入" : "未导入"}
            </span>
          </div>
          <p>{fileStatus(project.ligand)}</p>
          <PathInput
            value={ligandPath}
            onChange={setLigandPath}
            mode="file"
            title="选择配体 PDBQT 文件"
            placeholder="输入 ligand.pdbqt 源文件路径"
            ariaLabel="配体 PDBQT 源文件路径"
            filters={[{ name: "PDBQT", extensions: ["pdbqt"] }]}
          />
          <button
            className="secondary-button"
            type="button"
            disabled={isBusy}
            onClick={() => void importFile("ligand")}
          >
            导入配体
          </button>
        </article>
      </div>

      <div className="toolbar project-toolbar">
        <button className="text-button inline" type="button" disabled={isBusy} onClick={() => void reloadProject()}>
          重新加载项目
        </button>
        <button className="secondary-button" type="button" onClick={() => onOpenStructureFetch(project)}>
          先下载原始结构文件
        </button>
      </div>

      {readyForBox ? (
        <div className="ready-note">
          <span>受体和配体都已导入，可以进入 Box 设置。</span>
          <button className="secondary-button" type="button" onClick={() => onOpenBoxSetup(project)}>
            进入 Box 设置
          </button>
        </div>
      ) : null}

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
