import assert from "node:assert/strict";
import test from "node:test";
import type { BackgroundTaskStatus } from "../src/utils/backgroundTasks.ts";
import {
  DebouncedProjectRefresh,
  shouldRefreshProjectAfterTask,
} from "../src/utils/backgroundProjectRefresh.ts";

function task(overrides: Partial<BackgroundTaskStatus> = {}): BackgroundTaskStatus {
  return {
    ok: true,
    task_id: "task-1",
    kind: "preparation",
    status: "finished",
    stage: "finished",
    project_dir: "E:\\DockStart\\demo",
    run_id: "",
    target: "receptor",
    deduplicated: false,
    progress: { percent: 100, message: "" },
    elapsed_seconds: 1,
    message: "",
    stdout_tail: "",
    stderr_tail: "",
    log_tail: "",
    result_json: "",
    error: "",
    ...overrides,
  };
}

const wait = (milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds));

test("only terminal project-mutating tasks for the current project request refresh", () => {
  assert.equal(shouldRefreshProjectAfterTask(task(), "e:/dockstart/demo/"), true);
  assert.equal(shouldRefreshProjectAfterTask(task({ kind: "structure-fetch" }), "E:\\DockStart\\demo"), true);
  assert.equal(shouldRefreshProjectAfterTask(task({ kind: "vina" }), "E:\\DockStart\\demo"), true);
  assert.equal(shouldRefreshProjectAfterTask(task({ status: "running" }), "E:\\DockStart\\demo"), false);
  assert.equal(shouldRefreshProjectAfterTask(task({ status: "failed" }), "E:\\DockStart\\demo"), true);
  assert.equal(shouldRefreshProjectAfterTask(task({ status: "cancelled" }), "E:\\DockStart\\demo"), true);
  assert.equal(shouldRefreshProjectAfterTask(task({ kind: "candidate-preview" }), "E:\\DockStart\\demo"), false);
  assert.equal(shouldRefreshProjectAfterTask(task(), "E:\\DockStart\\other"), false);
});

test("adjacent task completions are debounced into one project refresh", async () => {
  let refreshCount = 0;
  let markRefreshed: (() => void) | null = null;
  const refreshed = new Promise<void>((resolve) => {
    markRefreshed = resolve;
  });
  const refresh = new DebouncedProjectRefresh(async () => {
    refreshCount += 1;
    markRefreshed?.();
  }, 10);

  refresh.request();
  refresh.request();
  refresh.request();
  await refreshed;
  await wait(20);

  assert.equal(refreshCount, 1);
  refresh.dispose();
});

test("a completion during an in-flight refresh queues one serial follow-up", async () => {
  let refreshCount = 0;
  let concurrent = 0;
  let maxConcurrent = 0;
  let releaseFirst: (() => void) | null = null;
  let markFirstStarted: (() => void) | null = null;
  let markSecondFinished: (() => void) | null = null;
  const firstRefresh = new Promise<void>((resolve) => {
    releaseFirst = resolve;
  });
  const firstStarted = new Promise<void>((resolve) => {
    markFirstStarted = resolve;
  });
  const secondFinished = new Promise<void>((resolve) => {
    markSecondFinished = resolve;
  });
  const refresh = new DebouncedProjectRefresh(async () => {
    refreshCount += 1;
    concurrent += 1;
    maxConcurrent = Math.max(maxConcurrent, concurrent);
    if (refreshCount === 1) {
      markFirstStarted?.();
      await firstRefresh;
    }
    concurrent -= 1;
    if (refreshCount === 2) markSecondFinished?.();
  }, 5);

  refresh.request();
  await firstStarted;
  refresh.request();
  refresh.request();
  releaseFirst?.();
  await secondFinished;

  assert.equal(refreshCount, 2);
  assert.equal(maxConcurrent, 1);
  refresh.dispose();
});

test("disposing the scheduler cancels a pending refresh", async () => {
  let refreshCount = 0;
  const refresh = new DebouncedProjectRefresh(async () => {
    refreshCount += 1;
  }, 10);

  refresh.request();
  refresh.dispose();
  await wait(25);

  assert.equal(refreshCount, 0);
});
