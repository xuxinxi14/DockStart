import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { DockStartProject, ProjectResponse } from "../types";

type BoxSetupPageProps = {
  project: DockStartProject;
  onBack: () => void;
  onProjectChange: (project: DockStartProject) => void;
  onOpenViewer: (project: DockStartProject) => void;
  onOpenVinaParams: (project: DockStartProject) => void;
};

type BoxFormState = Record<keyof DockStartProject["box"], string>;

const boxFields: Array<{
  key: keyof DockStartProject["box"];
  label: string;
  help: string;
}> = [
  { key: "center_x", label: "中心 X", help: "对接箱体中心 X 坐标，可为负数。" },
  { key: "center_y", label: "中心 Y", help: "对接箱体中心 Y 坐标，可为负数。" },
  { key: "center_z", label: "中心 Z", help: "对接箱体中心 Z 坐标，可为负数。" },
  { key: "size_x", label: "尺寸 X", help: "对接箱体 X 方向尺寸，必须大于 0。" },
  { key: "size_y", label: "尺寸 Y", help: "对接箱体 Y 方向尺寸，必须大于 0。" },
  { key: "size_z", label: "尺寸 Z", help: "对接箱体 Z 方向尺寸，必须大于 0。" },
];

function parseProjectResponse(rawPayload: string): ProjectResponse {
  const parsed = JSON.parse(rawPayload) as Partial<ProjectResponse>;
  return {
    ok: Boolean(parsed.ok),
    project_dir: parsed.project_dir,
    project: parsed.project ?? null,
    box: parsed.box,
    warnings: parsed.warnings ?? [],
    message: parsed.message,
    error: parsed.error,
  };
}

function boxToForm(project: DockStartProject): BoxFormState {
  return {
    center_x: String(project.box.center_x),
    center_y: String(project.box.center_y),
    center_z: String(project.box.center_z),
    size_x: String(project.box.size_x),
    size_y: String(project.box.size_y),
    size_z: String(project.box.size_z),
  };
}

function hasImportedFiles(project: DockStartProject): boolean {
  return Boolean(project.receptor.file && project.ligand.file);
}

export default function BoxSetupPage({
  project: initialProject,
  onBack,
  onProjectChange,
  onOpenViewer,
  onOpenVinaParams,
}: BoxSetupPageProps) {
  const [project, setProject] = useState<DockStartProject>(initialProject);
  const [boxForm, setBoxForm] = useState<BoxFormState>(() => boxToForm(initialProject));
  const [message, setMessage] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [canOpenVinaParams, setCanOpenVinaParams] = useState(false);

  const applyProjectResponse = useCallback(
    (response: ProjectResponse, fallbackMessage: string, showNextAction = false) => {
      if (response.ok && response.project) {
        setProject(response.project);
        setBoxForm(boxToForm(response.project));
        onProjectChange(response.project);
        setMessage(response.message ?? fallbackMessage);
        setWarnings(response.warnings ?? []);
        setRawError("");
        setCanOpenVinaParams(showNextAction);
        return;
      }
      setMessage(response.error?.message ?? "Box 参数操作失败。");
      setWarnings([]);
      setRawError(response.error?.raw_error ?? "");
      setCanOpenVinaParams(false);
    },
    [onProjectChange],
  );

  const reloadBox = useCallback(async () => {
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>("get_box_params", {
        projectDir: initialProject.project_dir,
      });
      applyProjectResponse(parseProjectResponse(rawPayload), "Box 参数已重新加载。");
    } catch (error) {
      setMessage("前端未能调用 Box 参数读取命令。");
      setWarnings([]);
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  }, [applyProjectResponse, initialProject.project_dir]);

  useEffect(() => {
    void reloadBox();
  }, [reloadBox]);

  const updateField = (key: keyof DockStartProject["box"], value: string) => {
    setCanOpenVinaParams(false);
    setBoxForm((current) => ({
      ...current,
      [key]: value,
    }));
  };

  const saveBox = async () => {
    setIsBusy(true);
    setMessage("");
    setWarnings([]);
    setRawError("");
    try {
      const rawPayload = await invoke<string>("update_box_params", {
        projectDir: project.project_dir,
        boxJson: JSON.stringify(boxForm),
      });
      applyProjectResponse(parseProjectResponse(rawPayload), "Box 参数已保存。", true);
    } catch (error) {
      setMessage("前端未能调用 Box 参数保存命令。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <section className="project-page" aria-labelledby="box-setup-title">
      <button className="text-button" type="button" onClick={onBack}>
        返回 PDBQT 导入页
      </button>

      <div className="page-heading">
        <p className="eyebrow">搜索范围</p>
        <h1 id="box-setup-title">设置对接箱体</h1>
        <p>
          这里只编辑手动 Box 参数，不生成 Vina 配置，也不做 3D 可视化选框。
          单位统一为 Å。
        </p>
      </div>

      <div className="project-summary">
        <span>项目</span>
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
          <p>{project.receptor.file || "运行 Vina 前需要导入受体 PDBQT。"}</p>
        </article>

        <article className="import-card">
          <div className="tool-card-header">
            <h2>配体 ligand.pdbqt</h2>
            <span className={`status-badge ${project.ligand.file ? "status-ok" : "status-missing"}`}>
              {project.ligand.file ? "已导入" : "未导入"}
            </span>
          </div>
          <p>{project.ligand.file || "运行 Vina 前需要导入配体 PDBQT。"}</p>
        </article>
      </div>

      {!hasImportedFiles(project) ? (
        <p className="warning-note">可以先编辑 Box 参数，但运行 Vina 前需要补全受体和配体输入文件。</p>
      ) : null}

      <div className="box-form">
        {boxFields.map((field) => (
          <label className="box-field" key={field.key}>
            <span>{field.label}</span>
            <input
              type="text"
              value={boxForm[field.key]}
              onChange={(event) => updateField(field.key, event.target.value)}
              inputMode="decimal"
            />
            <small>{field.help} 单位：Å</small>
          </label>
        ))}
      </div>

      <div className="toolbar project-toolbar">
        <button className="primary-button" type="button" disabled={isBusy} onClick={() => void saveBox()}>
          {isBusy ? "保存中..." : "保存搜索范围"}
        </button>
        <button className="text-button inline" type="button" disabled={isBusy} onClick={() => void reloadBox()}>
          重新加载项目
        </button>
      </div>

      <div className="toolbar project-toolbar">
        <button className="secondary-button" type="button" onClick={() => onOpenViewer(project)}>
          在 3D 中查看搜索范围
        </button>
      </div>

      {warnings.map((warning) => (
        <p className="warning-note" key={warning}>
          {warning}
        </p>
      ))}

      {canOpenVinaParams ? (
        <div className="ready-note">
          <span>Box 参数已保存，可以进入 Vina 参数设置。</span>
          <button className="secondary-button" type="button" onClick={() => onOpenVinaParams(project)}>
            进入 Vina 参数设置
          </button>
        </div>
      ) : null}

      {message ? <p className="settings-message">{message}</p> : null}
      {rawError ? (
        <details className="raw-error">
          <summary>错误详情</summary>
          <pre>{rawError}</pre>
        </details>
      ) : null}
    </section>
  );
}

