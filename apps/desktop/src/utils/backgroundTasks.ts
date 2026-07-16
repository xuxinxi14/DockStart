import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export const BACKGROUND_TASK_EVENT = "dockstart-background-task";

export type BackgroundTaskStatus = {
  ok: boolean;
  task_id: string;
  kind: "preparation" | "vina" | string;
  status: "queued" | "running" | "finished" | "failed" | "cancelled" | string;
  stage: string;
  project_dir: string;
  run_id: string;
  target: string;
  deduplicated: boolean;
  progress: {
    percent: number;
    message: string;
  };
  elapsed_seconds: number;
  message: string;
  stdout_tail: string;
  stderr_tail: string;
  log_tail: string;
  result_json: string;
  error: string;
};

function normalizeTaskStatus(rawPayload: unknown): BackgroundTaskStatus {
  const decoded = typeof rawPayload === "string" ? JSON.parse(rawPayload) : rawPayload;
  if (!decoded || typeof decoded !== "object") {
    throw new Error("后台任务返回了无法识别的状态数据。");
  }
  const parsed = decoded as Omit<Partial<BackgroundTaskStatus>, "error"> & {
    error?: string | { message?: string; raw_error?: string };
  };
  const error = typeof parsed.error === "string"
    ? parsed.error
    : [parsed.error?.message, parsed.error?.raw_error].filter(Boolean).join("\n");
  return {
    ok: Boolean(parsed.ok),
    task_id: parsed.task_id ?? "",
    kind: parsed.kind ?? "unknown",
    status: parsed.status ?? "unknown",
    stage: parsed.stage ?? parsed.status ?? "unknown",
    project_dir: parsed.project_dir ?? "",
    run_id: parsed.run_id ?? "",
    target: parsed.target ?? "",
    deduplicated: Boolean(parsed.deduplicated),
    progress: {
      percent: Math.max(0, Math.min(100, Number(parsed.progress?.percent ?? 0))),
      message: parsed.progress?.message ?? parsed.message ?? "",
    },
    elapsed_seconds: Number(parsed.elapsed_seconds ?? 0),
    message: parsed.message ?? "",
    stdout_tail: parsed.stdout_tail ?? "",
    stderr_tail: parsed.stderr_tail ?? "",
    log_tail: parsed.log_tail ?? "",
    result_json: parsed.result_json ?? "",
    error,
  };
}

function assertStarted(status: BackgroundTaskStatus): BackgroundTaskStatus {
  if (!status.ok || !status.task_id) {
    throw new Error(status.message || status.error || "后台任务未能启动。");
  }
  return status;
}

export async function startPreparationTask(
  projectDir: string,
  target: "receptor" | "ligand",
  overwrite: boolean,
): Promise<BackgroundTaskStatus> {
  const payload = await invoke<string>("start_preparation_task", { projectDir, target, overwrite });
  return assertStarted(normalizeTaskStatus(payload));
}

export async function startVinaRunTask(projectDir: string, runId: string): Promise<BackgroundTaskStatus> {
  const payload = await invoke<string>("start_vina_run_task", { projectDir, runId });
  return assertStarted(normalizeTaskStatus(payload));
}

export async function startPdbFetchTask(
  projectDir: string,
  pdbId: string,
  format: string,
  overwrite: boolean,
): Promise<BackgroundTaskStatus> {
  const payload = await invoke<string>("start_pdb_fetch_task", { projectDir, pdbId, format, overwrite });
  return assertStarted(normalizeTaskStatus(payload));
}

export async function startPubchemFetchTask(
  projectDir: string,
  query: string,
  queryType: "cid" | "name" | "smiles",
  format: string,
  overwrite: boolean,
): Promise<BackgroundTaskStatus> {
  const payload = await invoke<string>("start_pubchem_fetch_task", {
    projectDir,
    query,
    queryType,
    format,
    overwrite,
  });
  return assertStarted(normalizeTaskStatus(payload));
}

export async function getBackgroundTaskStatus(taskId: string): Promise<BackgroundTaskStatus> {
  const payload = await invoke<string>("get_background_task_status", { taskId });
  const status = normalizeTaskStatus(payload);
  if (!status.ok) throw new Error(status.message || status.error || "无法读取后台任务状态。");
  return status;
}

export async function findActiveBackgroundTask(
  projectDir: string,
  filters: { runId?: string; target?: string; kind?: string } = {},
): Promise<BackgroundTaskStatus | null> {
  const payload = await invoke<string>("find_active_background_task", {
    projectDir,
    runId: filters.runId,
    target: filters.target,
    kind: filters.kind,
  });
  const status = normalizeTaskStatus(payload);
  if (status.ok) return status;
  if (status.error === "ACTIVE_TASK_NOT_FOUND") return null;
  throw new Error(status.message || status.error || "无法查询活动后台任务。");
}

export async function cancelQueuedBackgroundTask(taskId: string): Promise<BackgroundTaskStatus> {
  const payload = await invoke<string>("cancel_background_task", { taskId });
  const status = normalizeTaskStatus(payload);
  if (!status.ok) throw new Error(status.message || status.error || "无法取消排队任务。");
  return status;
}

export function isTerminalBackgroundTask(status: BackgroundTaskStatus): boolean {
  return status.status === "finished" || status.status === "failed" || status.status === "cancelled";
}

export async function listenForBackgroundTaskUpdates(
  onUpdate: (status: BackgroundTaskStatus) => void,
  onError?: (error: Error) => void,
): Promise<UnlistenFn> {
  return listen<unknown>(BACKGROUND_TASK_EVENT, (event) => {
    try {
      onUpdate(normalizeTaskStatus(event.payload));
    } catch (error) {
      onError?.(error instanceof Error ? error : new Error(String(error)));
    }
  });
}

function mergeMonotonicTaskStatus(
  previous: BackgroundTaskStatus | null,
  observed: BackgroundTaskStatus,
): BackgroundTaskStatus {
  if (!previous || previous.task_id !== observed.task_id) return observed;
  if (isTerminalBackgroundTask(previous)) return previous;
  const previousCancelling = previous.stage === "cancel_pending" || previous.stage === "cancelling";
  const observedRegressesCancellation = previousCancelling
    && observed.status === "running"
    && (observed.stage === "starting" || observed.stage === "running");
  if (!observedRegressesCancellation) {
    return {
      ...observed,
      progress: {
        ...observed.progress,
        percent: Math.max(previous.progress.percent, observed.progress.percent),
      },
    };
  }
  return {
    ...observed,
    stage: "cancelling",
    message: previous.message || observed.message,
    progress: {
      percent: Math.max(previous.progress.percent, observed.progress.percent),
      message: previous.progress.message || observed.progress.message,
    },
  };
}

/**
 * Native events provide immediate progress. A low-frequency watchdog reads only
 * the in-memory Rust registry, so a dropped window event cannot strand the UI.
 */
export async function waitForBackgroundTask(
  taskId: string,
  onUpdate?: (status: BackgroundTaskStatus) => void,
  signal?: AbortSignal,
): Promise<BackgroundTaskStatus> {
  let unlisten: UnlistenFn | null = null;
  let watchdogTimer: number | null = null;
  let timeoutTimer: number | null = null;
  let watchdogInFlight = false;
  let settled = false;
  let latestStatus: BackgroundTaskStatus | null = null;
  let resolveCompletion: (status: BackgroundTaskStatus) => void = () => undefined;
  let rejectCompletion: (error: Error) => void = () => undefined;
  const completion = new Promise<BackgroundTaskStatus>((resolve, reject) => {
    resolveCompletion = resolve;
    rejectCompletion = reject;
  });

  const finish = (observed: BackgroundTaskStatus) => {
    if (settled) return;
    const status = mergeMonotonicTaskStatus(latestStatus, observed);
    latestStatus = status;
    onUpdate?.(status);
    if (!isTerminalBackgroundTask(status)) return;
    settled = true;
    resolveCompletion(status);
  };

  const fail = (error: unknown) => {
    if (settled) return;
    settled = true;
    rejectCompletion(error instanceof Error ? error : new Error(String(error)));
  };

  const onAbort = () => fail(new DOMException("页面已离开，停止等待后台任务事件。", "AbortError"));
  const refreshFromRegistry = async () => {
    if (settled || watchdogInFlight) return;
    watchdogInFlight = true;
    try {
      finish(await getBackgroundTaskStatus(taskId));
    } catch (error) {
      fail(error);
    } finally {
      watchdogInFlight = false;
    }
  };
  try {
    if (signal?.aborted) onAbort();
    signal?.addEventListener("abort", onAbort, { once: true });
    unlisten = await listen<unknown>(BACKGROUND_TASK_EVENT, (event) => {
      try {
        const status = normalizeTaskStatus(event.payload);
        if (status.task_id === taskId) finish(status);
      } catch (error) {
        fail(error);
      }
    });
    await refreshFromRegistry();
    if (!settled) watchdogTimer = window.setInterval(() => void refreshFromRegistry(), 4_000);
    timeoutTimer = window.setTimeout(
      () => fail(new Error("后台任务等待超过 12 小时，请重新打开项目检查运行记录。")),
      12 * 60 * 60 * 1_000,
    );
    return await completion;
  } finally {
    signal?.removeEventListener("abort", onAbort);
    if (watchdogTimer !== null) window.clearInterval(watchdogTimer);
    if (timeoutTimer !== null) window.clearTimeout(timeoutTimer);
    unlisten?.();
  }
}
