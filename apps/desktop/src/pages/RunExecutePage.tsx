import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import CommandResultPanel from "../components/CommandResultPanel";
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
  prepared: "已准备",
  running: "运行中",
  finished: "已完成",
  failed: "运行失败",
  cancelled: "已取消",
  unknown: "状态未知",
};

const fileStatusText: Record<RunFileStatus["status"], string> = {
  ok: "已生成",
  missing: "未生成",
  empty: "空文件",
  error: "状态错误",
};

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
  const commandPreview = command.length > 0 ? JSON.stringify(command, null, 2) : "命令记录为空或格式无效。";
  const canExecute = status === "prepared" && !isBusy;
  const disabledReason =
    status === "prepared"
      ? ""
      : `当前运行状态为 ${runStatusText[status] ?? status}，只能执行已准备的对接运行。`;

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
      applyResponse(parseProjectResponse(rawPayload), "run 状态已重新加载。");
    } catch (error) {
      setMessage("前端未能读取 run 状态。");
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
      applyResponse(parseProjectResponse(rawPayload), "Vina 执行完成。");
    } catch (error) {
      setMessage("前端未能调用 Vina 执行命令。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <section className="project-page" aria-labelledby="run-execute-title">
      <button className="text-button" type="button" onClick={onBack}>
        返回运行准备页
      </button>

      <div className="page-heading">
        <p className="eyebrow">AutoDock Vina</p>
        <h1 id="run-execute-title">执行 AutoDock Vina</h1>
        <p>
          这一步会真实执行已保存的命令数组，并保存 stdout、stderr、log 和 out 文件。
          这里仅负责运行和记录状态；对接评分表格请在结果页解析。
        </p>
      </div>

      <div className="project-summary">
        <span>项目</span>
        <strong>{project.project_name}</strong>
        <code>{project.project_dir}</code>
      </div>

      <VinaWorkflowBar current="execute" runId={runId} />

      <div className="summary-grid">
        <div className="param-summary">
          <span>运行记录</span>
          <strong>{runId}</strong>
        </div>
        <div className="param-summary">
          <span>当前状态</span>
          <strong>{runStatusText[status] ?? status}</strong>
        </div>
        <div className="param-summary">
          <span>exit_code</span>
          <strong>{exitCode === null ? "尚未产生" : exitCode}</strong>
        </div>
      </div>

      {disabledReason ? (
        <WarningCallout title="暂不能执行">
          <p>{disabledReason}</p>
        </WarningCallout>
      ) : null}

      <div className="config-preview-panel">
        <div className="tool-card-header">
          <h2>技术详情：命令数组预览</h2>
          <span>不会使用 shell 拼接字符串</span>
        </div>
        <pre className="config-preview">{commandPreview}</pre>
      </div>

      <div className="tool-grid run-check-grid">
        {files.map((file) => (
          <article className="tool-card" key={file.key}>
            <div className="tool-card-header">
              <h2>{file.name}</h2>
              <span className={`status-badge status-${file.status === "ok" ? "ok" : file.status === "missing" ? "missing" : "error"}`}>
                {fileStatusText[file.status]}
              </span>
            </div>
            <dl className="tool-meta">
              <div>
                <dt>路径</dt>
                <dd>{file.path || "未设置"}</dd>
              </div>
              <div>
                <dt>大小</dt>
                <dd>{file.exists ? `${file.size} bytes` : "文件不存在"}</dd>
              </div>
              <div>
                <dt>说明</dt>
                <dd>{file.message}</dd>
              </div>
            </dl>
          </article>
        ))}
      </div>

      <div className="toolbar project-toolbar">
        <button className="primary-button" type="button" disabled={!canExecute} onClick={() => void executeRun()}>
          {isBusy ? "执行中..." : "开始对接"}
        </button>
        <button className="text-button inline" type="button" disabled={isBusy} onClick={() => void reloadRun()}>
          重新加载运行状态
        </button>
      </div>

      {status === "finished" ? (
        <div className="ready-note run-result-note">
          <span>Vina 执行完成。</span>
          <div className="run-result-files">
            <code>{metadataString(metadata, "output_file")}</code>
            <code>{metadataString(metadata, "log_file")}</code>
          </div>
          <button className="secondary-button" type="button" onClick={() => onOpenResultPage(project, runId)}>
            查看对接结果
          </button>
        </div>
      ) : null}

      {status === "failed" ? (
        <WarningCallout title="Vina 执行失败">
          <strong>Vina 执行失败。</strong>
          <p>{metadataString(metadata, "error_message") || "请查看 stderr.txt 和 log.txt。"}</p>
          <code>{metadataString(metadata, "stderr_file")}</code>
        </WarningCallout>
      ) : null}

      <p className="placeholder-note">执行页只负责运行和记录状态；解析对接评分请进入结果页。</p>

      <CommandResultPanel title="Vina 执行结果" message={message} rawError={rawError} />
    </section>
  );
}
