import type { BackgroundTaskStatus } from "./backgroundTasks";

const PROJECT_MUTATING_TASK_KINDS = new Set([
  "structure-fetch",
  "preparation",
  "vina",
]);

const TERMINAL_TASK_STATUSES = new Set([
  "finished",
  "failed",
  "cancelled",
]);

function normalizedProjectDir(projectDir: string): string {
  return projectDir
    .trim()
    .replace(/[\\/]+/g, "/")
    .replace(/\/+$/, "")
    .toLowerCase();
}

export function isSameProjectDir(left: string, right: string): boolean {
  return normalizedProjectDir(left) === normalizedProjectDir(right);
}

export function shouldRefreshProjectAfterTask(
  status: BackgroundTaskStatus,
  currentProjectDir: string,
): boolean {
  return status.ok
    && TERMINAL_TASK_STATUSES.has(status.status)
    && PROJECT_MUTATING_TASK_KINDS.has(status.kind)
    && isSameProjectDir(status.project_dir, currentProjectDir);
}

/**
 * Collapses adjacent terminal task events and serializes refreshes. A task that
 * finishes while a refresh is in flight requests one follow-up read, rather
 * than starting another backend process concurrently.
 */
export class DebouncedProjectRefresh {
  private readonly refresh: () => Promise<void>;

  private readonly delayMs: number;

  private timer: ReturnType<typeof setTimeout> | null = null;

  private refreshInFlight = false;

  private refreshQueued = false;

  private disposed = false;

  constructor(refresh: () => Promise<void>, delayMs = 180) {
    this.refresh = refresh;
    this.delayMs = delayMs;
  }

  request(): void {
    if (this.disposed) return;
    if (this.refreshInFlight) {
      this.refreshQueued = true;
      return;
    }
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.flush();
    }, this.delayMs);
  }

  dispose(): void {
    this.disposed = true;
    this.refreshQueued = false;
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  private async flush(): Promise<void> {
    if (this.disposed || this.refreshInFlight) return;
    this.refreshInFlight = true;
    try {
      await this.refresh();
    } finally {
      this.refreshInFlight = false;
      if (this.refreshQueued && !this.disposed) {
        this.refreshQueued = false;
        this.request();
      }
    }
  }
}
