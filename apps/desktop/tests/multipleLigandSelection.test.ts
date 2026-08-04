import assert from "node:assert/strict";
import test from "node:test";

import type { LigandImportCandidate } from "../src/utils/screeningLigandImport.ts";
import {
  moveMultipleLigandMember,
  multipleLigandCompatibilityIssues,
  normalizeMultipleLigandSelection,
  selectedMultipleLigandFiles,
  toggleMultipleLigandMember,
} from "../src/utils/multipleLigandSelection.ts";

function candidate(id: string): LigandImportCandidate {
  return {
    id,
    status: "ready",
    displayName: id,
    sourceFile: `${id}.pdbqt`,
    originalName: `${id}.pdbqt`,
    sourceFormat: "pdbqt",
    recordIndex: 1,
    stagedFile: `screening/staging/${id}.pdbqt`,
    sha256: id.repeat(64).slice(0, 64),
    sizeBytes: 100,
    duplicateOf: null,
    issue: null,
    warnings: [],
  };
}

test("共同对接只保留两个不同且仍可用的成员", () => {
  const candidates = [candidate("a"), candidate("b"), candidate("c")];
  assert.deepEqual(
    normalizeMultipleLigandSelection(candidates, ["a", "missing", "a", "b", "c"]),
    ["a", "b"],
  );
  assert.deepEqual(
    selectedMultipleLigandFiles(candidates, ["b", "a"]),
    ["screening/staging/b.pdbqt", "screening/staging/a.pdbqt"],
  );
});

test("第三个成员不会静默替换已选成员，顺序可以显式调整", () => {
  const candidates = [candidate("a"), candidate("b"), candidate("c")];
  const limited = toggleMultipleLigandMember(candidates, ["a", "b"], "c");
  assert.equal(limited.limitReached, true);
  assert.deepEqual(limited.selection, ["a", "b"]);
  assert.deepEqual(moveMultipleLigandMember(limited.selection, "b", -1), ["b", "a"]);
});

test("协议门禁同时检查评分协议、受体、任务类型和 Vina 能力", () => {
  assert.deepEqual(
    multipleLigandCompatibilityIssues({
      engine: "vina",
      protocolId: "rigid_single",
      receptorMode: "rigid",
      runMode: "dock",
      capabilityChecked: true,
      capabilitySupported: true,
    }),
    [],
  );
  assert.deepEqual(
    multipleLigandCompatibilityIssues({
      engine: "ad4_maps",
      protocolId: "ad4zn_beta",
      receptorMode: "flexible",
      runMode: "local_only",
      capabilityChecked: true,
      capabilitySupported: false,
    }),
    [
      "仅支持 Vina/Vinardo 或标准 AutoDock4 maps",
      "仅支持刚性受体",
      "仅支持全局对接",
      "当前 Vina 不支持一个 --ligand 后跟多个输入文件",
    ],
  );
  assert.deepEqual(
    multipleLigandCompatibilityIssues({
      engine: "vina",
      protocolId: "vina_maps",
      receptorMode: "rigid",
      runMode: "dock",
      capabilityChecked: true,
      capabilitySupported: true,
    }),
    ["仅支持 Vina/Vinardo 或标准 AutoDock4 maps"],
  );
});
