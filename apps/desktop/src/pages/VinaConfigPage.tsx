import { translate } from "../i18n/translate";
import { useLanguage } from "../i18n/useLanguage";
import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import CommandResultPanel from "../components/CommandResultPanel";
import { BodyGrid, MainPanel, PageHero, PageShell, RightRail, RightRailSection } from "../components/layout/PageLayout";
import SectionCard from "../components/SectionCard";
import StatusBadge from "../components/StatusBadge";
import VinaWorkflowBar from "../components/VinaWorkflowBar";
import WarningCallout from "../components/WarningCallout";
import type { DockStartProject, ProjectResponse, VinaRunMode } from "../types";

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

function projectRunMode(project: DockStartProject): VinaRunMode {
  const value = project.docking_protocol?.run_mode;
  return value === "score_only" || value === "local_only" ? value : "dock";
}

export default function VinaConfigPage({
  project: initialProject,
  onBack,
  onProjectChange,
  onOpenRunPrepare,
}: VinaConfigPageProps) {
  useLanguage();
  const [project, setProject] = useState<DockStartProject>(initialProject);
  const [configText, setConfigText] = useState("");
  const [configFile, setConfigFile] = useState(initialProject.config?.vina_config_file ?? "");
  const [message, setMessage] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [canOpenRunPrepare, setCanOpenRunPrepare] = useState(Boolean(initialProject.config?.vina_config_file));
  const runMode = projectRunMode(project);
  const autobox = runMode !== "dock" && project.docking_protocol?.autobox === true;

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
    <PageShell labelledBy="vina-config-title">
      <PageHero
        eyebrow={translate("运行对接")}
        title={translate(runMode === "score_only" ? "生成姿势评分配置" : runMode === "local_only" ? "生成局部优化配置" : "生成运行配置")}
        titleId="vina-config-title"
        description={translate(runMode === "dock"
          ? "根据 PDBQT、Box 和 Vina 参数生成 vina_config.txt。"
          : `根据 PDBQT、评分函数与 CPU 生成评价配置${autobox ? "；范围由当前配体自动建立" : "；范围使用项目 Box"}。`)}
        actions={
          <>
          <ActionButton variant="text" onClick={onBack}>{translate("返回")}</ActionButton>
          </>
        }
      />

      <BodyGrid>
        <MainPanel>
          <div className="main-panel-content">
            <VinaWorkflowBar current="config" runMode={runMode} />

            <div className="status-strip">
              <article className="metric-card">
                <span>{translate("受体 PDBQT")}</span>
                <strong>{project.receptor.file || translate("未导入")}</strong>
                <StatusBadge tone={project.receptor.file ? "ok" : "warning"}>{project.receptor.file ? translate("已完成") : translate("缺失")}</StatusBadge>
              </article>
              <article className="metric-card">
                <span>{translate("配体 PDBQT")}</span>
                <strong>{project.ligand.file || translate("未导入")}</strong>
                <StatusBadge tone={project.ligand.file ? "ok" : "warning"}>{project.ligand.file ? translate("已完成") : translate("缺失")}</StatusBadge>
              </article>
              <article className="metric-card">
                <span>{translate("运行配置")}</span>
                <strong>{configFile || translate("尚未生成")}</strong>
                <StatusBadge tone={configFile ? "ok" : "muted"}>{translate(configFile ? "已完成" : "未开始")}</StatusBadge>
              </article>
            </div>

            <SectionCard title={translate("配置预览")}>
              <pre className="config-preview">
                {configText || (
                  autobox
                    ? "补全受体、配体 PDBQT 与评价参数后会显示配置预览。"
                    : "补全 PDBQT、Box 和 Vina 参数后会显示配置预览。"
                )}
              </pre>
              <div className="button-row end">
                <ActionButton variant="text" disabled={isBusy} onClick={() => void reloadPreview()}>{translate("刷新预览")}</ActionButton>
                <ActionButton variant="primary" disabled={isBusy} onClick={() => void generateConfig()}>
                  {translate(isBusy ? "生成中..." : "生成运行配置")}
                </ActionButton>
              </div>
            </SectionCard>

            <div className="next-step-strip">
              <div>
                <strong>{translate(canOpenRunPrepare ? "下一步：准备对接运行" : "先生成 vina_config.txt")}</strong>
                <p>{translate("准备运行会保存运行编号、命令预览和配置快照。")}</p>
              </div>
              <ActionButton variant="primary" disabled={!canOpenRunPrepare} onClick={() => onOpenRunPrepare(project)}>{translate("准备对接运行")}</ActionButton>
            </div>

            {translate(warnings.map((warning) => (
              <WarningCallout key={warning} title={translate("配置提示")}>
                <p>{translate(warning)}</p>
              </WarningCallout>
            )))}

            <CommandResultPanel title={translate("配置结果")} message={translate(message)} rawError={rawError} />
            {configFile ? (
              <AdvancedDetails summary={translate("配置文件路径")}>
                <code>{configFile}</code>
              </AdvancedDetails>
            ) : null}
          </div>
        </MainPanel>

        <RightRail>
          <RightRailSection title={translate("配置状态")}>
            <dl className="mode-context-list">
              <div>
                <dt>{translate("受体")}</dt>
                <dd>{project.receptor.file ? translate("已导入") : translate("缺失")}</dd>
              </div>
              <div>
                <dt>{translate("配体")}</dt>
                <dd>{project.ligand.file ? translate("已导入") : translate("缺失")}</dd>
              </div>
              <div>
                <dt>{translate("配置")}</dt>
                <dd>{translate(configFile ? "已生成" : "未生成")}</dd>
              </div>
            </dl>
          </RightRailSection>

          <RightRailSection title={translate("下一步")}>
            <p>{translate(canOpenRunPrepare ? "准备对接运行。" : "先生成 vina_config.txt。")}</p>
          </RightRailSection>
        </RightRail>
      </BodyGrid>
    </PageShell>
  );
}
