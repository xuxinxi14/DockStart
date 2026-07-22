import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { CheckCircle, Clock, Crosshair, FileText, FolderOpen, Gauge, Microscope, Ruler, Timer, TrayArrowDown } from "@phosphor-icons/react";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import CommandResultPanel from "../components/CommandResultPanel";
import { PageHero, PageShell } from "../components/layout/PageLayout";
import ScientificDisclaimer from "../components/ScientificDisclaimer";
import StatusBadge from "../components/StatusBadge";
import WarningCallout from "../components/WarningCallout";
import type { DockStartProject, ProjectResponse, ScoreRow } from "../types";
import { startResultExportTask, waitForBackgroundTask } from "../utils/backgroundTasks";

const PoseStructurePreview = lazy(() => import("../components/PoseStructurePreview"));

type ResultPageProps = {
  project: DockStartProject;
  runId: string;
  onBack: () => void;
  onProjectChange: (project: DockStartProject) => void;
  onOpenReportPage: (project: DockStartProject, runId: string) => void;
};

const runStatusText: Record<string, string> = {
  prepared: "可进行",
  running: "进行中",
  finished: "已完成",
  failed: "失败",
  cancelled: "已取消",
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
    report_file: parsed.report_file,
    project_report_file: parsed.project_report_file,
    reported_at: parsed.reported_at,
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

type ReferenceRmsdResult = {
  mode: number;
  rmsd_angstrom: number;
  method: string;
  heavy_atom_count: number;
  reference_source_name: string;
  reference_sha256: string;
  calculated_at: string;
};

function metadataReferenceRmsd(metadata: Record<string, unknown> | null): ReferenceRmsdResult | null {
  const value = metadata?.reference_rmsd;
  if (!value || typeof value !== "object") return null;
  const result = value as Partial<ReferenceRmsdResult>;
  if (typeof result.mode !== "number" || typeof result.rmsd_angstrom !== "number") return null;
  return {
    mode: result.mode,
    rmsd_angstrom: result.rmsd_angstrom,
    method: typeof result.method === "string" ? result.method : "重原子、对称性修正",
    heavy_atom_count: typeof result.heavy_atom_count === "number" ? result.heavy_atom_count : 0,
    reference_source_name: typeof result.reference_source_name === "string" ? result.reference_source_name : "参考配体",
    reference_sha256: typeof result.reference_sha256 === "string" ? result.reference_sha256 : "",
    calculated_at: typeof result.calculated_at === "string" ? result.calculated_at : "",
  };
}

function latestResultSdf(metadata: Record<string, unknown> | null): string {
  const exports = metadata?.result_exports;
  if (!Array.isArray(exports)) return "";
  const latest = [...exports].reverse().find((item) => item && typeof item === "object") as Record<string, unknown> | undefined;
  return typeof latest?.output_file === "string" ? latest.output_file : "";
}

function formatScoreValue(value: number): string {
  return Number.isFinite(value) ? String(value) : "";
}

function formatTimestamp(value: string): string {
  if (!value) return "未记录";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export default function ResultPage({
  project: initialProject,
  runId,
  onBack,
  onProjectChange,
  onOpenReportPage,
}: ResultPageProps) {
  const [project, setProject] = useState(initialProject);
  const [viewerMode, setViewerMode] = useState<number | null>(null);
  const [focusPoseRequest, setFocusPoseRequest] = useState<{ mode: number; token: number } | null>(null);
  const [metadata, setMetadata] = useState<Record<string, unknown> | null>(null);
  const [scores, setScores] = useState<ScoreRow[]>([]);
  const [logFile, setLogFile] = useState("");
  const [scoresFile, setScoresFile] = useState("");
  const [projectScoresFile, setProjectScoresFile] = useState("");
  const [bestAffinity, setBestAffinity] = useState<number | null>(null);
  const [analyzedAt, setAnalyzedAt] = useState("");
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [detailTab, setDetailTab] = useState<"scores" | "run-files">("scores");
  const [isBusy, setIsBusy] = useState(false);
  const mountedRef = useRef(true);
  const loadRequestRef = useRef(0);

  const status = metadataString(metadata, "status") || "unknown";
  const canGenerateReport = status === "finished" && scores.length > 0 && !isBusy;
  const logPath = logFile || metadataString(metadata, "log_file") || `runs/${runId}/log.txt`;
  const displayedScoresFile = scoresFile || metadataString(metadata, "scores_file");
  const displayedProjectScoresFile = projectScoresFile || metadataString(metadata, "project_scores_file");
  const displayedBestAffinity = bestAffinity ?? metadataNumber(metadata, "best_affinity");
  const displayedAnalyzedAt = analyzedAt || metadataString(metadata, "analyzed_at");
  const displayedReportFile = metadataString(metadata, "report_file") || metadataString(metadata, "project_report_file");
  const reportReady = Boolean(displayedReportFile);
  const referenceRmsd = metadataReferenceRmsd(metadata);
  const resultSdf = latestResultSdf(metadata);

  useEffect(() => {
    setViewerMode(null);
    setFocusPoseRequest(null);
    setMetadata(null);
    setScores([]);
    setLogFile("");
    setScoresFile("");
    setProjectScoresFile("");
    setBestAffinity(null);
    setAnalyzedAt("");
    setMessage("");
    setRawError("");
    setDetailTab("scores");
  }, [runId]);

  const applyResponse = useCallback(
    (response: ProjectResponse, fallbackMessage: string) => {
      if (!mountedRef.current) return false;
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
    const requestId = ++loadRequestRef.current;
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>("get_run_files_status", {
        projectDir: initialProject.project_dir,
        runId,
      });
      if (!mountedRef.current || requestId !== loadRequestRef.current) return;
      const runResponse = parseProjectResponse(rawPayload);
      const runReady = applyResponse(runResponse, "运行记录已刷新。");
      if (runReady && metadataString(runResponse.metadata ?? null, "status") === "finished") {
        const scoresPayload = await invoke<string>("load_scores_csv", {
          projectDir: initialProject.project_dir,
          runId,
        });
        if (!mountedRef.current || requestId !== loadRequestRef.current) return;
        const scoresResponse = parseProjectResponse(scoresPayload);
        if (scoresResponse.ok) {
          applyResponse(scoresResponse, "运行记录与 scores.csv 已加载。");
        }
      }
    } catch (error) {
      if (!mountedRef.current || requestId !== loadRequestRef.current) return;
      setMessage("无法读取运行记录。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      if (mountedRef.current && requestId === loadRequestRef.current) setIsBusy(false);
    }
  }, [applyResponse, initialProject.project_dir, runId]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      loadRequestRef.current += 1;
    };
  }, []);

  useEffect(() => {
    void reloadRunMetadata();
  }, [reloadRunMetadata]);

  const generateDetailedReport = async () => {
    setIsBusy(true);
    setMessage("");
    setRawError("");
    try {
      const rawPayload = await invoke<string>("export_markdown_report", {
        projectDir: project.project_dir,
        runId,
      });
      applyResponse(parseProjectResponse(rawPayload), "深度结果分析报告已生成。");
    } catch (error) {
      setMessage("无法生成深度结果分析报告。");
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

  const selectedMode = viewerMode ?? scores[0]?.mode ?? 1;
  const selectedScore = scores.find((score) => score.mode === selectedMode) ?? scores[0];
  const startedAt = metadataString(metadata, "started_at");
  const metadataFinishedAt = metadataString(metadata, "finished_at");
  const measuredElapsedSeconds = startedAt && metadataFinishedAt
    ? Math.max(0, (new Date(metadataFinishedAt).getTime() - new Date(startedAt).getTime()) / 1000)
    : null;
  const elapsedSeconds = metadataNumber(metadata, "elapsed_seconds") ?? measuredElapsedSeconds;
  const vinaVersion = metadataString(metadata, "vina_version") || "1.2.7";
  const vinaDisplay = vinaVersion.toLowerCase().includes("vina") ? vinaVersion : `AutoDock Vina ${vinaVersion}`;
  const finishedAt = metadataString(metadata, "finished_at") || displayedAnalyzedAt || project.updated_at;

  const copyOutputPath = async () => {
    try {
      await navigator.clipboard.writeText(`${project.project_dir}\\runs\\${runId}`);
      setMessage("运行输出路径已复制。");
      setRawError("");
    } catch (error) {
      setMessage("无法复制运行输出路径。");
      setRawError(error instanceof Error ? error.message : String(error));
    }
  };

  const calculateReferenceRmsd = async () => {
    const selected = await open({
      multiple: false,
      directory: false,
      title: "选择共晶参考配体",
      filters: [{ name: "参考配体", extensions: ["sdf", "mol", "pdb", "pdbqt"] }],
    });
    if (!selected || Array.isArray(selected)) return;
    setIsBusy(true);
    setMessage("");
    setRawError("");
    try {
      const rawPayload = await invoke<string>("calculate_reference_ligand_rmsd", {
        projectDir: project.project_dir,
        runId,
        mode: selectedMode,
        referencePath: selected,
      });
      applyResponse(parseProjectResponse(rawPayload), `Mode ${selectedMode} 的共晶参考 RMSD 已计算。`);
    } catch (error) {
      setMessage("无法计算共晶参考 RMSD。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  const exportTopologySdf = async () => {
    setIsBusy(true);
    setMessage("");
    setRawError("");
    try {
      const statusPayload = JSON.parse(await invoke<string>("get_result_export_status", {
        projectDir: project.project_dir,
        runId,
      })) as { ok?: boolean; ready?: boolean; message?: string; error?: { message?: string; raw_error?: string } };
      if (!statusPayload.ok || !statusPayload.ready) {
        throw new Error(statusPayload.error?.message || statusPayload.message || "当前结果缺少 Meeko 原始拓扑，不能安全导出 SDF。");
      }
      const task = await startResultExportTask(project.project_dir, runId);
      const completed = await waitForBackgroundTask(task.task_id, (next) => {
        if (mountedRef.current) setMessage(next.progress.message || "正在通过 Meeko 恢复 SDF…");
      });
      const result = completed.result_json
        ? JSON.parse(completed.result_json) as { ok?: boolean; message?: string; error?: { message?: string; raw_error?: string } }
        : null;
      if (completed.status !== "finished" || !result?.ok) {
        throw new Error(result?.error?.message || completed.error || "SDF 导出失败。");
      }
      setMessage(result.message || "SDF 已导出。未根据原子距离猜测键级。");
      await reloadRunMetadata();
    } catch (error) {
      setMessage("无法安全导出拓扑 SDF。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <PageShell labelledBy="result-title" className="result-analysis-page">
      <PageHero
        eyebrow="结果 · RESULT ANALYSIS"
        title="对接结果分析"
        titleId="result-title"
        description="查看与比较对接构象的评分与结构，访问运行记录并导出实验文件。"
        actions={
          <>
            <StatusBadge tone={status === "finished" ? "ok" : status === "failed" ? "error" : "warning"}>
              {runStatusText[status] ?? "需检查"}
            </StatusBadge>
            <ActionButton variant="text" onClick={onBack}>返回工作台</ActionButton>
            <ActionButton onClick={() => void reloadRunMetadata()} disabled={isBusy}>刷新结果</ActionButton>
          </>
        }
      />

      <section className="result-run-strip" aria-label="运行摘要">
        <div><Microscope aria-hidden="true" size={18} /><span>运行标识<strong>{runId}</strong></span></div>
        <div><CheckCircle aria-hidden="true" size={18} weight="fill" /><span>状态<strong>{runStatusText[status] ?? status}</strong></span></div>
        <div><Gauge aria-hidden="true" size={18} /><span>对接引擎<strong>{vinaDisplay}</strong></span></div>
        <div><Timer aria-hidden="true" size={18} /><span>运行耗时<strong>{elapsedSeconds === null || !Number.isFinite(elapsedSeconds) ? "未记录" : `${Math.round(elapsedSeconds)} 秒`}</strong></span></div>
        <div><Clock aria-hidden="true" size={18} /><span>保存时间<strong>{formatTimestamp(finishedAt)}</strong></span></div>
      </section>

      {status !== "finished" ? (
        <WarningCallout title="结果暂不可解析"><p>需要先完成 Vina 运行。</p></WarningCallout>
      ) : null}

      <div className="result-analysis-layout">
        <main className="result-analysis-main">
          <section className="result-pose-workbench">
            <div className="result-pose-viewer">
              {scores.length ? (
                <Suspense fallback={<div className="run-preview-loading">正在加载 3D 构象查看器…</div>}>
                  <PoseStructurePreview
                    className="result-pose-preview"
                    projectDir={project.project_dir}
                    runId={runId}
                    mode={selectedMode}
                    focusRequest={focusPoseRequest}
                  />
                </Suspense>
              ) : (
                <div className="result-pose-empty"><Microscope aria-hidden="true" size={28} /><span>解析 scores 后显示构象</span></div>
              )}
            </div>

            <div className="result-pose-ranking">
              <header><span>构象列表</span><strong>按评分排序</strong></header>
              <div className="result-ranking-head"><span>排名</span><span>构象</span><span>评分</span><span>RMSD l.b.</span><span>RMSD u.b.</span></div>
              <div className="result-ranking-list">
                {scores.map((score, index) => (
                  <button
                    key={score.mode}
                    type="button"
                    className={selectedMode === score.mode ? "is-selected" : ""}
                    onClick={() => setViewerMode(score.mode)}
                    aria-pressed={selectedMode === score.mode}
                  >
                    <span>{index + 1}</span>
                    <strong>Mode {score.mode}</strong>
                    <span>{formatScoreValue(score.affinity_kcal_mol)}</span>
                    <span>{formatScoreValue(score.rmsd_lb)}</span>
                    <span>{formatScoreValue(score.rmsd_ub)}</span>
                  </button>
                ))}
              </div>
              <div className="result-pose-focus-action">
                <ActionButton
                  variant="primary"
                  disabled={!scores.length}
                  onClick={() => setFocusPoseRequest((current) => ({
                    mode: selectedMode,
                    token: (current?.token ?? 0) + 1,
                  }))}
                >
                  <Crosshair aria-hidden="true" size={17} />
                  定位到当前配体
                </ActionButton>
                <small>将视角聚焦到 Mode {selectedMode}，不会改变构象或评分。</small>
              </div>
              <p>RMSD 相对基于 Mode 1 的构象，仅用于本次输出内比较。</p>
            </div>
          </section>

          <nav className="result-tabs" aria-label="结果详情">
            <button className={detailTab === "scores" ? "active" : ""} type="button" aria-pressed={detailTab === "scores"} onClick={() => setDetailTab("scores")}>评分</button>
            <button className={detailTab === "run-files" ? "active" : ""} type="button" aria-pressed={detailTab === "run-files"} onClick={() => setDetailTab("run-files")}>运行日志与文件</button>
            <button type="button" onClick={() => onOpenReportPage(project, runId)}>分析报告</button>
          </nav>

          {detailTab === "scores" ? <section className="result-score-ledger">
            <div className="result-score-actions">
              <div><strong>scores.csv</strong><span>{displayedScoresFile || "尚未生成"}</span></div>
              <div>
                <ActionButton variant="primary" disabled={!canGenerateReport} onClick={() => void generateDetailedReport()}>
                  {isBusy ? "生成中…" : reportReady ? "重新生成分析" : "生成结果分析"}
                </ActionButton>
                <ActionButton variant="text" disabled={isBusy} onClick={() => void reloadScores()}>重新加载</ActionButton>
              </div>
            </div>

            {scores.length ? (
              <div className="scores-table-wrap">
                <table className="scores-table">
                  <thead><tr><th>构象</th><th>对接评分 kcal/mol</th><th>RMSD l.b. (Å)</th><th>RMSD u.b. (Å)</th></tr></thead>
                  <tbody>
                    {scores.map((score) => (
                      <tr key={score.mode} className={selectedMode === score.mode ? "is-selected" : ""} onClick={() => setViewerMode(score.mode)}>
                        <td>Mode {score.mode}</td><td>{formatScoreValue(score.affinity_kcal_mol)}</td><td>{formatScoreValue(score.rmsd_lb)}</td><td>{formatScoreValue(score.rmsd_ub)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p className="message-line">尚未加载 scores.csv。</p>}

            <AdvancedDetails summary="运行与文件详情">
              <dl className="meta-list">
                <div><dt>log.txt</dt><dd><code>{logPath}</code></dd></div>
                <div><dt>本次 scores</dt><dd><code>{displayedScoresFile || "尚未生成"}</code></dd></div>
                <div><dt>项目 scores</dt><dd><code>{displayedProjectScoresFile || "尚未生成"}</code></dd></div>
                <div><dt>解析时间</dt><dd>{displayedAnalyzedAt || "未记录"}</dd></div>
              </dl>
            </AdvancedDetails>
            {message || rawError ? <CommandResultPanel title="结果状态" message={message} rawError={rawError} /> : null}
          </section> : (
            <section className="result-score-ledger result-run-files" aria-label="运行日志与文件">
              <div className="result-score-actions">
                <div><strong>{runId} · 可复现运行记录</strong><span>这里展示已保存记录；“刷新结果”才会重新读取磁盘。</span></div>
                <ActionButton disabled={isBusy} onClick={() => void reloadRunMetadata()}>刷新运行文件</ActionButton>
              </div>
              <dl className="result-run-file-grid">
                <div><dt>运行状态</dt><dd>{runStatusText[status] ?? status}</dd></div>
                <div><dt>Vina 版本</dt><dd>{vinaDisplay}</dd></div>
                <div><dt>开始时间</dt><dd>{formatTimestamp(startedAt)}</dd></div>
                <div><dt>结束时间</dt><dd>{formatTimestamp(metadataFinishedAt)}</dd></div>
                <div><dt>配置文件</dt><dd><code>{metadataString(metadata, "config_file") || "未记录"}</code></dd></div>
                <div><dt>输出构象</dt><dd><code>{metadataString(metadata, "output_file") || "未记录"}</code></dd></div>
                <div><dt>运行日志</dt><dd><code>{logPath}</code></dd></div>
                <div><dt>评分表</dt><dd><code>{displayedScoresFile || "未生成"}</code></dd></div>
              </dl>
              <AdvancedDetails summary="完整 metadata 快照">
                <pre>{metadata ? JSON.stringify(metadata, null, 2) : "尚未读取 metadata.json。"}</pre>
              </AdvancedDetails>
              {message || rawError ? <CommandResultPanel title="运行文件状态" message={message} rawError={rawError} /> : null}
            </section>
          )}
        </main>

        <aside className="result-analysis-rail">
          <section className="result-selected-pose">
            <span>所选构象</span><strong>Mode {selectedMode}</strong>
            <b>{selectedScore ? formatScoreValue(selectedScore.affinity_kcal_mol) : displayedBestAffinity ?? "—"} <small>kcal/mol</small></b>
          </section>
          <section className="result-output-files">
            <h2>输出文件</h2>
            <div><FileText aria-hidden="true" size={18} /><span><strong>log.txt</strong><small>{logPath}</small></span></div>
            <div><FileText aria-hidden="true" size={18} /><span><strong>scores.csv</strong><small>{displayedScoresFile || "未生成"}</small></span></div>
            <div><FileText aria-hidden="true" size={18} /><span><strong>docking_report.md</strong><small>{displayedReportFile || "未生成"}</small></span></div>
            <div><FileText aria-hidden="true" size={18} /><span><strong>poses.sdf</strong><small>{resultSdf || "未导出"}</small></span></div>
          </section>
          <section className="result-reference-rmsd">
            <h2><Ruler aria-hidden="true" size={18} /> 共晶姿势验证</h2>
            {referenceRmsd ? (
              <div className="result-reference-rmsd-value">
                <span>Mode {referenceRmsd.mode}</span>
                <strong>{referenceRmsd.rmsd_angstrom.toFixed(3)} Å</strong>
                <small>{referenceRmsd.reference_source_name} · {referenceRmsd.heavy_atom_count} 个重原子</small>
              </div>
            ) : (
              <p>选择同一化学实体的共晶配体，计算重原子、对称性修正 RMSD。</p>
            )}
            <ActionButton disabled={isBusy || !scores.length} onClick={() => void calculateReferenceRmsd()}>
              {referenceRmsd ? "更换参考并重算" : "选择参考配体并计算"}
            </ActionButton>
            <small>此值不同于列表中相对 Mode 1 的 RMSD；化学连接不一致时不会强行比较。</small>
          </section>
          <section className="result-rail-actions">
            <ActionButton disabled={isBusy || status !== "finished"} onClick={() => void exportTopologySdf()}>
              <TrayArrowDown aria-hidden="true" size={17} /> {resultSdf ? "重新导出拓扑 SDF" : "导出拓扑 SDF"}
            </ActionButton>
            <ActionButton variant="primary" disabled={!(scores.length || displayedScoresFile)} onClick={() => onOpenReportPage(project, runId)}>
              <FileText aria-hidden="true" size={17} /> {reportReady ? "查看分析报告" : "生成分析报告"}
            </ActionButton>
            <ActionButton onClick={() => void copyOutputPath()}><FolderOpen aria-hidden="true" size={17} /> 复制输出路径</ActionButton>
          </section>
          <ScientificDisclaimer kind="score" />
        </aside>
      </div>
    </PageShell>
  );
}
