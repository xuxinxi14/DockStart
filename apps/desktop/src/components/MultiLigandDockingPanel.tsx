import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  ArrowDown,
  ArrowUp,
  CheckCircle,
  Flask,
  FolderOpen,
  Play,
  SpinnerGap,
  WarningCircle,
} from "@phosphor-icons/react";

import type { DockStartProject } from "../types";
import {
  readyMultipleLigandCandidates,
  moveMultipleLigandMember,
  normalizeMultipleLigandSelection,
  selectedMultipleLigandFiles,
  toggleMultipleLigandMember,
} from "../utils/multipleLigandSelection";
import {
  normalizeLigandImportPreview,
  type LigandImportCandidate,
  type LigandImportPreview,
} from "../utils/screeningLigandImport";
import {
  cancelQueuedBackgroundTask,
  findActiveBackgroundTask,
  waitForBackgroundTask,
  type BackgroundTaskStatus,
} from "../utils/backgroundTasks";
import ActionButton from "./ActionButton";
import AdvancedDetails from "./AdvancedDetails";
import StatusBadge from "./StatusBadge";
import WarningCallout from "./WarningCallout";
import "../styles/multiple-ligand-docking.css";

type MultipleLigandMember = {
  member_index: number;
  member_id: string;
  display_name: string;
  file: string;
  sha256: string;
  size_bytes: number;
  stats?: {
    atom_count?: number;
    coordinate_count?: number;
    atom_types?: string[];
    torsdof?: number | null;
  };
};

type MultipleLigandScore = {
  mode: number;
  joint_affinity_kcal_mol: number;
  rmsd_lb: number;
  rmsd_ub: number;
  pose_available?: boolean;
};

type MultipleLigandRunResponse = {
  ok: boolean;
  project_dir?: string;
  run_id?: string;
  status?: "prepared" | "running" | "finished" | "failed" | string;
  metadata?: Record<string, unknown>;
  members?: MultipleLigandMember[];
  available_modes?: number[];
  scores?: MultipleLigandScore[];
  message?: string;
  error?: {
    code?: string;
    title?: string;
    message?: string;
    raw_error?: string;
    suggestion?: string;
  } | null;
};

type ScreeningStatusResponse = {
  ok: boolean;
  staged?: unknown;
  import_preview?: unknown;
  message?: string;
  error?: {
    message?: string;
    raw_error?: string;
    suggestion?: string;
  } | null;
};

type BackgroundStartResponse = Omit<Partial<BackgroundTaskStatus>, "error"> & {
  ok: boolean;
  task_id?: string;
  error?: string | { message?: string; raw_error?: string };
};

type MultiLigandDockingPanelProps = {
  projectDir: string;
  receptorFile: string;
  box: DockStartProject["box"];
  vina: DockStartProject["vina"];
  scoringProtocol?: string;
  compatibilityIssues: string[];
  disabled?: boolean;
  disabledReason?: string;
  initialRunId?: string;
  onOpenImport: () => void;
  onOpenResult: (runId: string) => void;
  onStatusChange?: () => void;
};

function parseJson<T>(raw: string): T {
  return JSON.parse(raw) as T;
}

function basename(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : path;
}

function formatBytes(value: number | null | undefined): string {
  if (!value || value <= 0) return "大小未记录";
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 ** 2).toFixed(1)} MB`;
}

function absoluteProjectPath(projectDir: string, path: string): string {
  if (/^(?:[A-Za-z]:[\\/]|\\\\|\/)/.test(path)) return path;
  const separator = projectDir.includes("\\") ? "\\" : "/";
  return `${projectDir.replace(/[\\/]+$/, "")}${separator}${path.replace(/[\\/]+/g, separator)}`;
}

function runTone(status: string): "ok" | "warning" | "error" | "info" | "muted" {
  if (status === "finished") return "ok";
  if (status === "failed") return "error";
  if (status === "interrupted") return "error";
  if (status === "cancelled") return "muted";
  if (status === "running") return "info";
  if (status === "prepared") return "warning";
  return "muted";
}

function runStatusLabel(status: string): string {
  if (status === "prepared") return "已冻结输入";
  if (status === "running") return "共同搜索中";
  if (status === "finished") return "已完成";
  if (status === "failed") return "运行失败";
  if (status === "cancelled") return "已取消";
  if (status === "interrupted") return "意外中断";
  return "尚未创建";
}

function backgroundError(response: BackgroundStartResponse): string {
  if (typeof response.error === "string") return response.error;
  return response.error?.message || response.error?.raw_error || response.message || "后台任务未能启动。";
}

export default function MultiLigandDockingPanel({
  projectDir,
  receptorFile,
  box,
  vina,
  scoringProtocol = "vina",
  compatibilityIssues,
  disabled = false,
  disabledReason = "",
  initialRunId = "",
  onOpenImport,
  onOpenResult,
  onStatusChange,
}: MultiLigandDockingPanelProps) {
  const [preview, setPreview] = useState<LigandImportPreview | null>(null);
  const [selection, setSelection] = useState<string[]>([]);
  const [loadingCandidates, setLoadingCandidates] = useState(false);
  const [run, setRun] = useState<MultipleLigandRunResponse | null>(null);
  const [currentRunId, setCurrentRunId] = useState(initialRunId);
  const [task, setTask] = useState<BackgroundTaskStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const mountedRef = useRef(true);
  const candidateRequestRef = useRef(0);
  const runRequestRef = useRef(0);
  const waitAbortRef = useRef<AbortController | null>(null);
  const reconnectAttemptRef = useRef("");

  const candidates = useMemo(() => readyMultipleLigandCandidates(preview), [preview]);
  const selectedFiles = useMemo(
    () => selectedMultipleLigandFiles(candidates, selection),
    [candidates, selection],
  );
  const selectedInputFiles = useMemo(
    () => selectedFiles.map((path) => absoluteProjectPath(projectDir, path)),
    [projectDir, selectedFiles],
  );
  const selectedCandidates = useMemo(() => {
    const byId = new Map(candidates.map((candidate) => [candidate.id, candidate]));
    return selection
      .map((id) => byId.get(id))
      .filter((candidate): candidate is LigandImportCandidate => Boolean(candidate));
  }, [candidates, selection]);
  const compatible = compatibilityIssues.length === 0;
  const isAd4 = scoringProtocol === "ad4_maps";
  const runStatus = run?.status || "";
  const hasActiveRun = runStatus === "prepared" || runStatus === "running";
  const bestMode = run?.scores?.find((score) => score.pose_available !== false)
    ?? run?.scores?.[0];
  const canCreate = (
    !busy
    && !disabled
    && compatible
    && selectedFiles.length === 2
    && Boolean(receptorFile)
    && !hasActiveRun
  );

  const refreshCandidates = useCallback(async (quiet = false) => {
    const request = ++candidateRequestRef.current;
    setLoadingCandidates(true);
    if (!quiet) {
      setMessage("");
      setRawError("");
    }
    try {
      const response = parseJson<ScreeningStatusResponse>(
        await invoke<string>("get_screening_status", { projectDir }),
      );
      if (request !== candidateRequestRef.current || !mountedRef.current) return;
      if (!response.ok) {
        throw new Error(
          response.error?.message
          || response.error?.raw_error
          || "无法读取已导入的配体。",
        );
      }
      const normalized = normalizeLigandImportPreview(response);
      const ready = readyMultipleLigandCandidates(normalized);
      setPreview(normalized);
      setSelection((current) => normalizeMultipleLigandSelection(ready, current));
      if (!quiet) {
        setMessage(
          ready.length
            ? `已读取 ${ready.length} 个可用于选择的 staging 配体。`
            : "当前没有可用的 staging 配体。",
        );
      }
    } catch (error) {
      if (request === candidateRequestRef.current && mountedRef.current) {
        setRawError(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (request === candidateRequestRef.current && mountedRef.current) {
        setLoadingCandidates(false);
      }
    }
  }, [projectDir]);

  const refreshRun = useCallback(async (runId: string, quiet = false) => {
    if (!runId) return null;
    const request = ++runRequestRef.current;
    try {
      const response = parseJson<MultipleLigandRunResponse>(
        await invoke<string>("get_multiple_ligand_run_status", {
          projectDir,
          runId,
        }),
      );
      if (request !== runRequestRef.current || !mountedRef.current) return response;
      if (!response.ok) {
        throw new Error(response.error?.message || "无法读取共同对接运行状态。");
      }
      setRun(response);
      setCurrentRunId(response.run_id || runId);
      if (!quiet) setMessage(response.message || "共同对接运行状态已刷新。");
      return response;
    } catch (error) {
      if (request === runRequestRef.current && mountedRef.current && !quiet) {
        setRawError(error instanceof Error ? error.message : String(error));
      }
      return null;
    }
  }, [projectDir]);

  useEffect(() => {
    mountedRef.current = true;
    candidateRequestRef.current += 1;
    runRequestRef.current += 1;
    waitAbortRef.current?.abort();
    reconnectAttemptRef.current = "";
    setPreview(null);
    setSelection([]);
    setRun(null);
    setTask(null);
    setCurrentRunId(initialRunId);
    setMessage("");
    setRawError("");
    void refreshCandidates(true);
    if (initialRunId) void refreshRun(initialRunId, true);
    return () => {
      mountedRef.current = false;
      candidateRequestRef.current += 1;
      runRequestRef.current += 1;
      waitAbortRef.current?.abort();
    };
  }, [initialRunId, projectDir, refreshCandidates, refreshRun]);

  useEffect(() => {
    if (runStatus !== "running" || !currentRunId || task) return;
    const timer = window.setInterval(() => {
      void refreshRun(currentRunId, true);
    }, 2_000);
    return () => window.clearInterval(timer);
  }, [currentRunId, refreshRun, runStatus, task]);

  const monitorTask = useCallback(async (
    started: BackgroundTaskStatus,
    runId: string,
    controller: AbortController,
  ) => {
    if (mountedRef.current) setTask(started);
    const finished = await waitForBackgroundTask(
      started.task_id,
      (status) => {
        if (mountedRef.current) setTask(status);
      },
      controller.signal,
    );
    if (!mountedRef.current) return;
    const latest = await refreshRun(runId, true);
    if (finished.status === "failed") {
      throw new Error(
        finished.error
        || latest?.error?.message
        || finished.message
        || "多配体共同对接失败。",
      );
    }
    setMessage(
      finished.status === "cancelled"
        ? "共同对接已取消。"
        : latest?.message || "多配体共同对接已完成。",
    );
    onStatusChange?.();
  }, [onStatusChange, refreshRun]);

  useEffect(() => {
    if (runStatus !== "running" || !currentRunId || task) return;
    if (reconnectAttemptRef.current === currentRunId) return;
    reconnectAttemptRef.current = currentRunId;
    const projectGeneration = candidateRequestRef.current;
    let cancelled = false;
    void findActiveBackgroundTask(projectDir, {
      runId: currentRunId,
      kind: "multiple-ligand",
    }).then(async (active) => {
      if (
        cancelled
        || !mountedRef.current
        || projectGeneration !== candidateRequestRef.current
        || !active
      ) return;
      const controller = new AbortController();
      waitAbortRef.current?.abort();
      waitAbortRef.current = controller;
      setBusy(true);
      setMessage("已重新连接正在运行的多配体共同对接。");
      await monitorTask(active, currentRunId, controller);
    }).catch((error) => {
      if (
        mountedRef.current
        && projectGeneration === candidateRequestRef.current
      ) {
        setRawError(error instanceof Error ? error.message : String(error));
      }
    }).finally(() => {
      if (
        mountedRef.current
        && projectGeneration === candidateRequestRef.current
      ) {
        setBusy(false);
        setTask(null);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [currentRunId, monitorTask, projectDir, runStatus]);

  const startPreparedRun = useCallback(async (runId: string) => {
    if (!runId || busy || !compatible) return;
    setBusy(true);
    setRawError("");
    setMessage("共同对接已提交到后台；离开此页面不会中断运行。");
    const controller = new AbortController();
    waitAbortRef.current?.abort();
    waitAbortRef.current = controller;
    try {
      const started = parseJson<BackgroundStartResponse>(
        await invoke<string>("start_multiple_ligand_task", {
          projectDir,
          runId,
        }),
      );
      if (!started.ok || !started.task_id) throw new Error(backgroundError(started));
      const normalizedTask = started as BackgroundTaskStatus;
      if (mountedRef.current) {
        setRun((current) => current ? { ...current, status: "running" } : current);
      }
      await monitorTask(normalizedTask, runId, controller);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (mountedRef.current) {
        setRawError(error instanceof Error ? error.message : String(error));
        void refreshRun(runId, true);
      }
    } finally {
      if (mountedRef.current) {
        setBusy(false);
        setTask(null);
      }
    }
  }, [busy, compatible, monitorTask, projectDir, refreshRun]);

  const cancelActiveRun = async () => {
    if (
      !currentRunId
      || (task && ["cancelled", "failed", "finished"].includes(task.status))
    ) return;
    setRawError("");
    setMessage("正在请求安全取消共同对接…");
    try {
      if (task?.status === "queued" && task.task_id) {
        const cancelled = await cancelQueuedBackgroundTask(task.task_id);
        if (mountedRef.current) setTask(cancelled);
      } else if (mountedRef.current) {
        setTask((current) => current ? {
          ...current,
          stage: "cancel_pending",
          message: "正在验证并终止本次 Vina 进程…",
          progress_message: "正在验证并终止本次 Vina 进程…",
        } : current);
      }
      const response = parseJson<MultipleLigandRunResponse>(
        await invoke<string>("cancel_multiple_ligand_run", {
          projectDir,
          runId: currentRunId,
        }),
      );
      if (!response.ok) {
        throw new Error(response.error?.message || "共同对接取消请求未被接受。");
      }
      if (mountedRef.current) {
        setRun((current) => ({ ...current, ...response }));
        setMessage(response.message || "共同对接取消请求已登记。");
      }
    } catch (error) {
      if (mountedRef.current) {
        setRawError(error instanceof Error ? error.message : String(error));
      }
    }
  };

  const createAndRun = async () => {
    if (!canCreate) return;
    setBusy(true);
    setRawError("");
    setMessage("正在冻结受体、两个配体、Box 与 Vina 参数…");
    try {
      const prepared = parseJson<MultipleLigandRunResponse>(
        await invoke<string>("prepare_multiple_ligand_run", {
          projectDir,
          ligandFiles: selectedInputFiles,
        }),
      );
      if (!prepared.ok || !prepared.run_id) {
        throw new Error(prepared.error?.message || "无法创建多配体共同对接运行。");
      }
      if (!mountedRef.current) return;
      setRun(prepared);
      setCurrentRunId(prepared.run_id);
      setMessage(prepared.message || `${prepared.run_id} 已冻结输入。`);
      setBusy(false);
      await startPreparedRun(prepared.run_id);
    } catch (error) {
      if (mountedRef.current) {
        setRawError(error instanceof Error ? error.message : String(error));
        setBusy(false);
      }
    }
  };

  const toggleCandidate = (candidateId: string) => {
    const result = toggleMultipleLigandMember(candidates, selection, candidateId);
    setSelection(result.selection);
    setRawError("");
    setMessage(
      result.limitReached
        ? "共同对接固定使用两个成员；请先取消一个已选配体。"
        : "",
    );
  };

  const progress = task?.progress?.percent ?? (runStatus === "finished" ? 100 : 0);
  const progressMessage = task?.progress?.message || task?.message || message;
  const disabledExplanation = compatibilityIssues.length
    ? compatibilityIssues.join("；")
    : disabledReason;

  return (
    <section
      className="run-cockpit-card multiple-ligand-docking-panel"
      aria-labelledby="multiple-ligand-docking-title"
    >
      <div className="run-cockpit-section-heading">
        <div>
          <span className="run-cockpit-kicker">
            {isAd4 ? "实验性协议 · 标准 AD4 maps" : "实验性协议"}
          </span>
          <h2 id="multiple-ligand-docking-title">多配体共同对接</h2>
        </div>
        <StatusBadge tone={runTone(runStatus)}>
          {runStatusLabel(runStatus)}
        </StatusBadge>
      </div>

      <div className="multiple-ligand-definition">
        <Flask aria-hidden="true" size={24} weight="duotone" />
        <div>
          <strong>
            两个配体在同一次{isAd4 ? " AutoDock4 maps" : " Vina/Vinardo"}全局搜索中共同优化
          </strong>
          <p>
            这不是串行批量筛选。每个 Mode 同时包含两个成员，Vina 只给出整个联合体系的一个评分，
            不提供单个成员的独立贡献。
          </p>
        </div>
      </div>

      {disabledExplanation ? (
        <WarningCallout title="当前设置不能运行共同对接">
          <p>{disabledExplanation}</p>
        </WarningCallout>
      ) : null}

      <div className="multiple-ligand-builder">
        <section className="multiple-ligand-candidates" aria-labelledby="multiple-ligand-members-title">
          <header>
            <div>
              <span>01 · 成员</span>
              <h3 id="multiple-ligand-members-title">选择两个 staging 配体</h3>
            </div>
            <strong>{selection.length} / 2</strong>
          </header>

          <div className="multiple-ligand-candidate-toolbar">
            <span>
              可用 {candidates.length}
              {preview && preview.counts.total !== candidates.length
                ? ` · 另有 ${preview.counts.duplicate} 个重复、${preview.counts.invalid} 个失败`
                : ""}
            </span>
            <div>
              <ActionButton
                variant="text"
                disabled={loadingCandidates || busy}
                onClick={() => void refreshCandidates()}
              >
                {loadingCandidates ? <SpinnerGap className="multiple-ligand-spinner" size={15} /> : null}
                重新读取
              </ActionButton>
              <ActionButton variant="text" disabled={busy} onClick={onOpenImport}>
                <FolderOpen size={15} /> 导入配体
              </ActionButton>
            </div>
          </div>

          {candidates.length ? (
            <div className="multiple-ligand-candidate-list">
              {candidates.map((candidate) => {
                const order = selection.indexOf(candidate.id);
                const selected = order >= 0;
                return (
                  <button
                    key={candidate.id}
                    type="button"
                    className={selected ? "is-selected" : ""}
                    aria-pressed={selected}
                    disabled={busy}
                    onClick={() => toggleCandidate(candidate.id)}
                  >
                    <span className="multiple-ligand-candidate-marker">
                      {selected ? order + 1 : ""}
                    </span>
                    <span className="multiple-ligand-candidate-copy">
                      <strong>{candidate.displayName}</strong>
                      <small>
                        {candidate.sourceFormat.toUpperCase()} · {formatBytes(candidate.sizeBytes)}
                        {candidate.sha256 ? ` · ${candidate.sha256.slice(0, 10)}…` : ""}
                      </small>
                      <code>{candidate.stagedFile}</code>
                    </span>
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="multiple-ligand-empty">
              {loadingCandidates ? (
                <><SpinnerGap className="multiple-ligand-spinner" size={20} /> 正在读取配体 staging…</>
              ) : (
                <>
                  <WarningCircle size={20} />
                  <span>尚无可用配体。请先在结构导入页一次导入多个配体。</span>
                </>
              )}
            </div>
          )}
        </section>

        <section className="multiple-ligand-order" aria-labelledby="multiple-ligand-order-title">
          <header>
            <span>02 · 输入顺序</span>
            <h3 id="multiple-ligand-order-title">确认成员 1 与成员 2</h3>
          </header>
          <p>输出中的成员按此顺序保存；顺序会写入 run 快照。</p>
          <ol>
            {[0, 1].map((slot) => {
              const candidate = selectedCandidates[slot];
              return (
                <li key={slot} className={candidate ? "is-filled" : ""}>
                  <span>{slot + 1}</span>
                  <div>
                    <strong>{candidate?.displayName || `请选择成员 ${slot + 1}`}</strong>
                    <small>{candidate?.stagedFile ? basename(candidate.stagedFile) : "等待选择"}</small>
                  </div>
                  {candidate ? (
                    <div className="multiple-ligand-order-actions">
                      <button
                        type="button"
                        aria-label={`将 ${candidate.displayName} 上移`}
                        disabled={slot === 0 || busy}
                        onClick={() => setSelection((current) => moveMultipleLigandMember(current, candidate.id, -1))}
                      >
                        <ArrowUp size={15} />
                      </button>
                      <button
                        type="button"
                        aria-label={`将 ${candidate.displayName} 下移`}
                        disabled={slot === 1 || busy}
                        onClick={() => setSelection((current) => moveMultipleLigandMember(current, candidate.id, 1))}
                      >
                        <ArrowDown size={15} />
                      </button>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ol>
        </section>
      </div>

      <section className="multiple-ligand-protocol-snapshot" aria-labelledby="multiple-ligand-snapshot-title">
        <div>
          <span>03 · 共同运行条件</span>
          <h3 id="multiple-ligand-snapshot-title">当前受体、Box 与 Vina 参数</h3>
        </div>
        <dl>
          <div><dt>受体</dt><dd>{receptorFile || "未导入"}</dd></div>
          <div>
            <dt>Box</dt>
            <dd>
              {box.size_x} × {box.size_y} × {box.size_z} Å
              <small>中心 {box.center_x}, {box.center_y}, {box.center_z}</small>
            </dd>
          </div>
          <div><dt>评分</dt><dd>{vina.scoring === "vinardo" ? "Vinardo" : "Vina"}</dd></div>
          <div>
            <dt>搜索</dt>
            <dd>
              exhaustiveness {vina.exhaustiveness}
              <small>{vina.num_modes} 个候选 Mode · CPU {vina.cpu || "自动"}</small>
            </dd>
          </div>
        </dl>
        {vina.exhaustiveness < 32 ? (
          <p className="multiple-ligand-search-note">
            两个配体会增加联合搜索自由度。官方示例使用 exhaustiveness 32；当前值不会被自动修改，
            应结合体系规模与重复运行收敛性决定。
          </p>
        ) : null}
      </section>

      {run ? (
        <section className={`multiple-ligand-run-state is-${runStatus}`} aria-live="polite">
          <header>
            <div>
              {runStatus === "finished"
                ? <CheckCircle size={21} weight="fill" />
                : runStatus === "failed"
                  ? <WarningCircle size={21} weight="fill" />
                  : <SpinnerGap className={runStatus === "running" ? "multiple-ligand-spinner" : ""} size={21} />}
              <div>
                <strong>{currentRunId} · {runStatusLabel(runStatus)}</strong>
                <span>{run.message || progressMessage}</span>
              </div>
            </div>
            {runStatus === "finished" && currentRunId ? (
              <ActionButton variant="secondary" onClick={() => onOpenResult(currentRunId)}>
                查看联合结果
              </ActionButton>
            ) : null}
          </header>
          {(runStatus === "running" || busy) ? (
            <>
              <div
                className="multiple-ligand-progress"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(progress)}
              >
                <span style={{ width: `${Math.max(0, Math.min(100, progress))}%` }} />
              </div>
              <p>{progressMessage || "正在等待 AutoDock Vina 更新…"}</p>
            </>
          ) : null}
          {bestMode ? (
            <dl className="multiple-ligand-result-summary">
              <div><dt>可查看 Mode</dt><dd>{run.available_modes?.length ?? 0}</dd></div>
              <div><dt>最佳联合评分</dt><dd>{bestMode.joint_affinity_kcal_mol} kcal/mol</dd></div>
              <div><dt>成员数</dt><dd>{run.members?.length ?? 2}</dd></div>
            </dl>
          ) : null}
          {runStatus === "prepared" && currentRunId ? (
            <ActionButton
              variant="primary"
              disabled={busy || !compatible}
              onClick={() => void startPreparedRun(currentRunId)}
            >
              <Play size={17} weight="fill" /> 开始已冻结的共同对接
            </ActionButton>
          ) : null}
          {runStatus === "running" && currentRunId ? (
            <ActionButton
              variant="text"
              disabled={task?.stage === "cancelling" || task?.stage === "cancel_pending"}
              onClick={() => void cancelActiveRun()}
            >
              {task?.stage === "cancelling" || task?.stage === "cancel_pending"
                ? "正在取消…"
                : "取消共同对接"}
            </ActionButton>
          ) : null}
        </section>
      ) : null}

      <div className="multiple-ligand-actions">
        <div>
          <strong>{selectedFiles.length === 2 ? "两个成员已按顺序选定" : "需要恰好选择两个成员"}</strong>
          <span>创建后会复制输入并记录 SHA256；不会覆盖项目当前单配体。</span>
        </div>
        <ActionButton
          variant="primary"
          disabled={!canCreate}
          title={!canCreate ? disabledExplanation || "请选择两个配体并完成运行前检查。" : undefined}
          onClick={() => void createAndRun()}
        >
          {busy ? <SpinnerGap className="multiple-ligand-spinner" size={18} /> : <Play size={18} weight="fill" />}
          {busy ? "共同对接运行中…" : "创建并开始共同对接"}
        </ActionButton>
      </div>

      {message && !run ? <p className="multiple-ligand-message" role="status">{message}</p> : null}
      {rawError ? (
        <WarningCallout title="共同对接操作未完成">
          <p>{rawError}</p>
          {run?.error?.suggestion ? <small>{run.error.suggestion}</small> : null}
          {run?.error?.raw_error ? (
            <AdvancedDetails summary="查看原始诊断">
              <pre>{run.error.raw_error}</pre>
            </AdvancedDetails>
          ) : null}
        </WarningCallout>
      ) : null}
    </section>
  );
}
