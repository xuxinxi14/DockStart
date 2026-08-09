import assert from "node:assert/strict";
import test from "node:test";
import type { DockStartProject } from "../src/types.ts";
import { mapsProjectContextKey } from "../src/utils/mapsContext.ts";

const project = {
  project_dir: "C:/project",
  revision: 4,
  updated_at: "2026-08-09T00:00:00Z",
  receptor: { file: "prepared/receptor.pdbqt", raw_file: "" },
  box: {
    center_x: 1,
    center_y: 2,
    center_z: 3,
    size_x: 20,
    size_y: 20,
    size_z: 20,
  },
  vina: { scoring: "vina", spacing: 0.375 },
  docking_protocol: {
    engine: "vina",
    receptor_mode: "rigid",
    grid_source: "receptor",
  },
} as DockStartProject;

test("maps context changes for project revision and scientific inputs", () => {
  const initial = mapsProjectContextKey(project);
  assert.notEqual(mapsProjectContextKey({ ...project, revision: 5 }), initial);
  assert.notEqual(mapsProjectContextKey({
    ...project,
    box: { ...project.box, size_x: 24 },
  }), initial);
  assert.notEqual(mapsProjectContextKey({
    ...project,
    vina: { ...project.vina, scoring: "vinardo" },
  }), initial);
  assert.notEqual(mapsProjectContextKey({
    ...project,
    receptor: { ...project.receptor, file: "prepared/receptor-new.pdbqt" },
  }), initial);
});
