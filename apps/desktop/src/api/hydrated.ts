import { invoke } from "@tauri-apps/api/core";
import type {
  HydratedMapsGenerationResponse,
  HydratedMapsOptions,
  HydratedResultsResponse,
  HydratedRunPreflightResponse,
  HydratedRunPrepareResponse,
  HydratedStatusResponse,
} from "../types";

export type HydratedInvoke = (
  command: string,
  args?: Record<string, unknown>,
) => Promise<string>;

export type HydratedDesktopApi = {
  getStatus(projectDir: string): Promise<HydratedStatusResponse>;
  prepareLigand(projectDir: string): Promise<HydratedStatusResponse>;
  generateMaps(
    projectDir: string,
    options?: HydratedMapsOptions,
  ): Promise<HydratedMapsGenerationResponse>;
  getRunPreflight(projectDir: string): Promise<HydratedRunPreflightResponse>;
  prepareRun(projectDir: string): Promise<HydratedRunPrepareResponse>;
  loadResults(
    projectDir: string,
    runId: string,
  ): Promise<HydratedResultsResponse>;
};

const tauriInvoke: HydratedInvoke = (command, args) =>
  invoke<string>(command, args);

export function decodeHydratedResponse<T extends { ok: boolean }>(
  rawPayload: string,
  command: string,
): T {
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawPayload);
  } catch (error) {
    throw new Error(
      `${command} 返回了无效 JSON：${
        error instanceof Error ? error.message : String(error)
      }`,
    );
  }
  if (
    parsed === null ||
    typeof parsed !== "object" ||
    Array.isArray(parsed) ||
    typeof (parsed as { ok?: unknown }).ok !== "boolean"
  ) {
    throw new Error(`${command} 返回的数据缺少布尔型 ok 字段。`);
  }
  return parsed as T;
}

export function createHydratedApi(
  invokeCommand: HydratedInvoke = tauriInvoke,
): HydratedDesktopApi {
  const request = async <T extends { ok: boolean }>(
    command: string,
    args: Record<string, unknown>,
  ): Promise<T> =>
    decodeHydratedResponse<T>(await invokeCommand(command, args), command);

  return {
    getStatus: (projectDir) =>
      request<HydratedStatusResponse>("get_hydrated_status", { projectDir }),
    prepareLigand: (projectDir) =>
      request<HydratedStatusResponse>("prepare_hydrated_ligand", {
        projectDir,
      }),
    generateMaps: (projectDir, options) =>
      request<HydratedMapsGenerationResponse>("generate_hydrated_maps", {
        projectDir,
        optionsJson: options === undefined ? null : JSON.stringify(options),
      }),
    getRunPreflight: (projectDir) =>
      request<HydratedRunPreflightResponse>("get_hydrated_run_preflight", {
        projectDir,
      }),
    prepareRun: (projectDir) =>
      request<HydratedRunPrepareResponse>("prepare_hydrated_run", {
        projectDir,
      }),
    loadResults: (projectDir, runId) =>
      request<HydratedResultsResponse>("load_hydrated_results", {
        projectDir,
        runId,
      }),
  };
}

export const hydratedApi = createHydratedApi();
