import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { CheckCircle, FolderOpen, Play, SpinnerGap, Stop, TrayArrowDown } from "@phosphor-icons/react";

import type { DockStartProject } from "../types";
import { startScreeningTask, waitForBackgroundTask } from "../utils/backgroundTasks";
import ActionButton from "./ActionButton";
import AdvancedDetails from "./AdvancedDetails";
import StatusBadge from "./StatusBadge";
import "../styles/batch-screening.css";

type ScreeningItem = {
  item_id: string;
  source_file?: string;
  status: string;
  attempt_count?: number;
  best_affinity_kcal_mol?: number | null;
  last_error?: string;
};

type ScreeningState = {
  status: string;
  queue?: string[];
  items?: ScreeningItem[];
  top_n?: number;
  max_retries?: number;
  outputs?: {
    summary_csv?: string;
    top_n_csv?: string;
    sdf?: { generated?: boolean; file?: string; reason?: string };
  };
  tools?: { vina?: { version?: string; source?: string; sha256?: string } };
};

type ScreeningResponse = {
  ok: boolean;
  screening?: ScreeningState;
  staged?: Array<{ file: string; original_name?: string; source_file?: string }>;
  message?: string;
  error?: { code?: string; message?: string; raw_error?: string; suggestion?: string };
};

type BatchScreeningPanelProps = {
  projectDir: string;
  receptorFile: string;
  box: DockStartProject["box"];
  vina: DockStartProject["vina"];
  disabled?: boolean;
  disabledReason?: string;
  onBatchModeDetected?: () => void;
};

function parseResponse(raw: string): ScreeningResponse {
  return JSON.parse(raw) as ScreeningResponse;
}

function terminal(status: string): boolean {
  return ["completed", "completed_with_failures", "canceled"].includes(status);
}

function tone(status: string): "ok" | "warning" | "error" | "info" | "muted" {
  if (status === "completed") return "ok";
  if (["completed_with_failures", "canceled", "cancel_requested", "interrupted"].includes(status)) return "warning";
  if (status === "running") return "info";
  if (status === "failed") return "error";
  return "muted";
}

const statusLabels: Record<string, string> = {
  ready: "已就绪",
  running: "运行中",
  cancel_requested: "等待安全取消",
  canceled: "已取消",
  interrupted: "已中断",
  completed: "已完成",
  completed_with_failures: "完成（含失败项）",
};

export default function BatchScreeningPanel({
  projectDir,
  receptorFile,
  box,
  vina,
  disabled = false,
  disabledReason = "",
  onBatchModeDetected,
}: BatchScreeningPanelProps) {
  const [state, setState] = useState<ScreeningState | null>(null);
  const [stagedFiles, setStagedFiles] = useState<string[]>([]);
  const [stagedLabels, setStagedLabels] = useState<Record<string, string>>({});
  const [maxRetries, setMaxRetries] = useState(1);
  const [topN, setTopN] = useState(20);
  const [cpuPerTask, setCpuPerTask] = useState(Math.max(1, vina.cpu || 1));
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");

  const refresh = useCallback(async (quiet = false) => {
    try {
      const parsed = parseResponse(await invoke<string>("get_screening_status", { projectDir }));
      const staged = (parsed.staged ?? []).map((item) => item.file).filter(Boolean);
      if (staged.length) setStagedFiles(staged);
      if (parsed.staged?.length) {
        setStagedLabels(Object.fromEntries(parsed.staged.map((item) => [
          item.file,
          item.original_name || item.source_file?.split(/[\\/]/).pop() || item.file.split(/[\\/]/).pop() || item.file,
        ])));
      }
      if (staged.length > 1 || parsed.screening) onBatchModeDetected?.();
      if (parsed.ok && parsed.screening) {
        setState(parsed.screening);
        if (!quiet) setMessage(parsed.message || "批量筛选状态已刷新。");
      } else if (!parsed.ok) {
        setRawError(parsed.error?.raw_error || parsed.error?.message || "无法读取批量筛选状态。");
      }
    } catch (error) {
      if (!quiet) setRawError(error instanceof Error ? error.message : String(error));
    }
  }, [onBatchModeDetected, projectDir]);

  useEffect(() => {
    setState(null);
    setStagedFiles([]);
    setStagedLabels({});
    setMessage("");
    setRawError("");
    void refresh(true);
  }, [projectDir, refresh]);

  const counts = useMemo(() => {
    const items = state?.items ?? [];
    return {
      total: items.length,
      succeeded: items.filter((item) => item.status === "succeeded").length,
      failed: items.filter((item) => item.status === "failed").length,
      pending: items.filter((item) => ["pending", "running", "interrupted"].includes(item.status)).length,
    };
  }, [state]);

  const rankedItems = useMemo(
    () => [...(state?.items ?? [])]
      .filter((item) => item.status === "succeeded" && typeof item.best_affinity_kcal_mol === "number")
      .sort((left, right) => Number(left.best_affinity_kcal_mol) - Number(right.best_affinity_kcal_mol))
      .slice(0, Math.min(state?.top_n ?? 20, 10)),
    [state],
  );

  const chooseLigands = async () => {
    const selected = await open({
      multiple: true,
      directory: false,
      title: "选择一个或多个配体",
      filters: [{ name: "配体结构", extensions: ["pdbqt", "sdf", "mol"] }],
    });
    const files = Array.isArray(selected) ? selected : selected ? [selected] : [];
    if (!files.length) return;
    setBusy(true);
    setRawError("");
    try {
      const parsed = parseResponse(await invoke<string>("stage_screening_inputs", { projectDir, files }));
      if (!parsed.ok) throw new Error(parsed.error?.message || "配体导入失败。");
      const staged = (parsed.staged ?? []).map((item) => item.file).filter(Boolean);
      setStagedFiles(staged);
      setStagedLabels(Object.fromEntries((parsed.staged ?? []).map((item) => [
        item.file,
        item.original_name || item.source_file?.split(/[\\/]/).pop() || item.file.split(/[\\/]/).pop() || item.file,
      ])));
      if (staged.length > 1) onBatchModeDetected?.();
      setMessage(`已导入 ${staged.length} 个配体快照${files.some((file) => !file.toLowerCase().endsWith(".pdbqt")) ? "，原始结构已自动准备为 PDBQT" : ""}；尚未开始对接。`);
    } catch (error) {
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const create = async (): Promise<boolean> => {
    if (!receptorFile || !stagedFiles.length) return false;
    setBusy(true);
    setRawError("");
    try {
      const parsed = parseResponse(await invoke<string>("create_screening", {
        projectDir,
        receptorFile,
        ligandFiles: stagedFiles,
        boxJson: JSON.stringify(box),
        vinaJson: JSON.stringify({ ...vina, cpu: cpuPerTask }),
        maxRetries,
        topN,
      }));
      if (!parsed.ok || !parsed.screening) throw new Error(parsed.error?.message || "批量筛选任务创建失败。");
      setState(parsed.screening);
      setMessage(parsed.message || "批量筛选任务已创建。");
      return true;
    } catch (error) {
      setRawError(error instanceof Error ? error.message : String(error));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const createAndRun = async () => {
    if (await create()) await run();
  };

  const run = async () => {
    setBusy(true);
    setRawError("");
    let timer: number | null = null;
    try {
      const task = await startScreeningTask(projectDir);
      setMessage("批量筛选已进入后台串行队列。离开当前页面不会中断任务。");
      timer = window.setInterval(() => void refresh(true), 1500);
      const finished = await waitForBackgroundTask(task.task_id);
      if (finished.status === "failed") throw new Error(finished.error || finished.message || "批量筛选失败。");
      await refresh(true);
      setMessage(finished.status === "cancelled" ? "批量筛选已安全取消。" : "批量筛选队列已结束。");
    } catch (error) {
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      if (timer !== null) window.clearInterval(timer);
      setBusy(false);
      void refresh(true);
    }
  };

  const invokeStateCommand = async (command: "request_screening_cancel" | "resume_screening" | "archive_screening") => {
    setBusy(command !== "request_screening_cancel");
    setRawError("");
    try {
      const parsed = parseResponse(await invoke<string>(command, { projectDir }));
      if (!parsed.ok) throw new Error(parsed.error?.message || "批量筛选操作失败。");
      setState(parsed.screening ?? null);
      if (command === "archive_screening") setStagedFiles([]);
      setMessage(parsed.message || "操作已完成。");
    } catch (error) {
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
      void refresh(true);
    }
  };

  return (
    <section className="run-cockpit-card batch-screening-panel" aria-labelledby="batch-screening-title">
      <div className="run-cockpit-section-heading">
        <div>
          <span className="run-cockpit-kicker">多配体任务</span>
          <h2 id="batch-screening-title">配体队列与批量运行</h2>
        </div>
        <StatusBadge tone={tone(state?.status || "idle")}>{state ? statusLabels[state.status] || state.status : "尚未创建"}</StatusBadge>
      </div>

      <div className="batch-screening-content">
        <p className="batch-screening-intro">全部配体共用上方受体、Box 与 Vina 参数，并按可恢复队列依次运行。CPU 表示每个配体任务的线程数。</p>

        {state ? (
          <>
            <div className="batch-screening-metrics">
              <div><span>总数</span><strong>{counts.total}</strong></div>
              <div><span>成功</span><strong>{counts.succeeded}</strong></div>
              <div><span>待处理</span><strong>{counts.pending}</strong></div>
              <div><span>失败</span><strong>{counts.failed}</strong></div>
            </div>
            <div className="batch-screening-actions">
              {state.status === "ready" ? <ActionButton variant="primary" disabled={busy || disabled} onClick={() => void run()}><Play size={16} weight="fill" />开始多配体对接</ActionButton> : null}
              {state.status === "running" || state.status === "cancel_requested" ? <ActionButton variant="secondary" disabled={state.status === "cancel_requested"} onClick={() => void invokeStateCommand("request_screening_cancel")}><Stop size={16} />{state.status === "cancel_requested" ? "等待当前配体结束" : "安全取消"}</ActionButton> : null}
              {["canceled", "interrupted"].includes(state.status) ? <ActionButton variant="primary" disabled={busy || disabled} onClick={() => void invokeStateCommand("resume_screening")}><Play size={16} />恢复队列</ActionButton> : null}
              {terminal(state.status) ? <ActionButton variant="secondary" disabled={busy || disabled} onClick={() => void invokeStateCommand("archive_screening")}><TrayArrowDown size={16} />归档本次筛选</ActionButton> : null}
              <ActionButton variant="text" disabled={busy} onClick={() => void refresh()}>{busy ? <SpinnerGap className="run-monitor-spinner" size={15} /> : null}刷新状态</ActionButton>
            </div>
            {state.outputs?.summary_csv ? (
              <div className="batch-screening-output">
                <CheckCircle aria-hidden="true" size={18} weight="fill" />
                <div><strong>结果汇总已写入项目</strong><code>{state.outputs.summary_csv}</code><code>{state.outputs.top_n_csv}</code></div>
              </div>
            ) : null}
            {rankedItems.length ? (
              <div className="batch-screening-results" aria-label="批量筛选最佳结果">
                <div><span>排名</span><span>配体</span><span>最佳评分</span></div>
                {rankedItems.map((item, index) => (
                  <div key={item.item_id}>
                    <strong>{index + 1}</strong>
                    <span title={item.source_file || item.item_id}>{stagedLabels[item.source_file || ""] || item.source_file?.split(/[\\/]/).pop() || item.item_id}</span>
                    <code>{item.best_affinity_kcal_mol?.toFixed(3)} kcal/mol</code>
                  </div>
                ))}
              </div>
            ) : null}
            {state.outputs?.sdf && !state.outputs.sdf.generated ? <p className="batch-screening-boundary">未生成 SDF：{state.outputs.sdf.reason}</p> : null}
          </>
        ) : (
          <>
            <div className="batch-screening-library">
              <div>
                <span>配体库</span>
                <strong>{stagedFiles.length ? `已导入 ${stagedFiles.length} 个配体` : "尚未导入配体"}</strong>
                <small>{stagedFiles.length ? "队列会使用上方已经保存的受体、Box 与 Vina 参数。" : "可从项目创建、PDBQT 导入或原始结构导入页面一次选择多个配体。"}</small>
              </div>
              <ActionButton variant="secondary" disabled={disabled || busy} onClick={() => void chooseLigands()}><FolderOpen size={16} />{stagedFiles.length ? "重新选择配体" : "选择多个配体"}</ActionButton>
            </div>
            <div className="batch-screening-create-grid">
              <label><span>单任务 CPU</span><input type="number" min={1} max={64} value={cpuPerTask} disabled={disabled || busy} onChange={(event) => setCpuPerTask(Math.max(1, Number(event.target.value) || 1))} /><small>每次只运行一个配体</small></label>
              <label><span>失败重试</span><input type="number" min={0} max={10} value={maxRetries} disabled={disabled || busy} onChange={(event) => setMaxRetries(Math.max(0, Number(event.target.value) || 0))} /><small>只重试失败项</small></label>
              <label><span>汇总 Top N</span><input type="number" min={1} max={10000} value={topN} disabled={disabled || busy} onChange={(event) => setTopN(Math.max(1, Number(event.target.value) || 1))} /><small>用于结果排行</small></label>
            </div>
            <div className="batch-screening-actions">
              <ActionButton variant="secondary" disabled={disabled || busy || !receptorFile || !stagedFiles.length} onClick={() => void create()}>仅创建队列</ActionButton>
              <ActionButton variant="primary" disabled={disabled || busy || !receptorFile || !stagedFiles.length} onClick={() => void createAndRun()}><Play size={16} weight="fill" />创建并开始对接</ActionButton>
            </div>
          </>
        )}

        {disabled && disabledReason ? <p className="batch-screening-boundary">{disabledReason}</p> : null}
        {message ? <p className="batch-screening-message" role="status">{message}</p> : null}
        {rawError ? <AdvancedDetails summary="查看批量筛选诊断"><pre>{rawError}</pre></AdvancedDetails> : null}
      </div>
    </section>
  );
}
