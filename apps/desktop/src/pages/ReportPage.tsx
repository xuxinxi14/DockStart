import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import CommandResultPanel from "../components/CommandResultPanel";
import ReportStatusCard from "../components/ReportStatusCard";
import ScientificDisclaimer from "../components/ScientificDisclaimer";
import SectionCard from "../components/SectionCard";
import StatusBadge from "../components/StatusBadge";
import VinaWorkflowBar from "../components/VinaWorkflowBar";
import WarningCallout from "../components/WarningCallout";
import type { DockStartProject, ProjectResponse, RunFileStatus } from "../types";

type ReportPageProps = {
  project: DockStartProject;
  runId: string;
  onBack: () => void;
  onProjectChange: (project: DockStartProject) => void;
};

const fileStatusText: Record<RunFileStatus["status"], string> = {
  ok: "已完成",
  missing: "缺失",
  empty: "需检查",
  error: "失败",
};

function parseProjectResponse(rawPayload: string): ProjectResponse {
  const parsed = JSON.parse(rawPayload) as Partial<ProjectResponse>;
  return {
    ok: Boolean(parsed.ok),
    project_dir: parsed.project_dir,
    project: parsed.project ?? null,
    run_id: parsed.run_id,
    metadata: parsed.metadata,
    report_file: parsed.report_file,
    project_report_file: parsed.project_report_file,
    reported_at: parsed.reported_at,
    report_status: parsed.report_status,
    scores_status: parsed.scores_status,
    can_export: parsed.can_export,
    files: parsed.files ?? [],
    message: parsed.message,
    error: parsed.error,
  };
}

function metadataString(metadata: Record<string, unknown> | null, key: string): string {
  const value = metadata?.[key];
  return typeof value === "string" ? value : "";
}

export default function ReportPage({ project: initialProject, runId, onBack, onProjectChange }: ReportPageProps) {
  const [project, setProject] = useState(initialProject);
  const [metadata, setMetadata] = useState<Record<string, unknown> | null>(null);
  const [files, setFiles] = useState<RunFileStatus[]>([]);
  const [reportFile, setReportFile] = useState("");
  const [projectReportFile, setProjectReportFile] = useState("");
  const [reportedAt, setReportedAt] = useState("");
  const [reportStatus, setReportStatus] = useState("missing");
  const [canExport, setCanExport] = useState(false);
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);

  const scoresStatus = useMemo(() => files.find((file) => file.key === "scores"), [files]);
  const runReportStatus = useMemo(() => files.find((file) => file.key === "run_report"), [files]);
  const projectReportStatus = useMemo(() => files.find((file) => file.key === "project_report"), [files]);
  const displayedReportFile = reportFile || metadataString(metadata, "report_file") || `runs/${runId}/docking_report.md`;
  const displayedProjectReportFile =
    projectReportFile || metadataString(metadata, "project_report_file") || "reports/docking_report.md";
  const displayedReportedAt = reportedAt || metadataString(metadata, "reported_at");
  const hasScores = scoresStatus?.status === "ok";

  const applyResponse = useCallback(
    (response: ProjectResponse, fallbackMessage: string) => {
      if (response.project) {
        setProject(response.project);
        onProjectChange(response.project);
      }
      if (response.metadata !== undefined) setMetadata(response.metadata ?? null);
      setFiles(response.files ?? []);
      setReportFile(response.report_file ?? metadataString(response.metadata ?? null, "report_file"));
      setProjectReportFile(response.project_report_file ?? metadataString(response.metadata ?? null, "project_report_file"));
      setReportedAt(response.reported_at ?? metadataString(response.metadata ?? null, "reported_at"));
      setReportStatus(response.report_status ?? "missing");
      setCanExport(Boolean(response.can_export));
      setMessage(response.ok ? response.message ?? fallbackMessage : response.error?.message ?? fallbackMessage);
      setRawError(response.ok ? "" : response.error?.raw_error ?? "");
      return response.ok;
    },
    [onProjectChange],
  );

  const reloadReportStatus = useCallback(async () => {
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>("get_report_status", {
        projectDir: initialProject.project_dir,
        runId,
      });
      applyResponse(parseProjectResponse(rawPayload), "报告状态已刷新。");
    } catch (error) {
      setMessage("无法读取报告状态。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  }, [applyResponse, initialProject.project_dir, runId]);

  useEffect(() => {
    void reloadReportStatus();
  }, [reloadReportStatus]);

  const exportReport = async () => {
    setIsBusy(true);
    setMessage("");
    setRawError("");
    try {
      const rawPayload = await invoke<string>("export_markdown_report", {
        projectDir: project.project_dir,
        runId,
      });
      applyResponse(parseProjectResponse(rawPayload), "Markdown 实验记录已导出。");
    } catch (error) {
      setMessage("无法导出 Markdown 实验记录。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <section className="workbench-page" aria-labelledby="report-title">
      <header className="page-hero">
        <div className="page-hero-main">
          <p className="eyebrow">结果与报告</p>
          <h1 id="report-title">导出实验记录</h1>
          <p>导出 Markdown 记录，便于复现和排查。</p>
        </div>
        <div className="page-hero-actions">
          <ActionButton variant="text" onClick={onBack}>返回</ActionButton>
          <ActionButton onClick={() => void reloadReportStatus()} disabled={isBusy}>刷新状态</ActionButton>
        </div>
      </header>

      <VinaWorkflowBar current="report" runId={runId} />

      <div className="status-strip">
        <article className="metric-card">
          <span>运行记录</span>
          <strong>{runId}</strong>
        </article>
        <article className="metric-card">
          <span>scores.csv</span>
          <strong>{scoresStatus ? fileStatusText[scoresStatus.status] : "未检查"}</strong>
          <StatusBadge tone={hasScores ? "ok" : "warning"}>{hasScores ? "已完成" : "缺失"}</StatusBadge>
        </article>
        <article className="metric-card">
          <span>报告</span>
          <strong>{reportStatus === "exported" ? "已导出" : "未导出"}</strong>
          <StatusBadge tone={reportStatus === "exported" ? "ok" : "muted"}>
            {reportStatus === "exported" ? "已完成" : "未开始"}
          </StatusBadge>
        </article>
      </div>

      {!hasScores ? (
        <WarningCallout title="报告暂不可导出">
          <p>请先回到结果页解析 scores.csv。</p>
        </WarningCallout>
      ) : null}

      <SectionCard title="导出">
        <ReportStatusCard status={reportStatus} path={displayedProjectReportFile} />
        <div className="button-row">
          <ActionButton variant="primary" disabled={isBusy || !canExport} onClick={() => void exportReport()}>
            {isBusy ? "处理中..." : "导出 Markdown 实验记录"}
          </ActionButton>
        </div>
      </SectionCard>

      {(reportStatus === "exported" || displayedReportedAt) ? (
        <div className="next-step-strip">
          <div>
            <strong>实验记录已导出</strong>
            <p>{displayedProjectReportFile}</p>
          </div>
        </div>
      ) : null}

      <AdvancedDetails>
        <dl className="meta-list">
          {[scoresStatus, runReportStatus, projectReportStatus].filter(Boolean).map((file) => (
            <div key={file!.key}>
              <dt>{file!.name}</dt>
              <dd><code>{file!.path}</code> · {fileStatusText[file!.status]}</dd>
            </div>
          ))}
          <div>
            <dt>运行内报告</dt>
            <dd><code>{displayedReportFile}</code></dd>
          </div>
          <div>
            <dt>导出时间</dt>
            <dd>{displayedReportedAt || "未记录"}</dd>
          </div>
        </dl>
      </AdvancedDetails>

      <ScientificDisclaimer kind="score" />
      <CommandResultPanel title="报告导出" message={message} rawError={rawError} />
    </section>
  );
}
