import assert from "node:assert/strict";
import test from "node:test";
import {
  splitLigandStructurePaths,
  structureInputKind,
} from "../src/utils/structureInput.ts";

test("受体与配体分别识别 PDBQT 和原始结构格式", () => {
  assert.equal(structureInputKind("C:\\data\\receptor.PDBQT", "receptor"), "pdbqt");
  assert.equal(structureInputKind("C:\\data\\receptor.cif", "receptor"), "raw");
  assert.equal(structureInputKind("C:\\data\\ligand.sdf", "ligand"), "raw");
  assert.equal(structureInputKind("C:\\data\\ligand.pdb", "ligand"), "unsupported");
});

test("混合配体文件不会被强制归入同一种输入模式", () => {
  assert.deepEqual(
    splitLigandStructurePaths(["a.pdbqt", "b.sdf", "c.mol", "ignored.txt"]),
    { pdbqt: ["a.pdbqt"], raw: ["b.sdf", "c.mol"] },
  );
});
