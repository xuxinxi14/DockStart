import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import SectionCard from "../components/SectionCard";
import StatusBadge from "../components/StatusBadge";
import WarningCallout from "../components/WarningCallout";
import type { DockStartProject, ProjectResponse } from "../types";

type VinaParamPageProps = {
  project: DockStartProject;
  onBack: () => void;
  onProjectChange: (project: DockStartProject) => void;
  onOpenVinaConfig: (project: DockStartProject) => void;
};

type VinaFormState = Record<keyof DockStartProject["vina"], string>;

const vinaFields: Array<{ key: keyof DockStartProject["vina"]; label: string; hint: string; inputMode: "numeric" | "decimal" }> = [
  { key: "exhaustiveness", label: "搜索彻底程度", hint: "建议 8", inputMode: "numeric" },
  { key: "num_modes", label: "输出构象数量", hint: "建议 9", inputMode: "numeric" },
  { key: "energy_range", label: "能量范围", hint: "kcal/mol", inputMode: "decimal" },
  { key: "cpu", label: "CPU 核心数", hint: "0 表示自动", inputMode: "numeric" },
  { key: "seed", label: "随机种子", hint: "可留空", inputMode: "numeric" },
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

function hasPreparedFiles(project: DockStartProject): boolean {
  return Boolean(project.receptor.file && project.ligand.file);
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
      setMessage(response.error?.message ?? "Vina 参数保存失败。");
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
      applyProjectResponse(parseProjectResponse(rawPayload), "Vina 参数已刷新。");
    } catch (error) {
      setMessage("无法读取 Vina 参数。");
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
    setVinaForm((current) => ({ ...current, [key]: value }));
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
      setMessage("无法保存 Vina 参数。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <section className="workbench-page" aria-labelledby="vina-param-title">
      <header className="page-hero">
        <div className="page-hero-main">
          <p className="eyebrow">运行对接</p>
          <h1 id="vina-param-title">设置 Vina 参数</h1>
          <p>保存本次对接使用的 Vina 参数。</p>
        </div>
        <div className="page-hero-actions">
          <ActionButton variant="text" onClick={onBack}>返回</ActionButton>
        </div>
      </header>

      <div className="status-strip">
        <article className="metric-card">
          <span>受体 PDBQT</span>
          <strong>{project.receptor.file || "未导入"}</strong>
          <StatusBadge tone={project.receptor.file ? "ok" : "warning"}>{project.receptor.file ? "已完成" : "缺失"}</StatusBadge>
        </article>
        <article className="metric-card">
          <span>配体 PDBQT</span>
          <strong>{project.ligand.file || "未导入"}</strong>
          <StatusBadge tone={project.ligand.file ? "ok" : "warning"}>{project.ligand.file ? "已完成" : "缺失"}</StatusBadge>
        </article>
        <article className="metric-card">
          <span>Box</span>
          <strong>
            {project.box.center_x}, {project.box.center_y}, {project.box.center_z} / {project.box.size_x}, {project.box.size_y}, {project.box.size_z}
          </strong>
          <StatusBadge tone="ok">已记录</StatusBadge>
        </article>
      </div>

      {!hasPreparedFiles(project) ? (
        <WarningCallout title="输入文件缺失">
          <p>可以先保存参数，但生成配置前需要补全受体和配体 PDBQT。</p>
        </WarningCallout>
      ) : null}

      <SectionCard title="Vina 参数">
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
              <small>{field.hint}</small>
            </label>
          ))}
        </div>
        <div className="button-row end">
          <ActionButton variant="text" disabled={isBusy} onClick={() => void reloadVina()}>重新加载</ActionButton>
          <ActionButton variant="primary" disabled={isBusy} onClick={() => void saveVina()}>
            {isBusy ? "保存中..." : "保存 Vina 参数"}
          </ActionButton>
        </div>
      </SectionCard>

      <div className="next-step-strip">
        <div>
          <strong>{canOpenConfig ? "下一步：生成运行配置" : "保存参数后生成配置"}</strong>
          <p>生成配置只写入 vina_config.txt，不执行对接。</p>
        </div>
        <ActionButton variant="primary" disabled={!canOpenConfig} onClick={() => onOpenVinaConfig(project)}>
          生成运行配置
        </ActionButton>
      </div>

      {warnings.map((warning) => (
        <WarningCallout key={warning} title="参数提示">
          <p>{warning}</p>
        </WarningCallout>
      ))}

      {message ? <p className="message-line">{message}</p> : null}
      {rawError ? (
        <AdvancedDetails>
          <pre>{rawError}</pre>
        </AdvancedDetails>
      ) : null}
    </section>
  );
}
