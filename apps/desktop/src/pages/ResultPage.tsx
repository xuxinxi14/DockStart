import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import CommandResultPanel from "../components/CommandResultPanel";
import { BodyGrid, MainPanel, PageHero, PageShell, RightRail, RightRailSection } from "../components/layout/PageLayout";
import ScientificDisclaimer from "../components/ScientificDisclaimer";
import SectionCard from "../components/SectionCard";
import StatusBadge from "../components/StatusBadge";
import VinaWorkflowBar from "../components/VinaWorkflowBar";
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
  prepared: "可进行",
  running: "进行中",
  finished: "已完成",
  failed: "失败",
  cancelled: "失败",
  unknown: "需检查",
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
      if (response.metadata !== undefined) setMetadata(response.metadata ?? null);
      if (response.log_file !== undefined || response.metadata !== undefined) {
        setLogFile(response.log_file ?? metadataString(response.metadata ?? null, "log_file"));
      }
      if (response.scores !== undefined) setScores(response.scores);
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
      applyResponse(parseProjectResponse(rawPayload), "运行记录已刷新。");
    } catch (error) {
      setMessage("无法读取运行记录。");
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
      applyResponse(parseProjectResponse(rawPayload), "结果已解析。");
    } catch (error) {
      setMessage("无法解析 Vina 结果。");
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
      applyResponse(parseProjectResponse(rawPayload), "scores.csv 已加载。");
    } catch (error) {
      setMessage("无法读取 scores.csv。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <PageShell labelledBy="result-title">
      <PageHero
        eyebrow="结果与报告"
        title="查看对接结果"
        titleId="result-title"
        description="解析 log.txt，生成 scores.csv，并查看对接评分表。"
        actions={
          <>
          <ActionButton variant="text" onClick={onBack}>返回</ActionButton>
          <ActionButton onClick={() => void reloadRunMetadata()} disabled={isBusy}>刷新运行记录</ActionButton>
          </>
        }
      />

      <BodyGrid>
        <MainPanel>
          <div className="main-panel-content">
            <VinaWorkflowBar current="result" runId={runId} />

            <div className="status-strip">
              <article className="metric-card">
                <span>运行记录</span>
                <strong>{runId}</strong>
              </article>
              <article className="metric-card">
                <span>运行状态</span>
                <strong>{runStatusText[status] ?? status}</strong>
                <StatusBadge tone={status === "finished" ? "ok" : status === "failed" ? "error" : "warning"}>
                  {runStatusText[status] ?? "需检查"}
                </StatusBadge>
              </article>
              <article className="metric-card">
                <span>最佳对接评分</span>
                <strong>{displayedBestAffinity === null ? "尚未解析" : `${displayedBestAffinity} kcal/mol`}</strong>
              </article>
            </div>

            {status !== "finished" ? (
              <WarningCallout title="结果暂不可解析">
                <p>需要先完成 Vina 运行。</p>
              </WarningCallout>
            ) : null}

            <SectionCard title="scores.csv">
              <div className="button-row">
                <ActionButton variant="primary" disabled={!canAnalyze} onClick={() => void analyzeResults()}>
                  {isBusy ? "处理中..." : "解析 scores"}
                </ActionButton>
                <ActionButton variant="text" disabled={isBusy} onClick={() => void reloadScores()}>重新加载</ActionButton>
                <ActionButton disabled={status !== "finished"} onClick={() => onOpenViewer(project)}>查看构象</ActionButton>
                <ActionButton disabled={!(scores.length > 0 || displayedScoresFile)} onClick={() => onOpenReportPage(project, runId)}>
                  导出实验记录
                </ActionButton>
              </div>

              {scores.length > 0 ? (
                <div className="scores-table-wrap">
                  <table className="scores-table">
                    <thead>
                      <tr>
                        <th>构象</th>
                        <th>对接评分 kcal/mol</th>
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
                <p className="message-line">尚未加载 scores.csv。</p>
              )}
            </SectionCard>

            <AdvancedDetails>
              <dl className="meta-list">
                <div>
                  <dt>log.txt</dt>
                  <dd><code>{logPath}</code></dd>
                </div>
                <div>
                  <dt>本次 scores</dt>
                  <dd><code>{displayedScoresFile || "尚未生成"}</code></dd>
                </div>
                <div>
                  <dt>项目 scores</dt>
                  <dd><code>{displayedProjectScoresFile || "尚未生成"}</code></dd>
                </div>
                <div>
                  <dt>解析时间</dt>
                  <dd>{displayedAnalyzedAt || "未记录"}</dd>
                </div>
              </dl>
            </AdvancedDetails>

            <ScientificDisclaimer kind="score" />
            <CommandResultPanel title="结果解析" message={message} rawError={rawError} />
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
                <dt>最佳评分</dt>
                <dd>{displayedBestAffinity === null ? "未解析" : `${displayedBestAffinity} kcal/mol`}</dd>
              </div>
            </dl>
          </RightRailSection>

          <RightRailSection title="输出文件">
            <dl className="mode-context-list">
              <div>
                <dt>log</dt>
                <dd>{logPath}</dd>
              </div>
              <div>
                <dt>scores</dt>
                <dd>{displayedScoresFile || "未生成"}</dd>
              </div>
            </dl>
          </RightRailSection>

          <RightRailSection title="下一步">
            <p>{scores.length > 0 || displayedScoresFile ? "导出 Markdown 实验记录。" : "先解析 scores.csv。"}</p>
          </RightRailSection>
        </RightRail>
      </BodyGrid>
    </PageShell>
  );
}
