import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import CommandResultPanel from "../components/CommandResultPanel";
import ScientificDisclaimer from "../components/ScientificDisclaimer";
import WarningCallout from "../components/WarningCallout";
import type { DockStartProject, ProjectResponse, ScoreRow } from "../types";

type ResultPageProps = {
  project: DockStartProject;
  runId: string;
  onBack: () => void;
  onProjectChange: (project: DockStartProject) => void;
  onOpenViewer: (project: DockStartProject) => void;
  onOpenReportPage: (project: DockStartProject, runId: string) => void;
};

const runStatusText: Record<string, string> = {
  prepared: "已准备",
  running: "运行中",
  finished: "已完成",
  failed: "运行失败",
  cancelled: "已取消",
  unknown: "状态未知",
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
    log_file: parsed.log_file,
    scores: parsed.scores,
    scores_file: parsed.scores_file,
    project_scores_file: parsed.project_scores_file,
    best_affinity: parsed.best_affinity,
    analyzed_at: parsed.analyzed_at,
    files: parsed.files ?? [],
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

function formatScoreValue(value: number): string {
  return Number.isFinite(value) ? String(value) : "";
}

export default function ResultPage({
  project: initialProject,
  runId,
  onBack,
  onProjectChange,
  onOpenViewer,
  onOpenReportPage,
}: ResultPageProps) {
  const [project, setProject] = useState(initialProject);
  const [metadata, setMetadata] = useState<Record<string, unknown> | null>(null);
  const [scores, setScores] = useState<ScoreRow[]>([]);
  const [logFile, setLogFile] = useState("");
  const [scoresFile, setScoresFile] = useState("");
  const [projectScoresFile, setProjectScoresFile] = useState("");
  const [bestAffinity, setBestAffinity] = useState<number | null>(null);
  const [analyzedAt, setAnalyzedAt] = useState("");
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);

  const status = metadataString(metadata, "status") || "unknown";
  const canAnalyze = status === "finished" && !isBusy;
  const logPath = logFile || metadataString(metadata, "log_file") || `runs/${runId}/log.txt`;
  const displayedScoresFile = scoresFile || metadataString(metadata, "scores_file");
  const displayedProjectScoresFile = projectScoresFile || metadataString(metadata, "project_scores_file");
  const displayedBestAffinity = bestAffinity ?? metadataNumber(metadata, "best_affinity");
  const displayedAnalyzedAt = analyzedAt || metadataString(metadata, "analyzed_at");

  const applyResponse = useCallback(
    (response: ProjectResponse, fallbackMessage: string) => {
      if (response.project) {
        setProject(response.project);
        onProjectChange(response.project);
      }
      if (response.metadata !== undefined) {
        setMetadata(response.metadata ?? null);
      }
      if (response.log_file !== undefined || response.metadata !== undefined) {
        setLogFile(response.log_file ?? metadataString(response.metadata ?? null, "log_file"));
      }
      if (response.scores !== undefined) {
        setScores(response.scores);
      }
      if (response.scores_file !== undefined || response.metadata !== undefined) {
        setScoresFile(response.scores_file ?? metadataString(response.metadata ?? null, "scores_file"));
      }
      if (response.project_scores_file !== undefined || response.metadata !== undefined) {
        setProjectScoresFile(response.project_scores_file ?? metadataString(response.metadata ?? null, "project_scores_file"));
      }
      if (response.best_affinity !== undefined || response.metadata !== undefined) {
        setBestAffinity(response.best_affinity ?? metadataNumber(response.metadata ?? null, "best_affinity"));
      }
      if (response.analyzed_at !== undefined || response.metadata !== undefined) {
        setAnalyzedAt(response.analyzed_at ?? metadataString(response.metadata ?? null, "analyzed_at"));
      }
      setMessage(response.ok ? response.message ?? fallbackMessage : response.error?.message ?? fallbackMessage);
      setRawError(response.ok ? "" : response.error?.raw_error ?? "");
      return response.ok;
    },
    [onProjectChange],
  );

  const reloadRunMetadata = useCallback(async () => {
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>("get_run_files_status", {
        projectDir: initialProject.project_dir,
        runId,
      });
      applyResponse(parseProjectResponse(rawPayload), "run metadata 已重新加载。");
    } catch (error) {
      setMessage("前端未能读取 run metadata。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  }, [applyResponse, initialProject.project_dir, runId]);

  useEffect(() => {
    void reloadRunMetadata();
  }, [reloadRunMetadata]);

  const analyzeResults = async () => {
    setIsBusy(true);
    setMessage("");
    setRawError("");
    try {
      const rawPayload = await invoke<string>("analyze_vina_run_results", {
        projectDir: project.project_dir,
        runId,
      });
      applyResponse(parseProjectResponse(rawPayload), "Vina 结果已解析。");
    } catch (error) {
      setMessage("前端未能调用 Vina 结果解析命令。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  const reloadScores = async () => {
    setIsBusy(true);
    setMessage("");
    setRawError("");
    try {
      const rawPayload = await invoke<string>("load_scores_csv", {
        projectDir: project.project_dir,
        runId,
      });
      applyResponse(parseProjectResponse(rawPayload), "scores.csv 已重新加载。");
    } catch (error) {
      setMessage("前端未能读取 scores.csv。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <section className="project-page" aria-labelledby="result-title">
      <button className="text-button" type="button" onClick={onBack}>
        返回执行页
      </button>

      <div className="page-heading">
        <p className="eyebrow">ResultPage</p>
        <h1 id="result-title">Vina 结果表格</h1>
        <p>
          本页只解析已完成 run 的 log.txt docking score 表格，导出 scores.csv，并显示 mode、affinity 和 RMSD bounds。
          不解析 pose，不生成报告，也不做药效判断。
        </p>
      </div>

      <div className="project-summary">
        <span>当前项目路径</span>
        <strong>{project.project_name}</strong>
        <code>{project.project_dir}</code>
      </div>

      <div className="summary-grid">
        <div className="param-summary">
          <span>run_id</span>
          <strong>{runId}</strong>
        </div>
        <div className="param-summary">
          <span>run 状态</span>
          <strong>{runStatusText[status] ?? status}</strong>
        </div>
        <div className="param-summary">
          <span>log.txt</span>
          <strong>{logPath}</strong>
        </div>
      </div>

      {status !== "finished" ? (
        <WarningCallout title="结果暂不可解析">
          <p>需要先成功运行 Vina，run.status 为 finished 后才能解析结果。</p>
        </WarningCallout>
      ) : null}

      <ScientificDisclaimer kind="score" />

      <div className="toolbar project-toolbar">
        <button className="primary-button" type="button" disabled={!canAnalyze} onClick={() => void analyzeResults()}>
          {isBusy ? "处理中..." : "解析结果"}
        </button>
        <button className="text-button inline" type="button" disabled={isBusy} onClick={() => void reloadScores()}>
          重新加载 scores.csv
        </button>
        <button className="text-button inline" type="button" disabled={isBusy} onClick={() => void reloadRunMetadata()}>
          重新加载 run metadata
        </button>
        <button className="secondary-button" type="button" disabled={status !== "finished"} onClick={() => onOpenViewer(project)}>
          查看 docking pose
        </button>
        {scores.length > 0 || displayedScoresFile ? (
          <button className="secondary-button" type="button" disabled={isBusy} onClick={() => onOpenReportPage(project, runId)}>
            进入报告页
          </button>
        ) : null}
      </div>

      {displayedBestAffinity !== null ? (
        <div className="summary-grid">
          <div className="param-summary">
            <span>best_affinity</span>
            <strong>{displayedBestAffinity} kcal/mol</strong>
          </div>
          <div className="param-summary">
            <span>run scores.csv</span>
            <strong>{displayedScoresFile || "尚未生成"}</strong>
          </div>
          <div className="param-summary">
            <span>project scores.csv</span>
            <strong>{displayedProjectScoresFile || "尚未生成"}</strong>
          </div>
          <div className="param-summary">
            <span>analyzed_at</span>
            <strong>{displayedAnalyzedAt || "尚未分析"}</strong>
          </div>
        </div>
      ) : null}

      {scores.length > 0 ? (
        <div className="scores-table-wrap">
          <table className="scores-table">
            <thead>
              <tr>
                <th>Mode</th>
                <th>Affinity kcal/mol</th>
                <th>RMSD l.b.</th>
                <th>RMSD u.b.</th>
              </tr>
            </thead>
            <tbody>
              {scores.map((score) => (
                <tr key={score.mode}>
                  <td>{score.mode}</td>
                  <td>{formatScoreValue(score.affinity_kcal_mol)}</td>
                  <td>{formatScoreValue(score.rmsd_lb)}</td>
                  <td>{formatScoreValue(score.rmsd_ub)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="placeholder-note">尚未加载 scores.csv。解析成功后这里会显示结果表格。</p>
      )}

      <CommandResultPanel title="结果解析命令结果" message={message} rawError={rawError} />
    </section>
  );
}
