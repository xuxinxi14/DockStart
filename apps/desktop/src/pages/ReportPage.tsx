import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { FolderOpen } from "@phosphor-icons/react";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import CommandResultPanel from "../components/CommandResultPanel";
import { BodyGrid, MainPanel, PageHero, PageShell, RightRail, RightRailSection } from "../components/layout/PageLayout";
import MarkdownPreview from "../components/MarkdownPreview";
import ScientificDisclaimer from "../components/ScientificDisclaimer";
import SectionCard from "../components/SectionCard";
import StatusBadge from "../components/StatusBadge";
import VinaWorkflowBar from "../components/VinaWorkflowBar";
import WarningCallout from "../components/WarningCallout";
import type {
  DockStartProject,
  ProjectResponse,
  RunFileStatus,
  ScoreRow,
  VinaEvaluation,
  VinaEvaluationStage,
  VinaRunMode,
} from "../types";

const PoseStructurePreview = lazy(() => import("../components/PoseStructurePreview"));

type ReportPageProps = {
  project: DockStartProject;
  runId: string;
  onBack: () => void;
  onProjectChange: (project: DockStartProject) => void;
};

type MarkdownPreviewResponse = {
  ok: boolean;
  relative_path?: string;
  content?: string;
  message?: string;
  error?: {
    message?: string;
    raw_error?: string;
    suggestion?: string;
  };
};

type LocalDirectoryResponse = {
  ok: boolean;
  directory?: string;
  message?: string;
  error?: {
    message?: string;
    raw_error?: string;
    suggestion?: string;
  };
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
    evaluation: parsed.evaluation,
    scores: parsed.scores,
    evaluation_file: parsed.evaluation_file,
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

function metadataProtocolId(metadata: Record<string, unknown> | null): string {
  const direct = metadataString(metadata, "protocol_id");
  if (direct) return direct;
  const protocol = metadata?.docking_protocol;
  if (!protocol || typeof protocol !== "object" || Array.isArray(protocol)) return "";
  const nested = (protocol as Record<string, unknown>).protocol_id;
  return typeof nested === "string" ? nested : "";
}

function reportRunMode(
  metadata: Record<string, unknown> | null,
  project: DockStartProject,
): VinaRunMode {
  const metadataMode = metadataString(metadata, "run_mode");
  const candidate = metadataMode || project.docking_protocol?.run_mode;
  return candidate === "score_only" || candidate === "local_only" ? candidate : "dock";
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatMetric(value: number | null | undefined, digits = 3): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";
}

function formatSignedMetric(value: number | null | undefined, digits = 3): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function formatAngstrom(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(3)} Å` : "—";
}

function stageIdentity(stage: VinaEvaluationStage): string {
  return String(stage.id || stage.stage_id || stage.kind || stage.name || "").toLowerCase();
}

function stageLabel(stage: VinaEvaluationStage, index: number): string {
  if (stage.label?.trim()) return stage.label;
  const identity = stageIdentity(stage);
  if (identity.includes("input") || identity.includes("score")) return "输入姿势评分";
  if (identity.includes("local") || identity.includes("optim")) return "局部优化";
  return `阶段 ${index + 1}`;
}

function stageStatusLabel(stage: VinaEvaluationStage): string {
  const status = String(stage.status || "").toLowerCase();
  if (status === "finished" || status === "completed" || status === "success") return "已完成";
  if (status === "running") return "进行中";
  if (status === "failed" || status === "error") return "失败";
  if (status === "cancelled" || status === "canceled") return "已取消";
  return status || "未记录";
}

function stageElapsed(stage: VinaEvaluationStage): string {
  const recorded = finiteNumber(stage.elapsed_seconds) ?? finiteNumber(stage.duration_seconds);
  if (recorded !== null) return `${recorded.toFixed(1)} 秒`;
  if (!stage.started_at || !stage.finished_at) return "—";
  const elapsed = (new Date(stage.finished_at).getTime() - new Date(stage.started_at).getTime()) / 1000;
  return Number.isFinite(elapsed) && elapsed >= 0 ? `${elapsed.toFixed(1)} 秒` : "—";
}

function geometryMappingLabel(value: string | undefined): string {
  if (value === "meeko_smiles_index") return "Meeko SMILES 索引";
  if (value === "pdbqt_serial_full_identity") return "PDBQT 序号与完整身份";
  return value || "—";
}

export default function ReportPage({ project: initialProject, runId, onBack, onProjectChange }: ReportPageProps) {
  const [project, setProject] = useState(initialProject);
  const [metadata, setMetadata] = useState<Record<string, unknown> | null>(null);
  const [evaluation, setEvaluation] = useState<VinaEvaluation | null>(null);
  const [files, setFiles] = useState<RunFileStatus[]>([]);
  const [scores, setScores] = useState<ScoreRow[]>([]);
  const [reportFile, setReportFile] = useState("");
  const [projectReportFile, setProjectReportFile] = useState("");
  const [reportedAt, setReportedAt] = useState("");
  const [reportStatus, setReportStatus] = useState("missing");
  const [canExport, setCanExport] = useState(false);
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [previewContent, setPreviewContent] = useState("");
  const [previewPath, setPreviewPath] = useState("");
  const [previewError, setPreviewError] = useState("");
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);

  const runMode = reportRunMode(metadata, project);
  const isEvaluation = runMode !== "dock";
  const analysisStatus = useMemo(
    () => files.find((file) => file.key === (isEvaluation ? "evaluation" : "scores")),
    [files, isEvaluation],
  );
  const runReportStatus = useMemo(() => files.find((file) => file.key === "run_report"), [files]);
  const projectReportStatus = useMemo(() => files.find((file) => file.key === "project_report"), [files]);
  const protocolId = metadataProtocolId(metadata);
  const isMultipleLigand = protocolId === "simultaneous_multi_ligand";
  const isHydrated = protocolId === "hydrated_ad4_experimental";
  const isAd4Zn = protocolId === "ad4zn_beta";
  const isAd4Maps = metadataString(metadata, "scoring_protocol") === "ad4_maps" || isAd4Zn;
  const ad4ProtocolLabel = isAd4Zn ? "AutoDock4Zn beta" : "AutoDock4 maps";
  const modeTitle =
    runMode === "score_only"
      ? "当前姿势评分报告"
      : runMode === "local_only"
        ? "局部优化报告"
        : isMultipleLigand
          ? "多配体共同对接报告"
          : "结果分析报告";
  const analysisLabel = isEvaluation
    ? "evaluation.json"
    : isMultipleLigand
      ? "联合 scores.csv"
      : "scores.csv";
  const displayedReportFile =
    reportFile ||
    metadataString(metadata, "report_file") ||
    `runs/${runId}/${
      isEvaluation
        ? "evaluation_report.md"
        : isMultipleLigand
          ? "multi_ligand_report.md"
          : "docking_report.md"
    }`;
  const displayedProjectReportFile =
    projectReportFile ||
    metadataString(metadata, "project_report_file") ||
    (isEvaluation
      ? `reports/${runMode}_report.md`
      : isMultipleLigand
        ? "reports/simultaneous_multi_ligand_report.md"
      : isAd4Zn
        ? "reports/ad4zn_docking_report.md"
        : isAd4Maps
        ? "reports/ad4_docking_report.md"
        : "reports/docking_report.md");
  const displayedReportedAt = reportedAt || metadataString(metadata, "reported_at");
  const hasAnalysis = analysisStatus?.status === "ok";
  const hasExportedReport = reportStatus === "exported";
  const runTaskLabel = runMode === "score_only"
    ? "当前姿势评分"
    : runMode === "local_only"
      ? "局部优化"
      : isMultipleLigand
        ? "多配体共同对接"
        : "全局对接";
  const comparison = evaluation?.comparison;
  const inputScore = finiteNumber(comparison?.input_score_kcal_mol);
  const optimizedScore = finiteNumber(comparison?.optimized_score_kcal_mol)
    ?? (runMode === "local_only" ? finiteNumber(evaluation?.primary_score_kcal_mol) : null);
  const scoreDelta = finiteNumber(comparison?.delta_score_kcal_mol)
    ?? (inputScore !== null && optimizedScore !== null ? optimizedScore - inputScore : null);
  const comparisonReason = comparison?.reason?.trim()
    || (runMode === "local_only" && inputScore === null ? "此次运行未记录输入姿势的基线评分。" : "");
  const geometry = comparison?.geometry;
  const geometryReason = geometry?.error?.message?.trim()
    || (runMode === "local_only" && !geometry ? "此次运行未记录优化前后的几何比较。" : "")
    || (geometry?.ok === false ? "优化前后的几何比较不可用。" : "");
  const stages = evaluation?.stages ?? [];
  const topPoseScores = useMemo(
    () => scores.filter((score) => score.pose_available !== false).slice(0, 4),
    [scores],
  );

  const applyResponse = useCallback(
    (response: ProjectResponse, fallbackMessage: string) => {
      if (response.project) {
        setProject(response.project);
        onProjectChange(response.project);
      }
      if (response.metadata !== undefined) setMetadata(response.metadata ?? null);
      if (response.evaluation !== undefined) setEvaluation(response.evaluation);
      if (response.scores !== undefined) setScores(response.scores);
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

  const loadReportPreview = useCallback(async (projectDir: string, relativePath: string) => {
    setIsPreviewLoading(true);
    setPreviewError("");
    try {
      const rawPayload = await invoke<string>("read_project_markdown_report", {
        projectDir,
        relativePath,
      });
      const response = JSON.parse(rawPayload) as MarkdownPreviewResponse;
      if (!response.ok || typeof response.content !== "string") {
        setPreviewContent("");
        setPreviewPath(relativePath);
        setPreviewError(
          [
            response.error?.message || "无法读取 Markdown 报告预览。",
            response.error?.suggestion,
          ].filter(Boolean).join(" "),
        );
        return;
      }
      setPreviewContent(response.content);
      setPreviewPath(response.relative_path || relativePath);
    } catch (error) {
      setPreviewContent("");
      setPreviewPath(relativePath);
      setPreviewError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsPreviewLoading(false);
    }
  }, []);

  const reloadReportStatus = useCallback(async () => {
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>("get_report_status", {
        projectDir: initialProject.project_dir,
        runId,
      });
      const response = parseProjectResponse(rawPayload);
      const ok = applyResponse(response, "报告状态已刷新。");
      const observedProject = response.project ?? initialProject;
      const observedReportPath = response.project_report_file
        || metadataString(response.metadata ?? null, "project_report_file");
      if (ok && response.report_status === "exported" && observedReportPath) {
        await loadReportPreview(observedProject.project_dir, observedReportPath);
      } else {
        setPreviewContent("");
        setPreviewPath(observedReportPath);
        setPreviewError("");
      }
      const observedMode = reportRunMode(response.metadata ?? null, observedProject);
      const observedProtocol = metadataProtocolId(response.metadata ?? null);
      const evaluationReady = response.files?.some((file) => file.key === "evaluation" && file.status === "ok");
      const scoresReady = response.files?.some((file) => file.key === "scores" && file.status === "ok");
      if (ok && observedMode !== "dock" && evaluationReady) {
        const evaluationPayload = await invoke<string>("load_vina_evaluation", {
          projectDir: initialProject.project_dir,
          runId,
        });
        const evaluationResponse = parseProjectResponse(evaluationPayload);
        if (evaluationResponse.ok && evaluationResponse.evaluation) {
          setEvaluation(evaluationResponse.evaluation);
        } else {
          setEvaluation(null);
          setMessage(evaluationResponse.error?.message ?? "报告状态已读取，但无法加载局部优化对比数据。");
          setRawError(evaluationResponse.error?.raw_error ?? "");
        }
      } else {
        setEvaluation(null);
        if (
          ok
          && observedMode === "dock"
          && scoresReady
          && observedProtocol !== "simultaneous_multi_ligand"
          && observedProtocol !== "hydrated_ad4_experimental"
        ) {
          const scoresPayload = await invoke<string>("load_scores_csv", {
            projectDir: initialProject.project_dir,
            runId,
          });
          const scoresResponse = parseProjectResponse(scoresPayload);
          if (scoresResponse.ok) {
            setScores(scoresResponse.scores ?? []);
          } else {
            setScores([]);
            setMessage(scoresResponse.error?.message ?? "报告状态已读取，但无法加载构象排名。");
            setRawError(scoresResponse.error?.raw_error ?? "");
          }
        } else {
          setScores([]);
        }
      }
    } catch (error) {
      setMessage("无法读取报告状态。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  }, [applyResponse, initialProject.project_dir, loadReportPreview, runId]);

  useEffect(() => {
    setEvaluation(null);
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
      const response = parseProjectResponse(rawPayload);
      const exported = applyResponse(
        response,
        isMultipleLigand
          ? "多配体共同对接 Markdown 报告已生成。"
          : "Markdown 结果分析报告已生成。",
      );
      if (exported) await reloadReportStatus();
    } catch (error) {
      setMessage("无法导出 Markdown 实验记录。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  const openReportDirectory = async () => {
    setIsBusy(true);
    setMessage("");
    setRawError("");
    try {
      const response = JSON.parse(await invoke<string>("open_result_output_directory", {
        projectDir: project.project_dir,
        target: "reports",
        runId,
        relativePath: null,
      })) as LocalDirectoryResponse;
      if (!response.ok) {
        setMessage(response.error?.message || "无法打开报告目录。");
        setRawError(
          [response.error?.raw_error, response.error?.suggestion]
            .filter(Boolean)
            .join("\n"),
        );
        return;
      }
      setMessage(response.message || "已打开报告目录。");
    } catch (error) {
      setMessage("无法打开报告目录。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  const markdownPreview = (
    <SectionCard
      title="Markdown 阅读预览"
      description="以安全的只读阅读视图渲染实际保存的报告；不会执行 Markdown 中的 HTML 或脚本。"
      className="report-markdown-preview-section"
    >
      <MarkdownPreview
        content={previewContent}
        path={previewPath || displayedProjectReportFile}
        loading={isPreviewLoading}
        error={previewError}
      />
    </SectionCard>
  );

  return (
    <PageShell labelledBy="report-title">
      <PageHero
        eyebrow="结果与报告"
        title={modeTitle}
        titleId="report-title"
        description={
          runMode === "score_only"
            ? "生成包含当前输入姿势评分、能量项、评价范围、输入哈希与可复现记录的 Markdown 报告。"
            : runMode === "local_only"
              ? "生成包含输入与优化后评分、几何变化、两阶段日志和可复现记录的 Markdown 报告。"
              : isMultipleLigand
                ? "生成两个配体共同搜索的成员顺序、联合构象评分、输入哈希与可复现记录。联合评分不会拆分为单个成员贡献。"
              : isAd4Maps
                ? `生成独立的 ${ad4ProtocolLabel} 评分、网格、输入哈希与可复现记录。`
                : "生成包含评分统计、构象离散度、结构事实、运行参数与可复现记录的 Markdown 报告。"
        }
        actions={
          <ActionButton variant="text" onClick={onBack}>返回结果</ActionButton>
        }
      />

      <BodyGrid>
        <MainPanel>
          <div className="main-panel-content">
            <VinaWorkflowBar current="report" runId={runId} runMode={runMode} />

            <div className="next-step-strip report-action-strip">
              <div>
                <strong>
                  {hasExportedReport
                    ? `${modeTitle}已生成`
                    : hasAnalysis
                      ? `${analysisLabel} 已就绪，可以生成报告`
                      : "请先返回结果页完成分析"}
                </strong>
                <p>
                  {hasExportedReport
                    ? displayedProjectReportFile
                    : hasAnalysis
                      ? "报告将沿用结果页已解析的数据，并保存到项目 reports 目录。"
                      : `报告依赖结果页生成的 ${analysisLabel}；完成解析后再回到这里。`}
                </p>
              </div>
              <div className="button-row">
                <StatusBadge tone={hasExportedReport ? "ok" : hasAnalysis ? "warning" : "muted"}>
                  {hasExportedReport ? "已生成" : hasAnalysis ? "待生成" : "等待分析"}
                </StatusBadge>
                <ActionButton variant="primary" disabled={isBusy || !canExport} onClick={() => void exportReport()}>
                  {isBusy
                    ? "处理中..."
                    : hasExportedReport
                      ? "重新生成报告"
                      : "生成报告"}
                </ActionButton>
                <ActionButton disabled={isBusy} onClick={() => void openReportDirectory()}>
                  <FolderOpen aria-hidden="true" size={17} /> 打开报告目录
                </ActionButton>
                <ActionButton variant="text" onClick={() => void reloadReportStatus()} disabled={isBusy}>刷新</ActionButton>
              </div>
            </div>

            {message || rawError ? <CommandResultPanel title="报告操作" message={message} rawError={rawError} /> : null}

            {runMode === "dock" && !isMultipleLigand && !isHydrated && topPoseScores.length ? (
              <SectionCard
                title="排名靠前的对接姿势"
                description={`展示 scores.csv 中前 ${topPoseScores.length} 个可读取构象；视图自动聚焦配体所在区域。`}
                className="report-pose-gallery-section"
              >
                <div className="report-pose-gallery">
                  {topPoseScores.map((score) => (
                    <article className="report-pose-card" key={score.mode}>
                      <header>
                        <strong>Mode {score.mode}</strong>
                        <span>{formatMetric(score.affinity_kcal_mol, 2)} kcal/mol</span>
                      </header>
                      <Suspense fallback={<div className="report-pose-loading">正在加载构象…</div>}>
                        <PoseStructurePreview
                          compact
                          className="report-pose-preview"
                          projectDir={project.project_dir}
                          runId={runId}
                          mode={score.mode}
                          poseLabel={`Mode ${score.mode}`}
                        />
                      </Suspense>
                      <footer>
                        <span>RMSD 下界 {formatMetric(score.rmsd_lb, 2)} Å</span>
                        <span>上界 {formatMetric(score.rmsd_ub, 2)} Å</span>
                      </footer>
                    </article>
                  ))}
                </div>
                <p className="report-pose-gallery-note">
                  图中结构来自本次运行的受体与对应输出构象，仅用于检查姿势位置和方向，不代表相互作用分析或实验结合证据。
                </p>
              </SectionCard>
            ) : null}

            {hasExportedReport ? markdownPreview : null}

            {!hasAnalysis ? (
              <WarningCallout title="分析报告暂不可生成">
                <p>
                  {isEvaluation
                    ? "请先完成姿势评价，并在结果页解析 evaluation.json。"
                    : isMultipleLigand
                      ? "请先完成多配体共同对接，并确认联合 scores.csv 已生成。"
                      : "请先完成对接，并在结果页解析 scores.csv。"}
                </p>
              </WarningCallout>
            ) : null}
            {isMultipleLigand ? (
              <WarningCallout title="报告使用联合构象评分">
                <p>
                  每个 Mode 同时包含两个配体，评分属于完整联合体系。报告不会把该分数拆成两个成员的
                  affinity，也不会把不同成员数量或不同成员组合的结果视为可直接比较。
                </p>
              </WarningCallout>
            ) : null}
            {isAd4Maps ? (
              <WarningCallout title="协议间评分不可直接比较">
                <p>
                  {isEvaluation
                    ? `本次评价使用 ${ad4ProtocolLabel}；其分值不能与 Vina / Vinardo 分值直接比较。`
                    : `${ad4ProtocolLabel} 报告与 Vina / Vinardo 项目报告分开保存。`}
                </p>
              </WarningCallout>
            ) : null}

            {runMode === "local_only" ? (
              <SectionCard title="局部优化对比" className="report-local-comparison">
                {evaluation ? (
                  <>
                    <div className="report-local-comparison-grid">
                      <section aria-labelledby="report-local-score-title">
                        <h3 id="report-local-score-title">评分变化</h3>
                        <table className="report-local-score-table">
                          <thead><tr><th>阶段</th><th>评分 (kcal/mol)</th></tr></thead>
                          <tbody>
                            <tr><th scope="row">输入姿势</th><td>{formatMetric(inputScore)}</td></tr>
                            <tr><th scope="row">优化后</th><td>{formatMetric(optimizedScore)}</td></tr>
                            <tr className="is-delta"><th scope="row">Δ（优化后－输入）</th><td>{formatSignedMetric(scoreDelta)}</td></tr>
                          </tbody>
                        </table>
                        {comparisonReason ? <p className="report-local-unavailable">{comparisonReason}</p> : null}
                        <small>负值仅表示当前评分数值降低，不代表真实结合能力提高。</small>
                      </section>

                      <section aria-labelledby="report-local-geometry-title">
                        <h3 id="report-local-geometry-title">几何变化</h3>
                        <dl className="report-local-geometry-list">
                          {finiteNumber(geometry?.heavy_atom_rmsd_aligned_angstrom) !== null ? (
                            <div><dt>对齐后重原子 RMSD</dt><dd>{formatAngstrom(geometry?.heavy_atom_rmsd_aligned_angstrom)}</dd></div>
                          ) : null}
                          <div><dt>未对齐重原子 RMSD</dt><dd>{formatAngstrom(geometry?.heavy_atom_rmsd_no_alignment_angstrom)}</dd></div>
                          <div><dt>平均重原子位移</dt><dd>{formatAngstrom(geometry?.mean_heavy_atom_displacement_angstrom)}</dd></div>
                          <div><dt>最大重原子位移</dt><dd>{formatAngstrom(geometry?.max_heavy_atom_displacement_angstrom)}</dd></div>
                          <div><dt>质心位移</dt><dd>{formatAngstrom(geometry?.centroid_displacement_angstrom)}</dd></div>
                          <div><dt>匹配重原子</dt><dd>{finiteNumber(geometry?.heavy_atom_count) ?? "—"}</dd></div>
                          <div><dt>匹配方法</dt><dd>{geometryMappingLabel(geometry?.matched_by || geometry?.mapping_method)}</dd></div>
                        </dl>
                        {geometryReason ? <p className="report-local-unavailable">{geometryReason}</p> : null}
                      </section>
                    </div>

                    <section className="report-local-stages" aria-labelledby="report-local-stages-title">
                      <h3 id="report-local-stages-title">执行阶段</h3>
                      {stages.length ? (
                        <div className="scores-table-wrap">
                          <table className="scores-table">
                            <thead><tr><th>阶段</th><th>状态</th><th>退出码</th><th>评分</th><th>耗时</th><th>记录</th></tr></thead>
                            <tbody>
                              {stages.map((stage, index) => {
                                const stageScore = finiteNumber(stage.score_kcal_mol)
                                  ?? finiteNumber(stage.primary_score_kcal_mol);
                                const evidence = stage.log_file
                                  || stage.output_file
                                  || stage.output_pose_file
                                  || stage.error_message
                                  || stage.error?.message
                                  || "—";
                                return (
                                  <tr key={`${stageIdentity(stage) || "stage"}-${index}`}>
                                    <td>{stageLabel(stage, index)}</td>
                                    <td>{stageStatusLabel(stage)}</td>
                                    <td>{finiteNumber(stage.exit_code) ?? "—"}</td>
                                    <td>{formatMetric(stageScore)}</td>
                                    <td>{stageElapsed(stage)}</td>
                                    <td><code>{evidence}</code></td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>
                      ) : (
                        <p className="report-local-unavailable">此次运行未记录分阶段执行数据。</p>
                      )}
                    </section>
                  </>
                ) : (
                  <p className="report-local-unavailable">
                    {hasAnalysis ? "evaluation.json 已存在，但局部优化对比数据尚未加载。" : "完成结果解析后显示优化前后对比。"}
                  </p>
                )}
              </SectionCard>
            ) : null}

            {!hasExportedReport ? markdownPreview : null}

            <AdvancedDetails>
              <dl className="meta-list">
                {[analysisStatus, runReportStatus, projectReportStatus].filter(Boolean).map((file) => (
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
          </div>
        </MainPanel>

        <RightRail>
          <RightRailSection title="本次运行">
            <dl className="mode-context-list">
              <div>
                <dt>run</dt>
                <dd>{runId}</dd>
              </div>
              <div>
                <dt>任务类型</dt>
                <dd>{runTaskLabel}</dd>
              </div>
              {isAd4Maps ? (
                <div>
                  <dt>评分协议</dt>
                  <dd>{ad4ProtocolLabel}</dd>
                </div>
              ) : null}
              {isMultipleLigand ? (
                <div>
                  <dt>运行协议</dt>
                  <dd>多配体共同对接（实验性）</dd>
                </div>
              ) : null}
            </dl>
          </RightRailSection>

          <RightRailSection title="输出位置">
            <p>{displayedProjectReportFile}</p>
            {displayedReportedAt ? <small>生成时间：{displayedReportedAt}</small> : null}
          </RightRailSection>

          <RightRailSection title="科学边界">
            <p>
              {runMode === "local_only"
                ? "报告会保留两阶段评分、日志与几何比较；不可用的量会注明原因。"
                : isEvaluation
                  ? "报告记录当前输入姿势的评分与能量项，不代表完成了全局构象搜索。"
                  : isMultipleLigand
                    ? "报告记录两个成员的固定顺序与联合构象评分，不提供单个成员的独立评分贡献。"
                    : "报告提供统计汇总、结构事实和可复现记录；不会把 docking score 解释为真实结合或药效证据。"}
            </p>
          </RightRailSection>
        </RightRail>
      </BodyGrid>
    </PageShell>
  );
}
