import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import PathInput from "../components/PathInput";
import type { DockStartProject, ProjectResponse, SettingsResponse } from "../types";

type ProjectCreatePageProps = {
  onBack: () => void;
  onCreated: (project: DockStartProject, nextPage: "structure-fetch" | "import-pdbqt") => void;
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

function parseSettingsResponse(rawPayload: string): SettingsResponse {
  const parsed = JSON.parse(rawPayload) as Partial<SettingsResponse>;
  return {
    ok: Boolean(parsed.ok),
    settings_path: parsed.settings_path ?? "",
    settings: parsed.settings ?? null,
    error: parsed.error,
  };
}

export default function ProjectCreatePage({ onBack, onCreated }: ProjectCreatePageProps) {
  const [projectName, setProjectName] = useState("demo_project");
  const [baseDir, setBaseDir] = useState("");
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [createdProject, setCreatedProject] = useState<DockStartProject | null>(null);

  useEffect(() => {
    async function loadDefaultProjectDir() {
      try {
        const rawPayload = await invoke<string>("get_settings");
        const response = parseSettingsResponse(rawPayload);
        if (response.ok && response.settings?.project.default_project_dir) {
          setBaseDir(response.settings.project.default_project_dir);
        }
      } catch {
        // Settings are helpful but not required for creating a project.
      }
    }

    void loadDefaultProjectDir();
  }, []);

  const createProject = useCallback(async () => {
    setIsBusy(true);
    setMessage("");
    setRawError("");
    setCreatedProject(null);
    try {
      const rawPayload = await invoke<string>("create_project", {
        projectName,
        baseDir,
      });
      const response = parseProjectResponse(rawPayload);
      if (response.ok && response.project) {
        setMessage(response.message ?? "项目创建成功。");
        setCreatedProject(response.project);
        return;
      }
      setMessage(response.error?.message ?? "项目创建失败。");
      setRawError(response.error?.raw_error ?? "");
    } catch (error) {
      setMessage("前端未能调用项目创建命令。请确认当前运行环境是 Tauri 桌面端。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  }, [baseDir, projectName]);

  return (
    <section className="project-page" aria-labelledby="project-create-title">
      <button className="text-button" type="button" onClick={onBack}>
        返回
      </button>

      <div className="page-heading">
        <p className="eyebrow">ProjectCreatePage</p>
        <h1 id="project-create-title">创建 DockStart 项目</h1>
        <p>
          这一步只创建项目文件夹和 project.json。后续导入的 receptor.pdbqt 和
          ligand.pdbqt 会复制到项目的 prepared 目录中。
        </p>
      </div>

      <div className="disclaimer-note">
        创建项目后有两条入口：可以先下载 raw 原始结构，也可以直接导入已经准备好的 PDBQT。
        raw 文件不能直接运行 Vina，prepared PDBQT 才是当前运行流程的输入。
      </div>

      <div className="form-panel">
        <label htmlFor="project-name">项目名称</label>
        <input
          id="project-name"
          type="text"
          value={projectName}
          onChange={(event) => setProjectName(event.target.value)}
          placeholder="例如 demo_project"
        />

        <label htmlFor="base-dir">项目保存目录</label>
        <PathInput
          id="base-dir"
          value={baseDir}
          onChange={setBaseDir}
          mode="directory"
          title="选择项目保存目录"
          placeholder="输入项目的父目录；会在其中创建项目名文件夹"
        />

        <div className="form-actions">
          <button className="primary-button" type="button" disabled={isBusy} onClick={createProject}>
            {isBusy ? "创建中..." : "创建项目"}
          </button>
        </div>
      </div>

      {message ? <p className="settings-message">{message}</p> : null}
      {createdProject ? (
        <div className="ready-note">
          <span>
            项目已创建。若还没有 PDBQT，可以先下载 raw 原始结构；若已经准备好 PDBQT，可以直接导入 prepared 文件。
          </span>
          <button
            className="secondary-button"
            type="button"
            onClick={() => onCreated(createdProject, "structure-fetch")}
          >
            下载原始结构文件
          </button>
          <button
            className="secondary-button"
            type="button"
            onClick={() => onCreated(createdProject, "import-pdbqt")}
          >
            直接导入 PDBQT
          </button>
        </div>
      ) : null}
      {rawError ? (
        <details className="raw-error">
          <summary>查看 raw_error</summary>
          <pre>{rawError}</pre>
        </details>
      ) : null}
    </section>
  );
}
