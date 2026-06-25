import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { DockStartProject, ProjectResponse } from "../types";

type VinaConfigPageProps = {
  project: DockStartProject;
  onBack: () => void;
  onProjectChange: (project: DockStartProject) => void;
  onOpenRunPrepare: (project: DockStartProject) => void;
};

function parseProjectResponse(rawPayload: string): ProjectResponse {
  const parsed = JSON.parse(rawPayload) as Partial<ProjectResponse>;
  return {
    ok: Boolean(parsed.ok),
    project_dir: parsed.project_dir,
    project: parsed.project ?? null,
    config_file: parsed.config_file,
    config_text: parsed.config_text,
    warnings: parsed.warnings ?? [],
    message: parsed.message,
    error: parsed.error,
  };
}

export default function VinaConfigPage({
  project: initialProject,
  onBack,
  onProjectChange,
  onOpenRunPrepare,
}: VinaConfigPageProps) {
  const [project, setProject] = useState<DockStartProject>(initialProject);
  const [configText, setConfigText] = useState("");
  const [configFile, setConfigFile] = useState(initialProject.config?.vina_config_file ?? "");
  const [message, setMessage] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [canOpenRunPrepare, setCanOpenRunPrepare] = useState(Boolean(initialProject.config?.vina_config_file));

  const applyProjectResponse = useCallback(
    (response: ProjectResponse, fallbackMessage: string, showRunPrepare = false) => {
      if (response.ok && response.project) {
        setProject(response.project);
        onProjectChange(response.project);
        setConfigText(response.config_text ?? "");
        setConfigFile(response.config_file ?? response.project.config.vina_config_file ?? "");
        setMessage(response.message ?? fallbackMessage);
        setWarnings(response.warnings ?? []);
        setRawError("");
        setCanOpenRunPrepare(showRunPrepare || Boolean(response.project.config.vina_config_file));
        return;
      }
      setMessage(response.error?.message ?? "Vina 配置操作失败。");
      setConfigText("");
      setWarnings([]);
      setRawError(response.error?.raw_error ?? "");
      setCanOpenRunPrepare(false);
    },
    [onProjectChange],
  );

  const reloadPreview = useCallback(async () => {
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>("get_vina_config_preview", {
        projectDir: initialProject.project_dir,
      });
      applyProjectResponse(parseProjectResponse(rawPayload), "Vina 配置预览已重新生成。");
    } catch (error) {
      setMessage("前端未能调用 Vina 配置预览命令。");
      setConfigText("");
      setWarnings([]);
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  }, [applyProjectResponse, initialProject.project_dir]);

  useEffect(() => {
    void reloadPreview();
  }, [reloadPreview]);

  const generateConfig = async () => {
    setIsBusy(true);
    setMessage("");
    setWarnings([]);
    setRawError("");
    try {
      const rawPayload = await invoke<string>("generate_vina_config", {
        projectDir: project.project_dir,
      });
      applyProjectResponse(parseProjectResponse(rawPayload), "vina_config.txt 已生成。", true);
    } catch (error) {
      setMessage("前端未能调用 Vina 配置生成命令。");
      setConfigText("");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <section className="project-page" aria-labelledby="vina-config-title">
      <button className="text-button" type="button" onClick={onBack}>
        返回 Vina 参数设置
      </button>

      <div className="page-heading">
        <p className="eyebrow">VinaConfigPage</p>
        <h1 id="vina-config-title">生成 Vina 配置文件</h1>
        <p>
          根据项目内的 prepared PDBQT、Box 参数和 Vina 参数生成 configs/vina_config.txt。
          这里只生成配置文件，不运行 AutoDock Vina。
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
            <h2>受体 receptor.pdbqt</h2>
            <span className={`status-badge ${project.receptor.file ? "status-ok" : "status-missing"}`}>
              {project.receptor.file ? "已导入" : "未导入"}
            </span>
          </div>
          <p>{project.receptor.file || "生成配置前需要导入受体 PDBQT。"}</p>
        </article>

        <article className="import-card">
          <div className="tool-card-header">
            <h2>配体 ligand.pdbqt</h2>
            <span className={`status-badge ${project.ligand.file ? "status-ok" : "status-missing"}`}>
              {project.ligand.file ? "已导入" : "未导入"}
            </span>
          </div>
          <p>{project.ligand.file || "生成配置前需要导入配体 PDBQT。"}</p>
        </article>
      </div>

      <div className="summary-grid">
        <div className="param-summary">
          <span>Box 参数摘要</span>
          <strong>
            中心：{project.box.center_x}, {project.box.center_y}, {project.box.center_z} Å
          </strong>
          <strong>
            尺寸：{project.box.size_x}, {project.box.size_y}, {project.box.size_z} Å
          </strong>
        </div>
        <div className="param-summary">
          <span>Vina 参数摘要</span>
          <strong>exhaustiveness：{project.vina.exhaustiveness}</strong>
          <strong>num_modes：{project.vina.num_modes}</strong>
          <strong>energy_range：{project.vina.energy_range} kcal/mol</strong>
          <strong>cpu：{project.vina.cpu}</strong>
          <strong>seed：{project.vina.seed ?? "随机"}</strong>
        </div>
      </div>

      <div className="config-preview-panel">
        <div className="tool-card-header">
          <h2>配置预览</h2>
          <span>{configFile || "尚未生成文件"}</span>
        </div>
        <pre className="config-preview">{configText || "暂无可预览内容。请先补全 PDBQT、Box 和 Vina 参数。"}</pre>
      </div>

      <div className="toolbar project-toolbar">
        <button className="text-button inline" type="button" disabled={isBusy} onClick={() => void reloadPreview()}>
          重新生成预览
        </button>
        <button className="primary-button" type="button" disabled={isBusy} onClick={() => void generateConfig()}>
          {isBusy ? "生成中..." : "生成 vina_config.txt"}
        </button>
      </div>

      <p className="placeholder-note">下一步先进入运行前检查和 run 记录准备，然后可以执行 prepared run。</p>

      {canOpenRunPrepare ? (
        <div className="ready-note">
          <span>vina_config.txt 已生成，可以进入运行前检查。</span>
          <button className="secondary-button" type="button" onClick={() => onOpenRunPrepare(project)}>
            进入运行前检查
          </button>
        </div>
      ) : null}

      {configFile ? <p className="settings-message">配置文件路径：{configFile}</p> : null}
      {warnings.map((warning) => (
        <p className="warning-note" key={warning}>
          {warning}
        </p>
      ))}
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
