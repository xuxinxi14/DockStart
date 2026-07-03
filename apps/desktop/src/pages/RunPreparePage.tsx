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
import type { DockStartProject, ProjectResponse, RunCheckResult, ToolStatus } from "../types";

type RunPreparePageProps = {
  project: DockStartProject;
  onBack: () => void;
  onProjectChange: (project: DockStartProject) => void;
  onOpenRunExecute: (project: DockStartProject, runId: string) => void;
};

const statusText: Record<ToolStatus, string> = {
  ok: "已完成",
  missing: "缺失",
  error: "失败",
  unknown: "需检查",
};

function statusTone(status: ToolStatus): "ok" | "warning" | "error" | "muted" {
  if (status === "ok") return "ok";
  if (status === "error") return "error";
  if (status === "missing") return "warning";
  return "muted";
}

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
      setMessage("无法完成运行前检查。");
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
      const ok = applyResponse(response, "对接运行记录已创建。");
      setPrepared(ok ? response : null);
    } catch (error) {
      setMessage("无法创建对接运行记录。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  const summaryChecks = [
    checkByKey(checks, "receptor"),
    checkByKey(checks, "ligand"),
    checkByKey(checks, "vina_config"),
    checkByKey(checks, "vina"),
    checkByKey(checks, "box"),
    checkByKey(checks, "vina_params"),
  ].filter(Boolean) as RunCheckResult[];

  return (
    <PageShell labelledBy="run-prepare-title">
      <PageHero
        eyebrow="运行对接"
        title="准备对接运行"
        titleId="run-prepare-title"
        description="创建 run 记录，保存配置快照和命令预览。"
        actions={
          <>
          <ActionButton variant="text" onClick={onBack}>返回</ActionButton>
          </>
        }
      />

      <BodyGrid>
        <MainPanel>
          <div className="main-panel-content">
            <VinaWorkflowBar current="prepare" runId={nextRunId || prepared?.run_id} />

            <SectionCard title="运行前状态">
              <div className="status-strip">
                {summaryChecks.length > 0 ? (
                  summaryChecks.map((check) => (
                    <article className="metric-card" key={check.key}>
                      <span>{check.name}</span>
                      <strong>{check.message || check.path || check.version || "已检查"}</strong>
                      <StatusBadge tone={statusTone(check.status)}>{statusText[check.status]}</StatusBadge>
                    </article>
                  ))
                ) : (
                  <p className="message-line">尚未获得检查结果。</p>
                )}
              </div>
              <AdvancedDetails summary="命令预览">
                <pre>{commandPreview || "运行前检查通过后会显示命令数组预览。"}</pre>
              </AdvancedDetails>
              <div className="button-row end">
                <ActionButton variant="text" disabled={isBusy} onClick={() => void reloadChecks()}>重新检查</ActionButton>
                <ActionButton variant="primary" disabled={isBusy} onClick={() => void prepareRun()}>
                  {isBusy ? "准备中..." : "创建运行记录"}
                </ActionButton>
              </div>
            </SectionCard>

            {prepared?.ok && prepared.project && prepared.run_id ? (
              <div className="next-step-strip">
                <div>
                  <strong>下一步：开始对接</strong>
                  <p>运行记录 {prepared.run_id} 已创建。</p>
                </div>
                <ActionButton variant="primary" onClick={() => onOpenRunExecute(prepared.project!, prepared.run_id!)}>
                  开始对接
                </ActionButton>
              </div>
            ) : null}

            {warnings.map((warning) => (
              <WarningCallout key={warning} title="运行前提示">
                <p>{warning}</p>
              </WarningCallout>
            ))}
            <CommandResultPanel title="运行准备结果" message={message} rawError={rawError} />
          </div>
        </MainPanel>

        <RightRail>
          <RightRailSection title="当前运行">
            <dl className="mode-context-list">
              <div>
                <dt>下一 run</dt>
                <dd>{nextRunId || prepared?.run_id || "待创建"}</dd>
              </div>
              <div>
                <dt>检查项</dt>
                <dd>{summaryChecks.length || 0}</dd>
              </div>
            </dl>
          </RightRailSection>

          <RightRailSection title="下一步">
            <p>{prepared?.ok ? "开始执行 AutoDock Vina。" : "通过检查后创建运行记录。"}</p>
          </RightRailSection>
        </RightRail>
      </BodyGrid>
    </PageShell>
  );
}
