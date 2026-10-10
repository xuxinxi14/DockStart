import assert from "node:assert/strict";
import test from "node:test";
import { reportLigandStyle, reportPocketAtomIndices, reportPocketStyle, reportReceptorStyle } from "../src/components/reportPoseStyle.ts";

test("report figures fade receptor without whole-protein sticks, keeping ligand opaque", () => {
  const receptor = reportReceptorStyle("gray");
  assert.equal(receptor.cartoon.opacity, 0.22);
  assert.equal("stick" in receptor, false);
  assert.ok(reportPocketStyle("gray").stick.opacity < 0.5);
  const ligand = reportLigandStyle();
  assert.equal(ligand.stick.colorscheme, "purpleCarbon");
  assert.equal("opacity" in ligand.stick, false);
});

test("local receptor context includes only valid heavy atoms within 5 Å", () => {
  const ligand = [{ x: 0, y: 0, z: 0, elem: "C" }, { x: 20, y: 0, z: 0, elem: "H" }];
  const receptor = [
    { index: 0, x: 3, y: 4, z: 0, elem: "C" },
    { index: 1, x: 5.01, y: 0, z: 0, elem: "N" },
    { index: 2, x: 2, y: 0, z: 0, elem: "H" },
    { index: 3, x: 20, y: 0, z: 0, elem: "C" },
    { index: 4, y: 0, z: 0, elem: "C" },
    { index: 5, x: Number.NaN, y: 0, z: 0, elem: "C" },
  ];
  const original = JSON.stringify({ ligand, receptor });
  assert.deepEqual(reportPocketAtomIndices(receptor, ligand), [0]);
  assert.deepEqual(reportPocketAtomIndices(receptor, []), []);
  assert.deepEqual(reportPocketAtomIndices(receptor, ligand, -1), []);
  assert.equal(JSON.stringify({ ligand, receptor }), original);
});
