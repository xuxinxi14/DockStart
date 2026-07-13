import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import CommandResultPanel from "../components/CommandResultPanel";
import { BodyGrid, MainPanel, PageHero, PageShell, RightRail, RightRailSection } from "../components/layout/PageLayout";
import SectionCard from "../components/SectionCard";
import StatusBadge from "../components/StatusBadge";
import VinaWorkflowBar from "../components/VinaWorkflowBar";
import WarningCallout from "../components/WarningCallout";
import type { DockStartProject, ProjectResponse, RunFileStatus, RunRuntimeStatusResponse } from "../types";
import {
  cancelQueuedBackgroundTask,
  findActiveBackgroundTask,
  startVinaRunTask,
  waitForBackgroundTask,
  type BackgroundTaskStatus,
} from "../utils/backgroundTasks";

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
  cancelled: "已取消",
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
  if (status === "failed") return "error";
  if (status === "cancelled") return "warning";
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
  const [activeTask, setActiveTask] = useState<BackgroundTaskStatus | null>(null);
  const activeTaskAbortRef = useRef<AbortController | null>(null);

  useEffect(() => () => activeTaskAbortRef.current?.abort(), []);

  const status = metadataString(metadata, "status") || "unknown";
  const exitCode = metadataNumber(metadata, "exit_code");
  const command = useMemo(() => metadataCommand(metadata), [metadata]);
  const commandPreview = command.length > 0 ? JSON.stringify(command, null, 2) : "命令记录为空。";
  const taskIsActive = activeTask?.status === "queued" || activeTask?.status === "running";
  const runIsActive = taskIsActive || status === "running";
  const canExecute = status === "prepared" && !isBusy && !runIsActive;
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
      const response = parseProjectResponse(rawPayload);
      applyResponse(response, "运行状态已刷新。");
      if (metadataString(response.metadata ?? null, "status") === "running") {
        const runtimePayload = await invoke<string>("get_run_runtime_status", {
          projectDir: initialProject.project_dir,
          runId,
        });
        const runtime = JSON.parse(runtimePayload) as RunRuntimeStatusResponse;
        if (runtime.project) {
          setProject(runtime.project);
          onProjectChange(runtime.project);
        }
        if (runtime.metadata) setMetadata(runtime.metadata);
        setMessage(runtime.message || "已重新核验未完成运行的进程状态。");
        setRawError(runtime.error?.raw_error || runtime.error?.suggestion || "");
      }
    } catch (error) {
      setMessage("无法读取运行状态。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  }, [applyResponse, initialProject.project_dir, onProjectChange, runId]);

  const waitForVinaTask = useCallback(
    async (started: BackgroundTaskStatus, controller: AbortController) => {
      setActiveTask(started);
      const completed = await waitForBackgroundTask(
        started.task_id,
        (task) => {
          setActiveTask(task);
          setMessage(task.progress.message || task.message);
          setMetadata((current) => {
            const currentStage = metadataString(current, "stage");
            const observedStage = task.stage === "cancel_pending" ? "cancelling" : task.stage;
            const nextStage = (currentStage === "cancelling" && (observedStage === "starting" || observedStage === "running"))
              ? currentStage
              : observedStage;
            return {
              ...(current ?? {}),
              status: task.status === "queued" ? "prepared" : task.status,
              stage: nextStage,
            };
          });
          if (task.error) setRawError(task.error);
        },
        controller.signal,
      );
      setActiveTask(completed);
      return completed;
    },
    [],
  );

  useEffect(() => {
    void reloadRun();
  }, [reloadRun]);

  useEffect(() => {
    const controller = new AbortController();
    let disposed = false;
    let resumedTaskId = "";
    const reconnect = async () => {
      try {
        const existing = await findActiveBackgroundTask(initialProject.project_dir, { runId, kind: "vina" });
        if (!existing || disposed) return;
        resumedTaskId = existing.task_id;
        activeTaskAbortRef.current?.abort();
        activeTaskAbortRef.current = controller;
        setIsBusy(true);
        setMessage("检测到该 run 仍在后台执行，正在恢复进度显示。");
        const completed = await waitForVinaTask(existing, controller);
        if (disposed) return;
        if (completed.result_json) {
          applyResponse(parseProjectResponse(completed.result_json), "对接运行已结束。");
        } else {
          await reloadRun();
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        if (!disposed) {
          setMessage("无法恢复后台 Vina 任务状态。");
          setRawError(error instanceof Error ? error.message : String(error));
        }
      } finally {
        if (!disposed) {
          setActiveTask((current) => (current?.task_id === resumedTaskId ? null : current));
          setIsBusy(false);
        }
        if (activeTaskAbortRef.current === controller) activeTaskAbortRef.current = null;
      }
    };
    void reconnect();
    return () => {
      disposed = true;
      controller.abort();
    };
  }, [applyResponse, initialProject.project_dir, reloadRun, runId, waitForVinaTask]);

  const executeRun = async () => {
    setIsBusy(true);
    setMessage("");
    setRawError("");
    activeTaskAbortRef.current?.abort();
    const controller = new AbortController();
    activeTaskAbortRef.current = controller;
    try {
      const started = await startVinaRunTask(project.project_dir, runId);
      setMessage(started.deduplicated ? "该 run 已在后台执行，正在接收进度。" : "Vina 任务已进入后台队列。");
      const completed = await waitForVinaTask(started, controller);
      if (completed.status === "cancelled") {
        setMessage("Vina 任务已取消，已有日志仍保留在运行目录中。");
        await reloadRun();
        return;
      }
      if (!completed.result_json) {
        throw new Error(completed.error || completed.message || "Vina 后台任务没有返回执行结果。");
      }
      applyResponse(parseProjectResponse(completed.result_json), "对接运行已完成。");
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setMessage("无法执行 AutoDock Vina。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      if (activeTaskAbortRef.current === controller) activeTaskAbortRef.current = null;
      setActiveTask((current) => (current?.task_id ? null : current));
      setIsBusy(false);
    }
  };

  const cancelRun = async () => {
    if (!runIsActive) return;
    try {
      if (activeTask?.status === "queued") {
        const cancelled = await cancelQueuedBackgroundTask(activeTask.task_id);
        setActiveTask(cancelled);
        if (cancelled.status === "cancelled") {
          setMessage("Vina 任务尚未启动，已从后台队列取消。");
          return;
        }
      }
      const payload = await invoke<string>("cancel_vina_run", {
        projectDir: project.project_dir,
        runId,
      });
      const parsed = JSON.parse(payload) as Partial<ProjectResponse>;
      if (!parsed.ok) {
        setMessage(parsed.error?.message || "取消请求失败，后台运行仍会继续。");
        setRawError(parsed.error?.raw_error || parsed.error?.suggestion || "");
        return;
      }
      if (parsed.project) {
        setProject(parsed.project);
        onProjectChange(parsed.project);
      }
      if (parsed.metadata) setMetadata(parsed.metadata);
      setMessage(parsed.message || "取消请求已发送，正在等待 Vina 安全退出。");
    } catch (error) {
      setRawError(error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <PageShell labelledBy="run-execute-title">
      <PageHero
        eyebrow="运行对接"
        title="执行 AutoDock Vina"
        titleId="run-execute-title"
        description="运行已准备的命令，保存 stdout、stderr、log 和 out 文件。"
        actions={
          <>
          <ActionButton variant="text" onClick={onBack}>返回</ActionButton>
          <ActionButton onClick={() => void reloadRun()} disabled={isBusy}>刷新状态</ActionButton>
          </>
        }
      />

      <BodyGrid>
        <MainPanel>
          <div className="main-panel-content">
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

            {status === "running" && !taskIsActive ? (
              <WarningCallout title="已恢复未完成运行">
                <p>该 run 的磁盘记录仍为 running，但当前窗口没有原后台任务内存。你可以刷新状态、等待外部 Vina 结束，或使用下方按钮安全取消；DockStart 不会并发启动第二个 run。</p>
              </WarningCallout>
            ) : null}

            <SectionCard title="执行">
              <div className="button-row">
                <ActionButton variant="primary" disabled={!canExecute} onClick={() => void executeRun()}>
                  {isBusy ? "执行中..." : "开始对接"}
                </ActionButton>
                {runIsActive ? (
                  <ActionButton variant="secondary" onClick={() => void cancelRun()}>
                    {activeTask?.status === "queued" ? "取消排队" : "终止运行"}
                  </ActionButton>
                ) : null}
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
          </div>
        </MainPanel>

        <RightRail>
          <RightRailSection title="运行状态">
            <dl className="mode-context-list">
              <div>
                <dt>run</dt>
                <dd>{runId}</dd>
              </div>
              <div>
                <dt>状态</dt>
                <dd>{runStatusText[status] ?? status}</dd>
              </div>
              <div>
                <dt>exit code</dt>
                <dd>{exitCode === null ? "未产生" : exitCode}</dd>
              </div>
            </dl>
          </RightRailSection>

          <RightRailSection title="下一步">
            <p>{status === "finished" ? "解析结果并生成 scores.csv。" : "运行完成后进入结果页。"}</p>
          </RightRailSection>
        </RightRail>
      </BodyGrid>
    </PageShell>
  );
}
