import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
  createServer,
  type ViteDevServer,
} from "vite";
import type {
  DockStartProject,
  HydratedResultsSuccess,
  HydratedRunPreflightSuccess,
  HydratedStatusSuccess,
} from "../src/types.ts";
import {
  getHydratedWorkflowAvailability,
  hydratedRunSummaries,
  isHydratedProtocolId,
  resolveHydratedRun,
} from "../src/utils/hydratedWorkflow.ts";

const project = {
  project_name: "hydrated-test",
  project_dir: "D:/project",
  created_at: "",
  updated_at: "",
  receptor: {
    source: "local",
    source_id: "",
    query_type: "",
    downloaded_at: "",
    raw_file: "raw/receptor.pdb",
    file: "prepared/receptor.pdbqt",
  },
  ligand: {
    source: "local",
    source_id: "",
    query_type: "",
    downloaded_at: "",
    raw_file: "raw/ligand.sdf",
    file: "prepared/ligand.pdbqt",
  },
  box: {
    center_x: 0,
    center_y: 0,
    center_z: 0,
    size_x: 20,
    size_y: 20,
    size_z: 20,
  },
  vina: {
    scoring: "vina",
    exhaustiveness: 8,
    max_evals: 0,
    num_modes: 9,
    min_rmsd: 1,
    energy_range: 4,
    spacing: 0.375,
    verbosity: 1,
    no_refine: false,
    force_even_voxels: false,
    unbound_energy: null,
    cpu: 1,
    seed: 7,
  },
  config: { vina_config_file: "", generated_at: "" },
  preparation: {} as DockStartProject["preparation"],
  runs: [
    { run_id: "run_001", protocol_id: "ad4_maps", status: "finished" },
    {
      run_id: "run_002",
      protocol_id: "hydrated_ad4_experimental",
      status: "prepared",
      stage: "prepared",
    },
    {
      run_id: "run_003",
      docking_protocol: { protocol_id: "hydrated_ad4_experimental" },
      status: "finished",
      stage: "finished",
    },
  ],
} as DockStartProject;

test("水合工作流只选择明确标记的 hydration run", () => {
  assert.deepEqual(
    hydratedRunSummaries(project).map((run) => run.runId),
    ["run_002", "run_003"],
  );
  assert.equal(resolveHydratedRun(project, "run_002")?.runId, "run_002");
  assert.equal(resolveHydratedRun(project, "run_001")?.runId, "run_003");
  assert.equal(isHydratedProtocolId("hydrated_ad4_experimental"), true);
  assert.equal(isHydratedProtocolId("ad4_maps"), false);
  assert.equal(isHydratedProtocolId("vina"), false);
});

test("按钮门禁要求有效配体、maps、preflight 和 finished run", () => {
  const status = {
    ok: true,
    preparation_ready: true,
    valid: true,
    maps_ready: true,
    maps_valid: true,
  } as HydratedStatusSuccess;
  const preflight = {
    ok: true,
    active_run_guard: { blocked: false },
  } as HydratedRunPreflightSuccess;

  const ready = getHydratedWorkflowAvailability({
    project,
    status,
    preflight,
    run: resolveHydratedRun(project, "run_003"),
    busy: false,
  });
  assert.equal(ready.canPrepareLigand, true);
  assert.equal(ready.canGenerateMaps, true);
  assert.equal(ready.canCheckRun, true);
  assert.equal(ready.canPrepareRun, true);
  assert.equal(ready.canLoadResults, true);

  const blocked = getHydratedWorkflowAvailability({
    project,
    status,
    preflight: {
      ...preflight,
      active_run_guard: { blocked: true },
    } as HydratedRunPreflightSuccess,
    run: resolveHydratedRun(project, "run_002"),
    busy: false,
  });
  assert.equal(blocked.canPrepareRun, false);
  assert.equal(blocked.canLoadResults, false);
  assert.match(blocked.reasons.prepareRun, /未完成的 Vina 运行/);
});

let vite: ViteDevServer;

before(async () => {
  vite = await createServer({
    appType: "custom",
    logLevel: "silent",
    plugins: [
      {
        name: "hydrated-react-test-icon-stub",
        enforce: "pre",
        resolveId(source) {
          return source === "@phosphor-icons/react"
            ? "\0hydrated-test-icons"
            : null;
        },
        load(id) {
          if (id !== "\0hydrated-test-icons") return null;
          return `
            import React from "react";
            const Icon = (props) => React.createElement("svg", props);
            export const Drop = Icon;
            export const Flask = Icon;
            export const GridFour = Icon;
            export const LockSimple = Icon;
            export const Target = Icon;
          `;
        },
      },
    ],
    root: fileURLToPath(new URL("..", import.meta.url)),
    server: { middlewareMode: true },
  });
});

after(async () => {
  await vite.close();
});

test("React 协议边界明确显示实验范围和评分限制", async () => {
  const module = await vite.ssrLoadModule(
    "/src/components/HydratedProtocolScope.tsx",
  ) as {
    default: React.ComponentType;
  };
  const html = renderToStaticMarkup(React.createElement(module.default));

  for (const text of [
    "Experimental",
    "单配体",
    "刚性受体",
    "全局对接",
    "AD4 maps",
    "不用于虚拟筛选",
    "不支持跨配体",
    "处理后评分未计算",
  ]) {
    assert.match(html, new RegExp(text));
  }
});

test("React 水合结果卡显示逐构象保留、强、弱和置换水", async () => {
  const module = await vite.ssrLoadModule(
    "/src/components/HydratedResultSummary.tsx",
  ) as {
    default: React.ComponentType<{
      results: HydratedResultsSuccess;
      selectedMode?: number;
    }>;
  };
  const results = {
    ok: true,
    protocol_id: "hydrated_ad4_experimental",
    stability: "experimental",
    project_dir: "D:/project",
    run_id: "run_003",
    metadata: {},
    score_semantics: {},
    scores: [
      {
        mode: 1,
        affinity_kcal_mol: -8.2,
        rmsd_lb: 0,
        rmsd_ub: 0,
      },
    ],
    modes: [
      {
        mode: 1,
        affinity_kcal_mol: -8.2,
        raw_affinity_kcal_mol: -8.2,
        rmsd_lb: 0,
        rmsd_ub: 0,
        water_summary: {
          retained_water_count: 4,
          strong_water_count: 2,
          weak_water_count: 2,
          displaced_water_count: 3,
        },
        waters: [],
      },
    ],
    water_summary: {
      raw_water_count: 7,
      retained_water_count: 4,
      strong_water_count: 2,
      weak_water_count: 2,
      displaced_water_count: 3,
    },
    waters_manifest: {},
    raw_output_file: "runs/run_003/out.pdbqt",
    retained_output_file: "runs/run_003/hydrated_retained.pdbqt",
    water_free_output_file: "runs/run_003/ligand_water_free.pdbqt",
    files: [],
    message: "",
    error: null,
  } satisfies HydratedResultsSuccess;
  const html = renderToStaticMarkup(
    React.createElement(module.default, { results, selectedMode: 1 }),
  );

  assert.match(html, /处理后评分未计算/);
  assert.match(html, /Raw AD4 affinity/);
  assert.match(html, /保留水/);
  assert.match(html, /强水/);
  assert.match(html, /弱水/);
  assert.match(html, /置换水/);
  assert.match(html, /-8.2 kcal\/mol/);
});
