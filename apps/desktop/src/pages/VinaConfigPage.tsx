import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import CommandResultPanel from "../components/CommandResultPanel";
import SectionCard from "../components/SectionCard";
import StatusBadge from "../components/StatusBadge";
import VinaWorkflowBar from "../components/VinaWorkflowBar";
import WarningCallout from "../components/WarningCallout";
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
      setMessage(response.error?.message ?? "运行配置生成失败。");
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
      applyProjectResponse(parseProjectResponse(rawPayload), "配置预览已刷新。");
    } catch (error) {
      setMessage("无法生成配置预览。");
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
      applyProjectResponse(parseProjectResponse(rawPayload), "运行配置已生成。", true);
    } catch (error) {
      setMessage("无法生成运行配置。");
      setConfigText("");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <section className="workbench-page" aria-labelledby="vina-config-title">
      <header className="page-hero">
        <div className="page-hero-main">
          <p className="eyebrow">运行对接</p>
          <h1 id="vina-config-title">生成运行配置</h1>
          <p>根据 PDBQT、Box 和 Vina 参数生成 vina_config.txt。</p>
        </div>
        <div className="page-hero-actions">
          <ActionButton variant="text" onClick={onBack}>返回</ActionButton>
        </div>
      </header>

      <VinaWorkflowBar current="config" />

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
          <span>运行配置</span>
          <strong>{configFile || "尚未生成"}</strong>
          <StatusBadge tone={configFile ? "ok" : "muted"}>{configFile ? "已完成" : "未开始"}</StatusBadge>
        </article>
      </div>

      <SectionCard title="配置预览">
        <pre className="config-preview">{configText || "补全 PDBQT、Box 和 Vina 参数后会显示配置预览。"}</pre>
        <div className="button-row end">
          <ActionButton variant="text" disabled={isBusy} onClick={() => void reloadPreview()}>刷新预览</ActionButton>
          <ActionButton variant="primary" disabled={isBusy} onClick={() => void generateConfig()}>
            {isBusy ? "生成中..." : "生成运行配置"}
          </ActionButton>
        </div>
      </SectionCard>

      <div className="next-step-strip">
        <div>
          <strong>{canOpenRunPrepare ? "下一步：准备对接运行" : "先生成 vina_config.txt"}</strong>
          <p>准备运行会保存运行编号、命令预览和配置快照。</p>
        </div>
        <ActionButton variant="primary" disabled={!canOpenRunPrepare} onClick={() => onOpenRunPrepare(project)}>
          准备对接运行
        </ActionButton>
      </div>

      {warnings.map((warning) => (
        <WarningCallout key={warning} title="配置提示">
          <p>{warning}</p>
        </WarningCallout>
      ))}

      <CommandResultPanel title="配置结果" message={message} rawError={rawError} />
      {configFile ? (
        <AdvancedDetails summary="配置文件路径">
          <code>{configFile}</code>
        </AdvancedDetails>
      ) : null}
    </section>
  );
}
