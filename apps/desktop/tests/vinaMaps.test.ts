import assert from "node:assert/strict";
import test from "node:test";
import type {
  DockStartProject,
  VinaMapsStatusResponse,
} from "../src/types.ts";
import {
  buildRawVinaMapsImportOptions,
  vinaMapsCompatibilityIssues,
  vinaMapsGridSummary,
} from "../src/utils/vinaMaps.ts";

const baseProject = {
  docking_protocol: {
    engine: "vina",
    receptor_mode: "rigid",
    run_mode: "dock",
    autobox: false,
  },
} as DockStartProject;

test("precomputed Vina maps are limited to rigid global docking", () => {
  assert.deepEqual(vinaMapsCompatibilityIssues(baseProject), []);
  assert.deepEqual(
    vinaMapsCompatibilityIssues({
      ...baseProject,
      docking_protocol: {
        ...baseProject.docking_protocol,
        receptor_mode: "flexible",
        run_mode: "score_only",
        autobox: true,
      },
    }),
    ["仅支持刚性受体", "仅支持全局对接", "不支持 autobox"],
  );
});
test("raw maps attestation requires all three user confirmations and stable hashes", () => {
  const receptorSha = "A".repeat(64);
  const vinaSha = "B".repeat(64);
  const status = {
    ok: true,
    ready: false,
    current_context: {
      scoring_function: "vinardo",
      receptor: { source_sha256: receptorSha },
      vina: { sha256: vinaSha },
      raw_import_attestation_template: {
        version: 1,
        confirmed: false,
        scoring_function: "vinardo",
        receptor_sha256: receptorSha,
        vina_binary_sha256: vinaSha,
        statement: "source maps belong to the stated receptor and scoring function",
      },
    },
  } as VinaMapsStatusResponse;

  assert.equal(
    buildRawVinaMapsImportOptions(status, {
      receptor: true,
      box: false,
      scoring: true,
    }),
    null,
  );

  assert.deepEqual(
    buildRawVinaMapsImportOptions(status, {
      receptor: true,
      box: true,
      scoring: true,
    }),
    {
      activate: true,
      attestation: {
        version: 1,
        confirmed: true,
        scoring_function: "vinardo",
        receptor_sha256: receptorSha.toLowerCase(),
        vina_binary_sha256: vinaSha.toLowerCase(),
        statement: "source maps belong to the stated receptor and scoring function",
      },
    },
  );
});

test("raw maps attestation rejects incomplete backend context", () => {
  const status = {
    ok: true,
    ready: false,
    current_context: {
      scoring_function: "vina",
      receptor: { source_sha256: "c".repeat(64) },
      vina: { sha256: "" },
    },
  } as VinaMapsStatusResponse;
  assert.equal(
    buildRawVinaMapsImportOptions(status, {
      receptor: true,
      box: true,
      scoring: true,
    }),
    null,
  );
});

test("manifest grid summary prefers the requested Box", () => {
  assert.deepEqual(
    vinaMapsGridSummary({
      grid: {
        requested_box: {
          center: { x: 1, y: 2, z: 3 },
          size: { x: 20, y: 22, z: 24 },
        },
        nelements: { x: 54, y: 58, z: 64 },
        spacing: 0.375,
      },
    }),
    {
      center: "1.00 × 2.00 × 3.00",
      size: "20.00 × 22.00 × 24.00",
      elements: "54 × 58 × 64",
      spacing: "0.375 Å",
    },
  );
});
