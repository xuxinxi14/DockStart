import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import CommandResultPanel from "../components/CommandResultPanel";
import VinaWorkflowBar from "../components/VinaWorkflowBar";
import WarningCallout from "../components/WarningCallout";
import type { DockStartProject, ProjectResponse, RunCheckResult, ToolStatus } from "../types";

type RunPreparePageProps = {
  project: DockStartProject;
  onBack: () => void;
  onProjectChange: (project: DockStartProject) => void;
  onOpenRunExecute: (project: DockStartProject, runId: string) => void;
};

const statusText: Record<ToolStatus, string> = {
  ok: "已通过",
  missing: "缺失",
  error: "检查错误",
  unknown: "状态未知",
};

function parseProjectResponse(rawPayload: string): ProjectResponse {
  const parsed = JSON.parse(rawPayload) as Partial<ProjectResponse>;
  return {
    ok: Boolean(parsed.ok),
    project_dir: parsed.project_dir,
    project: parsed.project ?? null,
    checks: parsed.checks ?? [],
    next_run_id: parsed.next_run_id,
    run_id: parsed.run_id,
    metadata: parsed.metadata,
    metadata_file: parsed.metadata_file,
    command_preview_file: parsed.command_preview_file,
    config_snapshot_file: parsed.config_snapshot_file,
    command: parsed.command,
    command_preview: parsed.command_preview,
    warnings: parsed.warnings ?? [],
    message: parsed.message,
    error: parsed.error,
  };
}

function checkByKey(checks: RunCheckResult[], key: string): RunCheckResult | undefined {
  return checks.find((check) => check.key === key);
}

function fileStatus(project: DockStartProject, key: "receptor" | "ligand"): string {
  return project[key].file || "尚未导入";
}

export default function RunPreparePage({
  project: initialProject,
  onBack,
  onProjectChange,
  onOpenRunExecute,
}: RunPreparePageProps) {
  const [project, setProject] = useState<DockStartProject>(initialProject);
  const [checks, setChecks] = useState<RunCheckResult[]>([]);
  const [commandPreview, setCommandPreview] = useState("");
  const [nextRunId, setNextRunId] = useState("");
  const [message, setMessage] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [rawError, setRawError] = useState("");
  const [prepared, setPrepared] = useState<ProjectResponse | null>(null);
  const [isBusy, setIsBusy] = useState(false);

  const applyResponse = useCallback(
    (response: ProjectResponse, fallbackMessage: string) => {
      if (response.project) {
        setProject(response.project);
        onProjectChange(response.project);
      }
      setChecks(response.checks ?? []);
      setCommandPreview(response.command_preview ?? "");
      setNextRunId(response.next_run_id ?? response.run_id ?? "");
      setWarnings(response.warnings ?? []);
      setMessage(response.ok ? response.message ?? fallbackMessage : response.error?.message ?? fallbackMessage);
      setRawError(response.ok ? "" : response.error?.raw_error ?? "");
      return response.ok;
    },
    [onProjectChange],
  );

  const reloadChecks = useCallback(async () => {
    setIsBusy(true);
    setPrepared(null);
    try {
      const rawPayload = await invoke<string>("validate_run_prerequisites", {
        projectDir: initialProject.project_dir,
      });
      applyResponse(parseProjectResponse(rawPayload), "运行前检查已完成。");
    } catch (error) {
      setChecks([]);
      setCommandPreview("");
      setNextRunId("");
      setWarnings([]);
      setMessage("前端未能调用运行前检查命令。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  }, [applyResponse, initialProject.project_dir]);

  useEffect(() => {
    void reloadChecks();
  }, [reloadChecks]);

  const prepareRun = async () => {
    setIsBusy(true);
    setPrepared(null);
    try {
      const rawPayload = await invoke<string>("prepare_vina_run", {
        projectDir: project.project_dir,
      });
      const response = parseProjectResponse(rawPayload);
      const ok = applyResponse(response, "运行记录已准备完成。");
      setPrepared(ok ? response : null);
    } catch (error) {
      setMessage("前端未能调用准备运行记录命令。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  const receptorCheck = checkByKey(checks, "receptor");
  const ligandCheck = checkByKey(checks, "ligand");
  const configCheck = checkByKey(checks, "vina_config");
  const vinaCheck = checkByKey(checks, "vina");
  const boxCheck = checkByKey(checks, "box");
  const vinaParamsCheck = checkByKey(checks, "vina_params");
  const summaryChecks = [receptorCheck, ligandCheck, configCheck, vinaCheck, boxCheck, vinaParamsCheck].filter(
    Boolean,
  ) as RunCheckResult[];

  return (
    <section className="project-page" aria-labelledby="run-prepare-title">
      <button className="text-button" type="button" onClick={onBack}>
        返回配置文件页面
      </button>

      <div className="page-heading">
        <p className="eyebrow">RunPreparePage</p>
        <h1 id="run-prepare-title">运行前检查与运行记录准备</h1>
        <p>
          本页面只准备运行记录，不执行 AutoDock Vina。这里会检查输入文件、配置文件、参数和 Vina
          可用性，然后生成 run 目录中的 metadata、命令预览和配置快照；下一页再执行 prepared run。
        </p>
      </div>

      <div className="project-summary">
        <span>当前项目</span>
        <strong>{project.project_name}</strong>
        <code>{project.project_dir}</code>
      </div>

      <VinaWorkflowBar current="prepare" runId={nextRunId || prepared?.run_id} />

      <div className="summary-grid">
        <div className="param-summary">
          <span>受体 receptor.pdbqt</span>
          <strong>{fileStatus(project, "receptor")}</strong>
        </div>
        <div className="param-summary">
          <span>配体 ligand.pdbqt</span>
          <strong>{fileStatus(project, "ligand")}</strong>
        </div>
        <div className="param-summary">
          <span>配置文件</span>
          <strong>{project.config.vina_config_file || "尚未生成 vina_config.txt"}</strong>
        </div>
      </div>

      <div className="tool-grid run-check-grid">
        {summaryChecks.length > 0 ? (
          summaryChecks.map((check) => (
            <article className="tool-card" key={check.key}>
              <div className="tool-card-header">
                <h2>{check.name}</h2>
                <span className={`status-badge status-${check.status}`}>{statusText[check.status]}</span>
              </div>
              <dl className="tool-meta">
                <div>
                  <dt>说明</dt>
                  <dd>{check.message || "暂无说明"}</dd>
                </div>
                <div>
                  <dt>路径 / 版本</dt>
                  <dd>{check.path || check.version || "未提供"}</dd>
                </div>
              </dl>
              {check.raw_error ? (
                <details className="raw-error">
                  <summary>查看 raw_error</summary>
                  <pre>{check.raw_error}</pre>
                </details>
              ) : null}
            </article>
          ))
        ) : (
          <p className="placeholder-note">尚未获得检查结果，请点击“重新检查”。</p>
        )}
      </div>

      <div className="config-preview-panel">
        <div className="tool-card-header">
          <h2>命令预览</h2>
          <span>{nextRunId ? `下一条运行记录：${nextRunId}` : "尚未生成 run_id"}</span>
        </div>
        <pre className="config-preview">
          {commandPreview || "运行前检查通过后，这里会显示将来执行 Vina 时使用的命令数组预览。"}
        </pre>
      </div>

      <p className="placeholder-note">准备运行记录时不会创建 out.pdbqt 或 log.txt；执行页会真实运行 Vina，但仍不解析 docking 结果。</p>

      <div className="toolbar project-toolbar">
        <button className="text-button inline" type="button" disabled={isBusy} onClick={() => void reloadChecks()}>
          重新检查
        </button>
        <button className="primary-button" type="button" disabled={isBusy} onClick={() => void prepareRun()}>
          {isBusy ? "准备中..." : "准备运行记录"}
        </button>
      </div>

      {prepared?.ok ? (
        <div className="ready-note run-result-note">
          <span>运行记录已准备：{prepared.run_id}</span>
          <div className="run-result-files">
            <code>{prepared.metadata_file}</code>
            <code>{prepared.command_preview_file}</code>
            <code>{prepared.config_snapshot_file}</code>
          </div>
          {prepared.project && prepared.run_id ? (
            <button
              className="secondary-button"
              type="button"
              onClick={() => onOpenRunExecute(prepared.project!, prepared.run_id!)}
            >
              进入 Vina 执行页
            </button>
          ) : null}
        </div>
      ) : null}

      {warnings.map((warning) => (
        <WarningCallout key={warning} title="运行前检查提示">
          <p>{warning}</p>
        </WarningCallout>
      ))}
      <CommandResultPanel title="运行准备命令结果" message={message} rawError={rawError} />
    </section>
  );
}
