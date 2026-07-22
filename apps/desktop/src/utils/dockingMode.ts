export type DockingWorkspaceMode = "single" | "batch";

function key(projectDir: string): string {
  return `dockstart:docking-workspace:${projectDir}`;
}

export function readDockingWorkspaceMode(projectDir: string): DockingWorkspaceMode {
  try {
    return window.localStorage.getItem(key(projectDir)) === "batch" ? "batch" : "single";
  } catch {
    return "single";
  }
}

export function writeDockingWorkspaceMode(projectDir: string, mode: DockingWorkspaceMode): void {
  try {
    window.localStorage.setItem(key(projectDir), mode);
  } catch {
    // Local preference failure must not block scientific work.
  }
}
