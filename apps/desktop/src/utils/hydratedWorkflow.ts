import type {
  DockStartProject,
  HydratedRunPreflightSuccess,
  HydratedStatusSuccess,
} from "../types";

export const HYDRATED_PROTOCOL_ID = "hydrated_ad4_experimental";

export function isHydratedProtocolId(value: unknown): boolean {
  return value === HYDRATED_PROTOCOL_ID;
}

export type HydratedRunSummary = {
  runId: string;
  status: string;
  stage: string;
  createdAt: string;
};

export type HydratedWorkflowAvailability = {
  rawLigandSupported: boolean;
  canPrepareLigand: boolean;
  canGenerateMaps: boolean;
  canCheckRun: boolean;
  canPrepareRun: boolean;
  canLoadResults: boolean;
  reasons: {
    prepareLigand: string;
    generateMaps: string;
    checkRun: string;
    prepareRun: string;
    loadResults: string;
  };
};

function recordString(
  record: Record<string, unknown>,
  key: string,
): string {
  const value = record[key];
  return typeof value === "string" ? value : "";
}

function isHydratedRecord(record: Record<string, unknown>): boolean {
  if (isHydratedProtocolId(recordString(record, "protocol_id"))) {
    return true;
  }
  const protocol = record.docking_protocol;
  return Boolean(
    protocol
    && typeof protocol === "object"
    && !Array.isArray(protocol)
    && isHydratedProtocolId(
      recordString(protocol as Record<string, unknown>, "protocol_id"),
    ),
  );
}

export function hydratedRunSummaries(
  project: DockStartProject,
): HydratedRunSummary[] {
  return project.runs
    .filter(isHydratedRecord)
    .map((record) => ({
      runId: recordString(record, "run_id"),
      status: recordString(record, "status") || "unknown",
      stage: recordString(record, "stage"),
      createdAt: recordString(record, "created_at"),
    }))
    .filter((run) => run.runId);
}

export function resolveHydratedRun(
  project: DockStartProject,
  preferredRunId = "",
): HydratedRunSummary | null {
  const runs = hydratedRunSummaries(project);
  const preferred = runs.find((run) => run.runId === preferredRunId);
  return preferred ?? runs[runs.length - 1] ?? null;
}

export function isSupportedHydratedRawLigand(path: string): boolean {
  return /\.(sdf|mol)$/i.test(path.trim());
}

export function getHydratedWorkflowAvailability(input: {
  project: DockStartProject;
  status: HydratedStatusSuccess | null;
  preflight: HydratedRunPreflightSuccess | null;
  run: HydratedRunSummary | null;
  busy: boolean;
}): HydratedWorkflowAvailability {
  const { project, status, preflight, run, busy } = input;
  const rawLigandSupported = isSupportedHydratedRawLigand(
    project.ligand.raw_file,
  );
  const preparationReady = Boolean(
    status?.preparation_ready && status.valid,
  );
  const mapsReady = Boolean(status?.maps_ready && status.maps_valid);
  const guardBlocked = preflight?.active_run_guard?.blocked === true;
  const preflightReady = (
    preflight as (HydratedRunPreflightSuccess & { ready?: boolean }) | null
  )?.ready !== false;

  return {
    rawLigandSupported,
    canPrepareLigand: rawLigandSupported && !busy,
    canGenerateMaps: preparationReady && !busy,
    canCheckRun: preparationReady && mapsReady && !busy,
    canPrepareRun: Boolean(
      preflight?.ok
      && preflightReady
      && !guardBlocked
      && !busy,
    ),
    canLoadResults: run?.status === "finished" && !busy,
    reasons: {
      prepareLigand: busy
        ? "请等待当前操作完成。"
        : rawLigandSupported
          ? ""
          : "请先为当前单配体保留 SDF 或 MOL 原始文件。",
      generateMaps: busy
        ? "请等待当前操作完成。"
        : preparationReady
          ? ""
          : "请先准备并校验水合配体。",
      checkRun: busy
        ? "请等待当前操作完成。"
        : !preparationReady
          ? "请先准备并校验水合配体。"
          : mapsReady
            ? ""
            : "请先生成并校验完整的水合 AD4 maps。",
      prepareRun: busy
        ? "请等待当前操作完成。"
        : guardBlocked
          ? "当前项目已有未完成的 Vina 运行，请先回到该运行记录处理。"
          : !preflight?.ok || !preflightReady
            ? "请先完成运行前检查并处理阻塞项。"
            : "",
      loadResults: busy
        ? "请等待当前操作完成。"
        : !run
          ? "尚未创建水合 AD4 run。"
          : run.status === "finished"
            ? ""
            : "水合 run 完成后才能读取水分子分类结果。",
    },
  };
}
