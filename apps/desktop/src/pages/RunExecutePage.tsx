import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import CommandResultPanel from "../components/CommandResultPanel";
import SectionCard from "../components/SectionCard";
import StatusBadge from "../components/StatusBadge";
import VinaWorkflowBar from "../components/VinaWorkflowBar";
import WarningCallout from "../components/WarningCallout";
import type { DockStartProject, ProjectResponse, RunFileStatus } from "../types";

type RunExecutePageProps = {
  project: DockStartProject;
  runId: string;
  onBack: () => void;
  onProjectChange: (project: DockStartProject) => void;
  onOpenResultPage: (project: DockStartProject, runId: string) => void;
};

const runStatusText: Record<string, string> = {
  prepared: "可进行",
  running: "进行中",
  finished: "已完成",
  failed: "失败",
  cancelled: "失败",
  unknown: "需检查",
};

const fileStatusText: Record<RunFileStatus["status"], string> = {
  ok: "已完成",
  missing: "缺失",
  empty: "需检查",
  error: "失败",
};

function toneForRun(status: string): "ok" | "warning" | "error" | "muted" | "info" {
  if (status === "finished" || status === "prepared") return "ok";
  if (status === "running") return "info";
  if (status === "failed" || status === "cancelled") return "error";
  return "muted";
}

function toneForFile(status: RunFileStatus["status"]): "ok" | "warning" | "error" | "muted" {
  if (status === "ok") return "ok";
  if (status === "error") return "error";
  if (status === "empty") return "warning";
  return "muted";
}

function parseProjectResponse(rawPayload: string): ProjectResponse {
  const parsed = JSON.parse(rawPayload) as Partial<ProjectResponse>;
  return {
    ok: Boolean(parsed.ok),
    project_dir: parsed.project_dir,
    project: parsed.project ?? null,
    run_id: parsed.run_id,
    metadata: parsed.metadata,
    metadata_file: parsed.metadata_file,
    stdout_file: parsed.stdout_file,
    stderr_file: parsed.stderr_file,
    output_file: parsed.output_file,
    log_file: parsed.log_file,
    files: parsed.files ?? [],
    command: parsed.command,
    command_preview: parsed.command_preview,
    warnings: parsed.warnings ?? [],
    message: parsed.message,
    error: parsed.error,
  };
}

function metadataString(metadata: Record<string, unknown> | null, key: string): string {
  const value = metadata?.[key];
  return typeof value === "string" ? value : "";
}

function metadataNumber(metadata: Record<string, unknown> | null, key: string): number | null {
  const value = metadata?.[key];
  return typeof value === "number" ? value : null;
}

function metadataCommand(metadata: Record<string, unknown> | null): string[] {
  const value = metadata?.command;
  return Array.isArray(value) ? value.map(String) : [];
}

export default function RunExecutePage({
  project: initialProject,
  runId,
  onBack,
  onProjectChange,
  onOpenResultPage,
}: RunExecutePageProps) {
  const [project, setProject] = useState(initialProject);
  const [metadata, setMetadata] = useState<Record<string, unknown> | null>(null);
  const [files, setFiles] = useState<RunFileStatus[]>([]);
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);

  const status = metadataString(metadata, "status") || "unknown";
  const exitCode = metadataNumber(metadata, "exit_code");
  const command = useMemo(() => metadataCommand(metadata), [metadata]);
  const commandPreview = command.length > 0 ? JSON.stringify(command, null, 2) : "命令记录为空。";
  const canExecute = status === "prepared" && !isBusy;
  const disabledReason = status === "prepared" ? "" : `当前状态为 ${runStatusText[status] ?? status}。`;

  const applyResponse = useCallback(
    (response: ProjectResponse, fallbackMessage: string) => {
      if (response.project) {
        setProject(response.project);
        onProjectChange(response.project);
      }
      setMetadata(response.metadata ?? null);
      setFiles(response.files ?? []);
      setMessage(response.message ?? response.error?.message ?? fallbackMessage);
      setRawError(response.error?.raw_error ?? "");
      return response.ok;
    },
    [onProjectChange],
  );

  const reloadRun = useCallback(async () => {
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>("get_run_files_status", {
        projectDir: initialProject.project_dir,
        runId,
      });
      applyResponse(parseProjectResponse(rawPayload), "运行状态已刷新。");
    } catch (error) {
      setMessage("无法读取运行状态。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  }, [applyResponse, initialProject.project_dir, runId]);

  useEffect(() => {
    void reloadRun();
  }, [reloadRun]);

  const executeRun = async () => {
    setIsBusy(true);
    setMessage("");
    setRawError("");
    try {
      const rawPayload = await invoke<string>("execute_prepared_vina_run", {
        projectDir: project.project_dir,
        runId,
      });
      applyResponse(parseProjectResponse(rawPayload), "对接运行已完成。");
    } catch (error) {
      setMessage("无法执行 AutoDock Vina。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <section className="workbench-page" aria-labelledby="run-execute-title">
      <header className="page-hero">
        <div className="page-hero-main">
          <p className="eyebrow">运行对接</p>
          <h1 id="run-execute-title">执行 AutoDock Vina</h1>
          <p>运行已准备的命令，保存 stdout、stderr、log 和 out 文件。</p>
        </div>
        <div className="page-hero-actions">
          <ActionButton variant="text" onClick={onBack}>返回</ActionButton>
          <ActionButton onClick={() => void reloadRun()} disabled={isBusy}>刷新状态</ActionButton>
        </div>
      </header>

      <VinaWorkflowBar current="execute" runId={runId} />

      <div className="status-strip">
        <article className="metric-card">
          <span>运行记录</span>
          <strong>{runId}</strong>
        </article>
        <article className="metric-card">
          <span>当前状态</span>
          <strong>{runStatusText[status] ?? status}</strong>
          <StatusBadge tone={toneForRun(status)}>{runStatusText[status] ?? "需检查"}</StatusBadge>
        </article>
        <article className="metric-card">
          <span>exit code</span>
          <strong>{exitCode === null ? "尚未产生" : exitCode}</strong>
        </article>
      </div>

      {disabledReason ? (
        <WarningCallout title="暂不能执行">
          <p>{disabledReason}</p>
        </WarningCallout>
      ) : null}

      <SectionCard title="执行">
        <div className="button-row">
          <ActionButton variant="primary" disabled={!canExecute} onClick={() => void executeRun()}>
            {isBusy ? "执行中..." : "开始对接"}
          </ActionButton>
        </div>
        <AdvancedDetails summary="命令与运行文件">
          <pre>{commandPreview}</pre>
          <div className="compact-grid">
            {files.map((file) => (
              <article className="file-card" key={file.key}>
                <span>{file.name}</span>
                <strong>{file.path || "未设置"}</strong>
                <StatusBadge tone={toneForFile(file.status)}>{fileStatusText[file.status]}</StatusBadge>
              </article>
            ))}
          </div>
        </AdvancedDetails>
      </SectionCard>

      {status === "finished" ? (
        <div className="next-step-strip">
          <div>
            <strong>下一步：解析结果</strong>
            <p>{metadataString(metadata, "log_file") || "log.txt 已记录。"}</p>
          </div>
          <ActionButton variant="primary" onClick={() => onOpenResultPage(project, runId)}>
            查看对接结果
          </ActionButton>
        </div>
      ) : null}

      {status === "failed" ? (
        <WarningCallout title="Vina 执行失败">
          <p>{metadataString(metadata, "error_message") || "请查看 stderr 和 log。"}</p>
        </WarningCallout>
      ) : null}

      <CommandResultPanel title="执行结果" message={message} rawError={rawError} />
    </section>
  );
}
