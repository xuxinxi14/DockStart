import assert from "node:assert/strict";
import test from "node:test";
import {
  createHydratedApi,
  decodeHydratedResponse,
  type HydratedInvoke,
} from "../src/api/hydrated.ts";

test("hydrated API maps every workflow step to the expected Tauri command", async () => {
  const calls: Array<{
    command: string;
    args?: Record<string, unknown>;
  }> = [];
  const invoke: HydratedInvoke = async (command, args) => {
    calls.push({ command, args });
    return JSON.stringify({ ok: true });
  };
  const api = createHydratedApi(invoke);

  await api.getStatus("C:\\project path");
  await api.prepareLigand("C:\\project path");
  await api.generateMaps("C:\\project path", {
    spacing: 0.375,
    grid_points: { x: 48, y: 50, z: 52 },
  });
  await api.getRunPreflight("C:\\project path");
  await api.prepareRun("C:\\project path");
  await api.loadResults("C:\\project path", "run_007");

  assert.deepEqual(calls, [
    {
      command: "get_hydrated_status",
      args: { projectDir: "C:\\project path" },
    },
    {
      command: "prepare_hydrated_ligand",
      args: { projectDir: "C:\\project path" },
    },
    {
      command: "generate_hydrated_maps",
      args: {
        projectDir: "C:\\project path",
        optionsJson:
          '{"spacing":0.375,"grid_points":{"x":48,"y":50,"z":52}}',
      },
    },
    {
      command: "get_hydrated_run_preflight",
      args: { projectDir: "C:\\project path" },
    },
    {
      command: "prepare_hydrated_run",
      args: { projectDir: "C:\\project path" },
    },
    {
      command: "load_hydrated_results",
      args: { projectDir: "C:\\project path", runId: "run_007" },
    },
  ]);
});

test("hydrated maps omit Python options through a null Tauri argument", async () => {
  let captured: Record<string, unknown> | undefined;
  const api = createHydratedApi(async (_command, args) => {
    captured = args;
    return '{"ok":true}';
  });

  await api.generateMaps("project");
  assert.deepEqual(captured, { projectDir: "project", optionsJson: null });
});

test("hydrated response decoder preserves structured business errors", () => {
  const payload =
    '{"ok":false,"error":{"code":"HYDRATED_MAPS_NOT_READY","message":"maps 未就绪"}}';
  assert.deepEqual(
    decodeHydratedResponse(payload, "get_hydrated_run_preflight"),
    {
      ok: false,
      error: {
        code: "HYDRATED_MAPS_NOT_READY",
        message: "maps 未就绪",
      },
    },
  );
  assert.throws(
    () => decodeHydratedResponse('{"message":"missing ok"}', "command"),
    /缺少布尔型 ok 字段/,
  );
});
