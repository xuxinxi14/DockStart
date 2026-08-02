import { lazy, Suspense, useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { CheckCircle, Clock, Crosshair, FileText, FolderOpen, Gauge, Microscope, Ruler, Timer } from "@phosphor-icons/react";
import { hydratedApi } from "../api/hydrated";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import CommandResultPanel from "../components/CommandResultPanel";
import HydratedProtocolScope from "../components/HydratedProtocolScope";
import HydratedResultSummary from "../components/HydratedResultSummary";
import { PageHero, PageShell } from "../components/layout/PageLayout";
import ScientificDisclaimer from "../components/ScientificDisclaimer";
import StatusBadge from "../components/StatusBadge";
import WarningCallout from "../components/WarningCallout";
import type {
  DockStartProject,
  HydratedResultsSuccess,
  LocalPoseKind,
  LocalPoseView,
  MultipleLigandMemberSummary,
  ProjectResponse,
  ScoreRow,
  VinaEvaluation,
  VinaRunMode,
} from "../types";
import {
  HYDRATED_PROTOCOL_ID,
  isHydratedProtocolId,
} from "../utils/hydratedWorkflow";

const PoseStructurePreview = lazy(() => import("../components/PoseStructurePreview"));
const MultiLigandPosePreview = lazy(() => import("../components/MultiLigandPosePreview"));

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
    evaluation: parsed.evaluation,
    evaluation_file: parsed.evaluation_file,
    scores_file: parsed.scores_file,
    project_scores_file: parsed.project_scores_file,
    best_affinity: parsed.best_affinity,
    primary_score_kcal_mol: parsed.primary_score_kcal_mol,
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

function metadataNestedString(
  metadata: Record<string, unknown> | null,
  objectKey: string,
  key: string,
): string {
  const value = metadata?.[objectKey];
  if (!value || typeof value !== "object" || Array.isArray(value)) return "";
  const nested = (value as Record<string, unknown>)[key];
  return typeof nested === "string" ? nested : "";
}

function metadataVinaVersion(metadata: Record<string, unknown> | null): string {
  return (
    metadataNestedString(metadata, "execution_vina", "version")
    || metadataString(metadata, "vina_version")
    || metadataNestedString(metadata, "prepared_vina", "version")
  );
}

function metadataProtocolId(metadata: Record<string, unknown> | null): string {
  const direct = metadataString(metadata, "protocol_id");
  if (direct) return direct;
  const protocol = metadata?.docking_protocol;
  if (!protocol || typeof protocol !== "object" || Array.isArray(protocol)) return "";
  const nested = (protocol as Record<string, unknown>).protocol_id;
  return typeof nested === "string" ? nested : "";
}

function metadataNumber(metadata: Record<string, unknown> | null, key: string): number | null {
  const value = metadata?.[key];
  return typeof value === "number" ? value : null;
}

function metadataMultipleLigandMembers(
  metadata: Record<string, unknown> | null,
): MultipleLigandMemberSummary[] {
  const direct = metadata?.members;
  const protocol = metadata?.docking_protocol;
  const nested = protocol && typeof protocol === "object" && !Array.isArray(protocol)
    ? (protocol as Record<string, unknown>).members
    : null;
  const candidates = Array.isArray(direct) ? direct : Array.isArray(nested) ? nested : [];
  return candidates.flatMap((item, index) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const member = item as Record<string, unknown>;
    return [{
      member_index: typeof member.member_index === "number" ? member.member_index : index + 1,
      member_id: typeof member.member_id === "string" ? member.member_id : undefined,
      display_name: typeof member.display_name === "string" ? member.display_name : undefined,
      source_name: typeof member.source_name === "string" ? member.source_name : undefined,
      source_file: typeof member.source_file === "string" ? member.source_file : undefined,
      source_sha256: typeof member.source_sha256 === "string" ? member.source_sha256 : undefined,
    }];
  });
}

function metadataPoseAttestation(
  metadata: Record<string, unknown> | null,
): Record<string, unknown> | null {
  const protocol = metadata?.docking_protocol;
  if (!protocol || typeof protocol !== "object" || Array.isArray(protocol)) return null;
  const attestation = (protocol as Record<string, unknown>).pose_input_attestation;
  return attestation && typeof attestation === "object" && !Array.isArray(attestation)
    ? attestation as Record<string, unknown>
    : null;
}

function shortSha256(value: unknown): string {
  const text = typeof value === "string" ? value : "";
  return /^[0-9a-f]{64}$/i.test(text) ? `${text.slice(0, 12)}…` : "未记录";
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

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatMetric(value: number | null | undefined, digits = 3): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";
}

function formatSignedMetric(value: number | null | undefined, digits = 3): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${value.toFixed(digits)}`;
}

function formatAngstrom(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(3)} Å` : "—";
}

function buildLocalEnergyRows(evaluation: VinaEvaluation) {
  const inputTerms = evaluation.input_energy_terms ?? [];
  const optimizedTerms = evaluation.energy_terms ?? [];
  const inputByKey = new Map(inputTerms.map((term) => [term.key, term]));
  const optimizedByKey = new Map(optimizedTerms.map((term) => [term.key, term]));
  const keys = [...new Set([...inputTerms.map((term) => term.key), ...optimizedTerms.map((term) => term.key)])];

  return keys.map((key) => {
    const input = inputByKey.get(key);
    const optimized = optimizedByKey.get(key);
    const inputValue = finiteNumber(input?.value_kcal_mol);
    const optimizedValue = finiteNumber(optimized?.value_kcal_mol);
    return {
      key,
      label: optimized?.label || input?.label || key,
      inputValue,
      optimizedValue,
      delta: inputValue !== null && optimizedValue !== null ? optimizedValue - inputValue : null,
    };
  });
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
  const [localPoseView, setLocalPoseView] = useState<LocalPoseView>("overlay");
  const [focusPoseRequest, setFocusPoseRequest] = useState<{ mode: number; token: number } | null>(null);
  const [metadata, setMetadata] = useState<Record<string, unknown> | null>(null);
  const [scores, setScores] = useState<ScoreRow[]>([]);
  const [hydratedResults, setHydratedResults] =
    useState<HydratedResultsSuccess | null>(null);
  const [evaluation, setEvaluation] = useState<VinaEvaluation | null>(null);
  const [evaluationFile, setEvaluationFile] = useState("");
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
  const rawRunMode = metadataString(metadata, "run_mode");
  const runMode: VinaRunMode = rawRunMode === "score_only" || rawRunMode === "local_only" ? rawRunMode : "dock";
  const isEvaluationMode = runMode !== "dock";
  const scoringProtocol = metadataString(metadata, "scoring_protocol") || "vina";
  const scoringFunction = metadataString(metadata, "scoring_function") || "vina";
  const protocolId = metadataProtocolId(metadata);
  const isHydrated = isHydratedProtocolId(protocolId);
  const isMultipleLigand = protocolId === "simultaneous_multi_ligand";
  const multipleLigandMembers = metadataMultipleLigandMembers(metadata);
  const isAd4Zn = protocolId === "ad4zn_beta";
  const isAd4Maps = scoringProtocol === "ad4_maps" || isAd4Zn;
  const ad4ProtocolLabel = isHydrated
    ? "实验性水合 AutoDock4"
    : isAd4Zn
      ? "AutoDock4Zn beta"
      : "AutoDock4 maps";
  const poseInputAttestation = metadataPoseAttestation(metadata);
  const analysisReady = isEvaluationMode ? Boolean(evaluation) : scores.length > 0;
  const canGenerateReport = status === "finished" && analysisReady && !isBusy;
  const canAnalyzeResults = status === "finished" && !analysisReady && !isBusy && !isMultipleLigand && !isHydrated;
  const logPath = logFile || metadataString(metadata, "log_file") || `runs/${runId}/log.txt`;
  const displayedScoresFile = scoresFile || metadataString(metadata, "scores_file");
  const displayedProjectScoresFile = projectScoresFile || metadataString(metadata, "project_scores_file");
  const displayedBestAffinity = bestAffinity ?? metadataNumber(metadata, "best_affinity");
  const displayedPrimaryScore = evaluation?.primary_score_kcal_mol ?? metadataNumber(metadata, "primary_score_kcal_mol");
  const displayedAnalyzedAt = analyzedAt || metadataString(metadata, "analyzed_at");
  const displayedReportFile = metadataString(metadata, "report_file") || metadataString(metadata, "project_report_file");
  const reportReady = Boolean(displayedReportFile);
  const referenceRmsd = metadataReferenceRmsd(metadata);
  const resultSdf = latestResultSdf(metadata);
  const comparison = evaluation?.comparison;
  const canOverlayLocalPoses = runMode === "local_only" && comparison?.comparable === true;
  const effectiveLocalPoseView: LocalPoseView = canOverlayLocalPoses
    ? localPoseView
    : localPoseView === "input"
      ? "input"
      : "optimized";
  const legacyLocalPoseKind: LocalPoseKind = effectiveLocalPoseView === "input" ? "input" : "optimized";
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
  const localEnergyRows = evaluation && runMode === "local_only" ? buildLocalEnergyRows(evaluation) : [];
  const explicitUnboundEnergy = evaluation?.unbound_energy?.mode === "explicit"
    ? finiteNumber(evaluation.unbound_energy.value_kcal_mol)
    : null;
  const unboundEnergyComparisonWarning = evaluation?.unbound_energy?.mode === "explicit"
    ? evaluation.unbound_energy.comparison_warning?.trim()
      || `本次显式使用 ${explicitUnboundEnergy ?? "未记录"} kcal/mol；评分只应与相同参考能量的结果比较。`
    : "";

  useEffect(() => {
    setViewerMode(null);
    setLocalPoseView("overlay");
    setFocusPoseRequest(null);
    setMetadata(null);
    setScores([]);
    setHydratedResults(null);
    setEvaluation(null);
    setEvaluationFile("");
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
      if (response.evaluation !== undefined) setEvaluation(response.evaluation);
      if (response.evaluation_file !== undefined || response.metadata !== undefined) {
        setEvaluationFile(response.evaluation_file ?? metadataString(response.metadata ?? null, "evaluation_file"));
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

  const applyHydratedResults = useCallback(
    (response: Awaited<ReturnType<typeof hydratedApi.loadResults>>): boolean => {
      if (!mountedRef.current) return false;
      if (!response.ok) {
        setHydratedResults(null);
        setMessage(response.error.message || "无法读取水合结果。");
        setRawError(
          [
            response.error.raw_error,
            response.error.suggestion,
          ].filter(Boolean).join("\n"),
        );
        return false;
      }
      setHydratedResults(response);
      setMetadata(response.metadata);
      setScores(response.scores);
      setScoresFile(metadataString(response.metadata, "scores_file"));
      setProjectScoresFile(
        metadataString(response.metadata, "project_scores_file"),
      );
      setBestAffinity(metadataNumber(response.metadata, "best_affinity"));
      setAnalyzedAt(metadataString(response.metadata, "analyzed_at"));
      setMessage(response.message);
      setRawError("");
      return true;
    },
    [],
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
        const observedProtocol = metadataProtocolId(runResponse.metadata ?? null);
        if (observedProtocol === HYDRATED_PROTOCOL_ID) {
          const hydratedResponse = await hydratedApi.loadResults(
            initialProject.project_dir,
            runId,
          );
          if (!mountedRef.current || requestId !== loadRequestRef.current) return;
          applyHydratedResults(hydratedResponse);
          return;
        }
        const observedMode = metadataString(runResponse.metadata ?? null, "run_mode");
        const evaluationMode = observedMode === "score_only" || observedMode === "local_only";
        const analysisPayload = await invoke<string>(evaluationMode ? "load_vina_evaluation" : "load_scores_csv", {
          projectDir: initialProject.project_dir,
          runId,
        });
        if (!mountedRef.current || requestId !== loadRequestRef.current) return;
        const analysisResponse = parseProjectResponse(analysisPayload);
        if (analysisResponse.ok) {
          applyResponse(analysisResponse, evaluationMode ? "姿势评价结果已加载。" : "运行记录与 scores.csv 已加载。");
        }
      }
    } catch (error) {
      if (!mountedRef.current || requestId !== loadRequestRef.current) return;
      setMessage("无法读取运行记录。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      if (mountedRef.current && requestId === loadRequestRef.current) setIsBusy(false);
    }
  }, [applyHydratedResults, applyResponse, initialProject.project_dir, runId]);

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
      applyResponse(
        parseProjectResponse(rawPayload),
        isMultipleLigand ? "多配体共同对接报告已生成。" : "深度结果分析报告已生成。",
      );
    } catch (error) {
      setMessage(isMultipleLigand ? "无法生成多配体共同对接报告。" : "无法生成深度结果分析报告。");
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
      if (isHydrated) {
        applyHydratedResults(
          await hydratedApi.loadResults(project.project_dir, runId),
        );
        return;
      }
      const rawPayload = await invoke<string>(isEvaluationMode ? "load_vina_evaluation" : "load_scores_csv", {
        projectDir: project.project_dir,
        runId,
      });
      applyResponse(
        parseProjectResponse(rawPayload),
        isEvaluationMode
          ? "姿势评价结果已加载。"
          : isMultipleLigand
            ? "联合 scores.csv 已加载。"
            : "scores.csv 已加载。",
      );
    } catch (error) {
      setMessage(isEvaluationMode ? "无法读取姿势评价结果。" : "无法读取 scores.csv。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  const selectedMode = viewerMode ?? scores[0]?.mode ?? 1;
  const poseAvailableScores = scores.filter((score) => score.pose_available !== false);
  const effectiveSelectedMode = poseAvailableScores.some((score) => score.mode === selectedMode)
    ? selectedMode
    : poseAvailableScores[0]?.mode ?? selectedMode;
  const selectedScore = scores.find((score) => score.mode === effectiveSelectedMode) ?? scores[0];
  const startedAt = metadataString(metadata, "started_at");
  const metadataFinishedAt = metadataString(metadata, "finished_at");
  const measuredElapsedSeconds = startedAt && metadataFinishedAt
    ? Math.max(0, (new Date(metadataFinishedAt).getTime() - new Date(startedAt).getTime()) / 1000)
    : null;
  const elapsedSeconds = metadataNumber(metadata, "elapsed_seconds") ?? measuredElapsedSeconds;
  const vinaVersion = metadataVinaVersion(metadata);
  const vinaDisplay = vinaVersion
    ? vinaVersion.toLowerCase().includes("vina")
      ? vinaVersion
      : `AutoDock Vina ${vinaVersion}`
    : "未记录";
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

  const analyzeResults = async () => {
    setIsBusy(true);
    setMessage("");
    setRawError("");
    try {
      const rawPayload = await invoke<string>("analyze_vina_run_results", {
        projectDir: project.project_dir,
        runId,
      });
      applyResponse(
        parseProjectResponse(rawPayload),
        isEvaluationMode ? "姿势评价结果已解析。" : "对接评分已解析。",
      );
    } catch (error) {
      setMessage(isEvaluationMode ? "无法解析姿势评价结果。" : "无法解析对接评分。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
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

  const openOutputDirectory = async (target: "result_sdf" | "reports") => {
    setIsBusy(true);
    setMessage("");
    setRawError("");
    try {
      const response = JSON.parse(await invoke<string>("open_result_output_directory", {
        projectDir: project.project_dir,
        target,
        runId,
        relativePath: target === "result_sdf" ? resultSdf : null,
      })) as LocalDirectoryResponse;
      if (!response.ok) {
        setMessage(response.error?.message || "无法打开本地目录。");
        setRawError(
          [response.error?.raw_error, response.error?.suggestion]
            .filter(Boolean)
            .join("\n"),
        );
        return;
      }
      setMessage(response.message || "已打开本地目录。");
    } catch (error) {
      setMessage("无法打开本地目录。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  const handleLocalPoseViewKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
    const options: LocalPoseView[] = canOverlayLocalPoses
      ? ["overlay", "input", "optimized"]
      : ["input", "optimized"];
    const currentIndex = Math.max(0, options.indexOf(effectiveLocalPoseView));
    let nextIndex = currentIndex;
    if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = options.length - 1;
    else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      nextIndex = (currentIndex - 1 + options.length) % options.length;
    } else {
      nextIndex = (currentIndex + 1) % options.length;
    }
    event.preventDefault();
    setLocalPoseView(options[nextIndex]);
    const radios = event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="radio"]');
    radios[nextIndex]?.focus();
  };

  return (
    <PageShell labelledBy="result-title" className="result-analysis-page">
      <PageHero
        eyebrow="结果 · RESULT ANALYSIS"
        title={runMode === "score_only"
          ? "当前姿势评分结果"
          : runMode === "local_only"
            ? "局部优化结果"
            : isHydrated
              ? "水合 AD4 对接结果"
            : isMultipleLigand
              ? "多配体共同对接结果"
              : "对接结果分析"}
        titleId="result-title"
        description={runMode === "score_only"
          ? "查看输入姿势的单点评分、能量分解与可复现运行记录。"
          : runMode === "local_only"
            ? "比较输入姿势与局部优化后姿势的评分、能量项和几何变化。"
            : isHydrated
              ? "查看 raw AD4 affinity、逐构象水分子分类和独立保存的水合输出。"
            : isMultipleLigand
              ? "查看两个配体在同一次联合搜索中生成的构象组、联合评分与可复现运行记录。"
            : isAd4Maps
              ? `查看 ${ad4ProtocolLabel} 对接构象、独立评分记录与可复现文件。`
              : "查看与比较对接构象的评分与结构，访问运行记录并导出实验文件。"}
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
        <div><Gauge aria-hidden="true" size={18} /><span>任务类型<strong>{runMode === "score_only" ? "仅评分" : runMode === "local_only" ? "局部优化" : isMultipleLigand ? "双配体联合搜索" : "全局对接"}</strong></span></div>
        <div><Timer aria-hidden="true" size={18} /><span>运行耗时<strong>{elapsedSeconds === null || !Number.isFinite(elapsedSeconds) ? "未记录" : `${Math.round(elapsedSeconds)} 秒`}</strong></span></div>
        <div><Clock aria-hidden="true" size={18} /><span>保存时间<strong>{formatTimestamp(finishedAt)}</strong></span></div>
      </section>

      {status !== "finished" ? (
        <WarningCallout title="结果暂不可解析"><p>需要先完成 Vina 运行。</p></WarningCallout>
      ) : null}
      {isAd4Maps ? (
        <WarningCallout title={`${ad4ProtocolLabel} 评分协议`}>
          <p>本页评分来自 {ad4ProtocolLabel}；不要与 Vina 或 Vinardo 的分值直接横向比较。</p>
        </WarningCallout>
      ) : null}
      {isHydrated ? <HydratedProtocolScope /> : null}
      {isMultipleLigand ? (
        <WarningCallout title="联合构象与联合评分">
          <p>
            每个 Mode 同时包含两个配体，评分描述整个联合体系，不能拆分成单个配体的 affinity，
            也不能与不同成员数量或不同成员组合的结果直接比较。
          </p>
          {multipleLigandMembers.length ? (
            <p>
              成员顺序：
              {" "}
              {multipleLigandMembers
                .map((member) => member.display_name || member.source_name || member.member_id || `配体 ${member.member_index}`)
                .join(" → ")}
            </p>
          ) : null}
        </WarningCallout>
      ) : null}
      {isEvaluationMode ? (
        <WarningCallout title={runMode === "score_only" ? "单点姿势评价" : "局部姿势优化"}>
          <p>{evaluation?.scientific_note || (runMode === "score_only"
            ? "本次没有搜索或生成新构象；分值只描述输入姿势。"
            : "本次只优化输入姿势附近的构象，不等同于全局对接。")}</p>
        </WarningCallout>
      ) : null}
      {isEvaluationMode ? (
        <WarningCallout title="输入姿势确认记录">
          {poseInputAttestation ? (
            <p>
              用户于 {formatTimestamp(String(poseInputAttestation.confirmed_at || ""))} 确认输入姿势。
              记录绑定运行受体 {shortSha256(poseInputAttestation.receptor_sha256)}
              {poseInputAttestation.flex_sha256
                ? `、柔性侧链 ${shortSha256(poseInputAttestation.flex_sha256)}`
                : ""}
              与配体 {shortSha256(poseInputAttestation.ligand_sha256)}。
              这是一项用户确认和文件完整性记录，不代表 DockStart 已自动验证坐标关系的科学有效性。
            </p>
          ) : (
            <p>
              该历史评价运行未保存输入姿势确认记录；结果可以读取，但其坐标系前提无法由 DockStart 追溯核对。
            </p>
          )}
        </WarningCallout>
      ) : null}
      {unboundEnergyComparisonWarning ? (
        <WarningCallout title="显式未结合态参考能量">
          <p>{unboundEnergyComparisonWarning}</p>
        </WarningCallout>
      ) : null}

      {isHydrated && hydratedResults ? (
        <HydratedResultSummary
          onSelectMode={setViewerMode}
          results={hydratedResults}
          selectedMode={effectiveSelectedMode}
        />
      ) : null}

      <div className="result-analysis-layout">
        <main className="result-analysis-main">
          <section className="result-pose-workbench">
            <div className="result-pose-viewer">
              {scores.length || evaluation ? (
                <>
                  {runMode === "local_only" ? (
                    <div
                      className="result-pose-kind-switch"
                      role="radiogroup"
                      aria-label="局部优化姿势视图"
                      onKeyDown={handleLocalPoseViewKeyDown}
                    >
                      {canOverlayLocalPoses ? (
                        <button
                          type="button"
                          role="radio"
                          className={effectiveLocalPoseView === "overlay" ? "is-active" : ""}
                          aria-checked={effectiveLocalPoseView === "overlay"}
                          tabIndex={effectiveLocalPoseView === "overlay" ? 0 : -1}
                          onClick={() => setLocalPoseView("overlay")}
                        >
                          叠合比较
                        </button>
                      ) : null}
                      <button
                        type="button"
                        role="radio"
                        className={effectiveLocalPoseView === "input" ? "is-active" : ""}
                        aria-checked={effectiveLocalPoseView === "input"}
                        tabIndex={effectiveLocalPoseView === "input" ? 0 : -1}
                        onClick={() => setLocalPoseView("input")}
                      >
                        输入姿势
                      </button>
                      <button
                        type="button"
                        role="radio"
                        className={effectiveLocalPoseView === "optimized" ? "is-active" : ""}
                        aria-checked={effectiveLocalPoseView === "optimized"}
                        tabIndex={effectiveLocalPoseView === "optimized" ? 0 : -1}
                        onClick={() => setLocalPoseView("optimized")}
                      >
                        优化后姿势
                      </button>
                    </div>
                  ) : null}
                  <Suspense fallback={<div className="run-preview-loading">正在加载 3D 构象查看器…</div>}>
                    {isMultipleLigand ? (
                      <MultiLigandPosePreview
                        className="result-pose-preview"
                        projectDir={project.project_dir}
                        runId={runId}
                        mode={effectiveSelectedMode}
                        focusRequest={focusPoseRequest}
                      />
                    ) : (
                      <PoseStructurePreview
                        className="result-pose-preview"
                        projectDir={project.project_dir}
                        runId={runId}
                        mode={effectiveSelectedMode}
                        poseKind={runMode === "local_only" && !canOverlayLocalPoses
                          ? legacyLocalPoseKind
                          : runMode === "score_only"
                            ? "input"
                            : undefined}
                        poseLabel={isEvaluationMode
                          ? runMode === "score_only"
                            ? "输入姿势"
                            : !canOverlayLocalPoses && legacyLocalPoseKind === "input"
                              ? "输入姿势"
                              : !canOverlayLocalPoses
                                ? "优化后姿势"
                                : undefined
                          : undefined}
                        localPoseView={runMode === "local_only" && canOverlayLocalPoses
                          ? effectiveLocalPoseView
                          : undefined}
                        onLocalPoseViewChange={runMode === "local_only" && canOverlayLocalPoses
                          ? setLocalPoseView
                          : undefined}
                        focusRequest={focusPoseRequest}
                      />
                    )}
                  </Suspense>
                </>
              ) : (
                <div className="result-pose-empty"><Microscope aria-hidden="true" size={28} /><span>{isEvaluationMode ? "解析评价结果后显示姿势" : "解析 scores 后显示构象"}</span></div>
              )}
            </div>

            <div className="result-pose-ranking">
              {isEvaluationMode ? (
                <>
                  <header>
                    <span>{runMode === "score_only" ? "输入姿势" : "优化前后评分"}</span>
                    <strong>{runMode === "score_only" ? "单个结果" : "局部优化"}</strong>
                  </header>
                  {runMode === "local_only" ? (
                    <div className="result-local-comparison">
                      <table aria-label="局部优化前后评分">
                        <thead><tr><th>阶段</th><th>评分 (kcal/mol)</th></tr></thead>
                        <tbody>
                          <tr><th scope="row">输入姿势</th><td>{formatMetric(inputScore)}</td></tr>
                          <tr><th scope="row">优化后</th><td>{formatMetric(optimizedScore)}</td></tr>
                          <tr className="is-delta"><th scope="row">Δ（优化后－输入）</th><td>{formatSignedMetric(scoreDelta)}</td></tr>
                        </tbody>
                      </table>
                      {comparisonReason ? <p className="result-comparison-state">{comparisonReason}</p> : null}
                      <small>负值仅表示当前评分数值降低，不代表真实结合能力提高。</small>
                      {canOverlayLocalPoses ? (
                        <p className="result-local-overlay-note">
                          3D 叠合直接显示本次 run 保存的输入坐标与优化后坐标，未对姿势进行额外对齐。
                        </p>
                      ) : null}
                      <dl className="result-local-geometry">
                        {finiteNumber(geometry?.heavy_atom_rmsd_aligned_angstrom) !== null ? (
                          <div><dt>对齐后 RMSD（仅数值比较）</dt><dd>{formatAngstrom(geometry?.heavy_atom_rmsd_aligned_angstrom)}</dd></div>
                        ) : null}
                        <div><dt>未对齐重原子 RMSD</dt><dd>{formatAngstrom(geometry?.heavy_atom_rmsd_no_alignment_angstrom)}</dd></div>
                        <div><dt>最大重原子位移</dt><dd>{formatAngstrom(geometry?.max_heavy_atom_displacement_angstrom)}</dd></div>
                        <div><dt>匹配重原子</dt><dd>{finiteNumber(geometry?.heavy_atom_count) ?? "—"}</dd></div>
                      </dl>
                      {geometryReason ? <p className="result-comparison-state">{geometryReason}</p> : null}
                    </div>
                  ) : (
                    <div className="result-evaluation-summary">
                      <span>
                        {isAd4Maps
                          ? `${isAd4Zn ? "AutoDock4Zn" : "AutoDock4"} 主要评分`
                          : scoringFunction === "vinardo"
                            ? "Vinardo 主要评分"
                            : "Vina 主要评分"}
                      </span>
                      <strong>{displayedPrimaryScore ?? "—"} <small>kcal/mol</small></strong>
                      <dl>
                        <div><dt>范围</dt><dd>{evaluation?.autobox ? "按配体自动建立" : isAd4Maps ? ad4ProtocolLabel : "项目 Box"}</dd></div>
                        <div><dt>评分函数</dt><dd>{evaluation?.scoring_function || scoringFunction}</dd></div>
                        <div><dt>输出姿势</dt><dd>未生成</dd></div>
                      </dl>
                    </div>
                  )}
                </>
              ) : (
                <>
                  <header><span>{isMultipleLigand ? "联合构象列表" : "构象列表"}</span><strong>按评分排序</strong></header>
                  <div className="result-ranking-head"><span>排名</span><span>构象</span><span>{isMultipleLigand ? "联合评分" : isHydrated ? "Raw AD4 affinity" : "评分"}</span><span>RMSD l.b.</span><span>RMSD u.b.</span></div>
                  <div className="result-ranking-list">
                    {scores.map((score, index) => (
                      <button
                        key={score.mode}
                        type="button"
                        className={[
                          effectiveSelectedMode === score.mode ? "is-selected" : "",
                          score.pose_available === false ? "is-unavailable" : "",
                        ].filter(Boolean).join(" ")}
                        onClick={() => {
                          if (score.pose_available !== false) setViewerMode(score.mode);
                        }}
                        disabled={score.pose_available === false}
                        aria-pressed={effectiveSelectedMode === score.mode}
                        title={score.pose_available === false ? "该评分行未写入 out.pdbqt，不能加载构象" : undefined}
                      >
                        <span>{index + 1}</span>
                        <strong>
                          {isMultipleLigand ? "联合 " : ""}Mode {score.mode}
                          {score.pose_available === false ? " · 仅日志" : ""}
                        </strong>
                        <span>{formatScoreValue(score.affinity_kcal_mol)}</span>
                        <span>{formatScoreValue(score.rmsd_lb)}</span>
                        <span>{formatScoreValue(score.rmsd_ub)}</span>
                      </button>
                    ))}
                  </div>
                </>
              )}
              <div className="result-pose-focus-action">
                <ActionButton
                  variant="primary"
                  disabled={!(scores.length || evaluation)}
                  onClick={() => setFocusPoseRequest((current) => ({
                    mode: effectiveSelectedMode,
                    token: (current?.token ?? 0) + 1,
                  }))}
                >
                  <Crosshair aria-hidden="true" size={17} />
                  {runMode === "local_only"
                    ? effectiveLocalPoseView === "overlay"
                      ? "定位到叠合姿势"
                      : effectiveLocalPoseView === "input"
                        ? "定位到输入姿势"
                        : "定位到优化后姿势"
                    : isMultipleLigand
                      ? "定位到两个配体"
                      : "定位到当前配体"}
                </ActionButton>
                <small>
                  {runMode === "local_only" && effectiveLocalPoseView === "overlay"
                    ? "聚焦本次 run 的两份原始坐标；3D 视图不使用对齐变换。"
                    : isEvaluationMode
                      ? "只调整视角，不会改变当前姿势或评分。"
                      : isMultipleLigand
                        ? `将视角聚焦到联合 Mode ${effectiveSelectedMode} 的两个配体，不会改变构象或评分。`
                        : `将视角聚焦到 Mode ${effectiveSelectedMode}，不会改变构象或评分。`}
                </small>
              </div>
              {!isEvaluationMode ? (
                <p>
                  {isMultipleLigand
                    ? "RMSD 描述整组联合构象相对 Mode 1 的差异；标为“仅日志”的评分行被 energy_range 排除，没有可加载的 out.pdbqt 构象。"
                    : "RMSD 相对基于 Mode 1 的构象，仅用于本次输出内比较。"}
                </p>
              ) : null}
            </div>
          </section>

          <nav className="result-tabs" aria-label="结果详情">
            <button className={detailTab === "scores" ? "active" : ""} type="button" aria-pressed={detailTab === "scores"} onClick={() => setDetailTab("scores")}>{runMode === "local_only" ? "优化前后能量" : isEvaluationMode ? "能量分解" : "评分"}</button>
            <button className={detailTab === "run-files" ? "active" : ""} type="button" aria-pressed={detailTab === "run-files"} onClick={() => setDetailTab("run-files")}>运行日志与文件</button>
            <button type="button" onClick={() => onOpenReportPage(project, runId)}>分析报告</button>
          </nav>

          {detailTab === "scores" ? <section className="result-score-ledger">
            <div className="result-score-actions">
              <div>
                <strong>{isEvaluationMode ? "evaluation.json" : "scores.csv"}</strong>
                <span>{isEvaluationMode ? evaluationFile || metadataString(metadata, "evaluation_file") || "尚未生成" : displayedScoresFile || "尚未生成"}</span>
              </div>
              <div>
                {isHydrated && !analysisReady ? (
                  <ActionButton
                    variant="primary"
                    disabled={isBusy || status !== "finished"}
                    onClick={() => void reloadScores()}
                  >
                    {isBusy ? "读取中…" : "加载水合结果"}
                  </ActionButton>
                ) : analysisReady ? (
                  <ActionButton variant="primary" disabled={!canGenerateReport} onClick={() => void generateDetailedReport()}>
                    {isBusy ? "生成中…" : reportReady ? "重新生成分析" : "生成结果分析"}
                  </ActionButton>
                ) : (
                  <ActionButton variant="primary" disabled={!canAnalyzeResults} onClick={() => void analyzeResults()}>
                    {isBusy ? "解析中…" : isEvaluationMode ? "解析能量分解" : "解析对接评分"}
                  </ActionButton>
                )}
                <ActionButton variant="text" disabled={isBusy} onClick={() => void reloadScores()}>重新加载</ActionButton>
              </div>
            </div>

            {runMode === "local_only" && evaluation ? (
              <>
                <div className="scores-table-wrap result-energy-breakdown">
                  <table className="scores-table">
                    <thead><tr><th>能量项</th><th>输入</th><th>优化后</th><th>Δ</th></tr></thead>
                    <tbody>
                      {localEnergyRows.map((term) => (
                        <tr key={term.key}>
                          <td>{term.label}</td>
                          <td>{formatMetric(term.inputValue)}</td>
                          <td>{formatMetric(term.optimizedValue)}</td>
                          <td>{formatSignedMetric(term.delta)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {!evaluation.input_energy_terms?.length ? (
                  <p className="result-table-note">此次运行未记录输入姿势的能量分项，因此输入列和 Δ 不可用。</p>
                ) : null}
              </>
            ) : isEvaluationMode && evaluation ? (
              <div className="scores-table-wrap result-energy-breakdown">
                <table className="scores-table">
                  <thead><tr><th>能量项</th><th>数值 (kcal/mol)</th><th>Vina 项号</th></tr></thead>
                  <tbody>
                    {evaluation.energy_terms.map((term) => (
                      <tr key={term.key}>
                        <td>{term.label}</td>
                        <td>{formatScoreValue(term.value_kcal_mol)}</td>
                        <td>{term.term_number ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : scores.length ? (
              <div className="scores-table-wrap">
                <table className="scores-table">
                  <thead><tr><th>{isMultipleLigand ? "联合构象" : "构象"}</th><th>{isMultipleLigand ? "联合评分 kcal/mol" : isHydrated ? "Raw AD4 affinity kcal/mol" : isAd4Maps ? `${isAd4Zn ? "AutoDock4Zn" : "AutoDock4"} 评分 kcal/mol` : "对接评分 kcal/mol"}</th><th>RMSD l.b. (Å)</th><th>RMSD u.b. (Å)</th></tr></thead>
                  <tbody>
                    {scores.map((score) => (
                      <tr
                        key={score.mode}
                        className={[
                          effectiveSelectedMode === score.mode ? "is-selected" : "",
                          score.pose_available === false ? "is-unavailable" : "",
                        ].filter(Boolean).join(" ")}
                        onClick={() => {
                          if (score.pose_available !== false) setViewerMode(score.mode);
                        }}
                        aria-disabled={score.pose_available === false}
                        title={score.pose_available === false ? "该评分行未写入 out.pdbqt，不能加载构象" : undefined}
                      >
                        <td>Mode {score.mode}{score.pose_available === false ? "（仅日志）" : ""}</td><td>{formatScoreValue(score.affinity_kcal_mol)}</td><td>{formatScoreValue(score.rmsd_lb)}</td><td>{formatScoreValue(score.rmsd_ub)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p className="message-line">{isEvaluationMode ? "尚未加载 evaluation.json。" : "尚未加载 scores.csv。"}</p>}

            <AdvancedDetails summary="运行与文件详情">
              <dl className="meta-list">
                <div><dt>log.txt</dt><dd><code>{logPath}</code></dd></div>
                {isEvaluationMode ? (
                  <>
                    <div><dt>评价结果</dt><dd><code>{evaluationFile || metadataString(metadata, "evaluation_file") || "尚未生成"}</code></dd></div>
                    <div><dt>{runMode === "local_only" ? "优化后姿势" : "输入姿势"}</dt><dd><code>{evaluation?.pose_file || metadataString(metadata, "pose_file") || "未记录"}</code></dd></div>
                  </>
                ) : (
                  <>
                    <div><dt>本次 scores</dt><dd><code>{displayedScoresFile || "尚未生成"}</code></dd></div>
                    <div><dt>项目 scores</dt><dd><code>{displayedProjectScoresFile || "尚未生成"}</code></dd></div>
                  </>
                )}
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
                <div><dt>任务类型</dt><dd>{runMode === "score_only" ? "仅评分" : runMode === "local_only" ? "局部优化" : isMultipleLigand ? "双配体联合搜索" : "全局对接"}</dd></div>
                <div><dt>评分协议</dt><dd>{isAd4Maps ? ad4ProtocolLabel : scoringFunction === "vinardo" ? "Vinardo" : "Vina"}</dd></div>
                <div><dt>Vina 版本</dt><dd>{vinaDisplay}</dd></div>
                <div><dt>开始时间</dt><dd>{formatTimestamp(startedAt)}</dd></div>
                <div><dt>结束时间</dt><dd>{formatTimestamp(metadataFinishedAt)}</dd></div>
                <div><dt>配置文件</dt><dd><code>{metadataString(metadata, "config_file") || "未记录"}</code></dd></div>
                <div><dt>{runMode === "score_only" ? "评价姿势" : runMode === "local_only" ? "优化后姿势" : "输出构象"}</dt><dd><code>{metadataString(metadata, runMode === "score_only" ? "pose_file" : "output_file") || "未记录"}</code></dd></div>
                <div><dt>运行日志</dt><dd><code>{logPath}</code></dd></div>
                <div><dt>{isEvaluationMode ? "评价结果" : "评分表"}</dt><dd><code>{isEvaluationMode ? evaluationFile || metadataString(metadata, "evaluation_file") || "未生成" : displayedScoresFile || "未生成"}</code></dd></div>
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
            <span>{runMode === "local_only" ? "主要评价结果" : isEvaluationMode ? "姿势评价" : isMultipleLigand ? "所选联合构象" : isHydrated ? "水合 AD4 构象" : isAd4Maps ? `${isAd4Zn ? "AutoDock4Zn" : "AutoDock4"} 构象` : "所选构象"}</span>
            <strong>{isEvaluationMode ? (runMode === "score_only" ? "输入姿势" : "优化后评分") : `${isMultipleLigand ? "联合 " : ""}Mode ${effectiveSelectedMode}`}</strong>
            <b>{isEvaluationMode ? displayedPrimaryScore ?? "—" : selectedScore ? formatScoreValue(selectedScore.affinity_kcal_mol) : displayedBestAffinity ?? "—"} <small>kcal/mol</small></b>
          </section>
          <section className="result-output-files">
            <h2>输出文件</h2>
            <div><FileText aria-hidden="true" size={18} /><span><strong>log.txt</strong><small>{logPath}</small></span></div>
            {runMode === "local_only" ? (
              <div>
                <FileText aria-hidden="true" size={18} />
                <span>
                  <strong>优化后姿势 PDBQT</strong>
                  <small>{evaluation?.output_pose_file || metadataString(metadata, "output_file") || "未生成"}</small>
                </span>
              </div>
            ) : null}
            <div><FileText aria-hidden="true" size={18} /><span><strong>{isEvaluationMode ? "evaluation.json" : "scores.csv"}</strong><small>{isEvaluationMode ? evaluationFile || metadataString(metadata, "evaluation_file") || "未生成" : displayedScoresFile || "未生成"}</small></span></div>
            <div><FileText aria-hidden="true" size={18} /><span><strong>{isEvaluationMode ? "evaluation_report.md" : isMultipleLigand ? "multi_ligand_report.md" : isHydrated ? "hydrated_docking_report.md" : "docking_report.md"}</strong><small>{displayedReportFile || "未生成"}</small></span></div>
            {runMode !== "score_only" && !isMultipleLigand ? <div><FileText aria-hidden="true" size={18} /><span><strong>{runMode === "local_only" ? "优化后姿势 SDF" : "poses.sdf"}</strong><small>{resultSdf || "未导出"}</small></span></div> : null}
          </section>
          {!isEvaluationMode && !isMultipleLigand ? <section className="result-reference-rmsd">
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
          </section> : null}
          <section className="result-rail-actions">
            {runMode !== "score_only" && !isMultipleLigand ? <ActionButton disabled={isBusy || !resultSdf} title={resultSdf ? "打开实际导出的拓扑 SDF 所在目录" : "尚未记录可用的拓扑 SDF 输出"} onClick={() => void openOutputDirectory("result_sdf")}>
              <FolderOpen aria-hidden="true" size={17} /> 打开拓扑 SDF 目录
            </ActionButton> : null}
            <ActionButton variant="primary" disabled={isBusy} onClick={() => void openOutputDirectory("reports")}>
              <FolderOpen aria-hidden="true" size={17} /> 打开报告目录
            </ActionButton>
            <ActionButton onClick={() => void copyOutputPath()}><FolderOpen aria-hidden="true" size={17} /> 复制输出路径</ActionButton>
          </section>
          <ScientificDisclaimer kind="score" />
        </aside>
      </div>
    </PageShell>
  );
}
