import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { DockStartProject, ProjectResponse } from "../types";

type VinaParamPageProps = {
  project: DockStartProject;
  onBack: () => void;
  onProjectChange: (project: DockStartProject) => void;
  onOpenVinaConfig: (project: DockStartProject) => void;
};

type VinaFormState = Record<keyof DockStartProject["vina"], string>;

const vinaFields: Array<{
  key: keyof DockStartProject["vina"];
  label: string;
  help: string;
  inputMode: "numeric" | "decimal";
}> = [
  {
    key: "exhaustiveness",
    label: "exhaustiveness",
    help: "搜索彻底程度，越高越慢，新手建议 8。",
    inputMode: "numeric",
  },
  {
    key: "num_modes",
    label: "num_modes",
    help: "输出构象数量，新手建议 9。",
    inputMode: "numeric",
  },
  {
    key: "energy_range",
    label: "energy_range",
    help: "保留能量范围，单位 kcal/mol，新手建议 3 或 4。",
    inputMode: "decimal",
  },
  {
    key: "cpu",
    label: "cpu",
    help: "使用 CPU 核心数，0 表示自动。",
    inputMode: "numeric",
  },
  {
    key: "seed",
    label: "seed",
    help: "随机种子，留空表示随机；填写整数可提高复现性。",
    inputMode: "numeric",
  },
];

function parseProjectResponse(rawPayload: string): ProjectResponse {
  const parsed = JSON.parse(rawPayload) as Partial<ProjectResponse>;
  return {
    ok: Boolean(parsed.ok),
    project_dir: parsed.project_dir,
    project: parsed.project ?? null,
    vina: parsed.vina,
    warnings: parsed.warnings ?? [],
    message: parsed.message,
    error: parsed.error,
  };
}

function vinaToForm(project: DockStartProject): VinaFormState {
  return {
    exhaustiveness: String(project.vina.exhaustiveness),
    num_modes: String(project.vina.num_modes),
    energy_range: String(project.vina.energy_range),
    cpu: String(project.vina.cpu),
    seed: project.vina.seed === null ? "" : String(project.vina.seed),
  };
}

function hasImportedFiles(project: DockStartProject): boolean {
  return Boolean(project.receptor.file && project.ligand.file);
}

function isDefaultBox(project: DockStartProject): boolean {
  const box = project.box;
  return (
    box.center_x === 0 &&
    box.center_y === 0 &&
    box.center_z === 0 &&
    box.size_x === 20 &&
    box.size_y === 20 &&
    box.size_z === 20
  );
}

export default function VinaParamPage({
  project: initialProject,
  onBack,
  onProjectChange,
  onOpenVinaConfig,
}: VinaParamPageProps) {
  const [project, setProject] = useState<DockStartProject>(initialProject);
  const [vinaForm, setVinaForm] = useState<VinaFormState>(() => vinaToForm(initialProject));
  const [message, setMessage] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [canOpenConfig, setCanOpenConfig] = useState(false);

  const applyProjectResponse = useCallback(
    (response: ProjectResponse, fallbackMessage: string, showNextAction = false) => {
      if (response.ok && response.project) {
        setProject(response.project);
        setVinaForm(vinaToForm(response.project));
        onProjectChange(response.project);
        setMessage(response.message ?? fallbackMessage);
        setWarnings(response.warnings ?? []);
        setRawError("");
        setCanOpenConfig(showNextAction);
        return;
      }
      setMessage(response.error?.message ?? "Vina 参数操作失败。");
      setWarnings([]);
      setRawError(response.error?.raw_error ?? "");
      setCanOpenConfig(false);
    },
    [onProjectChange],
  );

  const reloadVina = useCallback(async () => {
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>("get_vina_params", {
        projectDir: initialProject.project_dir,
      });
      applyProjectResponse(parseProjectResponse(rawPayload), "Vina 参数已重新加载。");
    } catch (error) {
      setMessage("前端未能调用 Vina 参数读取命令。");
      setWarnings([]);
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  }, [applyProjectResponse, initialProject.project_dir]);

  useEffect(() => {
    void reloadVina();
  }, [reloadVina]);

  const updateField = (key: keyof DockStartProject["vina"], value: string) => {
    setCanOpenConfig(false);
    setVinaForm((current) => ({
      ...current,
      [key]: value,
    }));
  };

  const saveVina = async () => {
    setIsBusy(true);
    setMessage("");
    setWarnings([]);
    setRawError("");
    try {
      const rawPayload = await invoke<string>("update_vina_params", {
        projectDir: project.project_dir,
        vinaJson: JSON.stringify(vinaForm),
      });
      applyProjectResponse(parseProjectResponse(rawPayload), "Vina 参数已保存。", true);
    } catch (error) {
      setMessage("前端未能调用 Vina 参数保存命令。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <section className="project-page" aria-labelledby="vina-param-title">
      <button className="text-button" type="button" onClick={onBack}>
        返回 Box 设置
      </button>

      <div className="page-heading">
        <p className="eyebrow">VinaParamPage</p>
        <h1 id="vina-param-title">设置 Vina 参数</h1>
        <p>
          这里只保存运行参数到 project.json，不生成 vina_config.txt，也不调用 AutoDock Vina。
          参数会在后续生成配置和运行步骤中复用。
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
        <p className="warning-note">可以先编辑 Vina 参数，但运行 Vina 前需要补全受体和配体输入文件。</p>
      ) : null}

      {isDefaultBox(project) ? (
        <p className="warning-note">当前 Box 参数仍为默认值，请确认它覆盖了合理结合区域；这里不会阻止保存 Vina 参数。</p>
      ) : null}

      <div className="param-summary">
        <span>当前 Box 摘要</span>
        <strong>
          中心：{project.box.center_x}, {project.box.center_y}, {project.box.center_z} Å
        </strong>
        <strong>
          尺寸：{project.box.size_x}, {project.box.size_y}, {project.box.size_z} Å
        </strong>
      </div>

      <div className="param-form">
        {vinaFields.map((field) => (
          <label className="param-field" key={field.key}>
            <span>{field.label}</span>
            <input
              type="text"
              value={vinaForm[field.key]}
              onChange={(event) => updateField(field.key, event.target.value)}
              inputMode={field.inputMode}
            />
            <small>{field.help}</small>
          </label>
        ))}
      </div>

      <div className="toolbar project-toolbar">
        <button className="primary-button" type="button" disabled={isBusy} onClick={() => void saveVina()}>
          {isBusy ? "保存中..." : "保存 Vina 参数"}
        </button>
        <button className="text-button inline" type="button" disabled={isBusy} onClick={() => void reloadVina()}>
          重新加载项目
        </button>
      </div>

      {warnings.map((warning) => (
        <p className="warning-note" key={warning}>
          {warning}
        </p>
      ))}

      {canOpenConfig ? (
        <div className="ready-note">
          <span>Vina 参数已保存，可以进入配置文件生成。</span>
          <button className="secondary-button" type="button" onClick={() => onOpenVinaConfig(project)}>
            进入配置文件生成
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
