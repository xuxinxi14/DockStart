import assert from "node:assert/strict";
import test from "node:test";
import { normalizeReceptorTransparency, receptorOpacity, workspaceReceptorStyle } from "../src/components/receptorTransparency.ts";
import { reportLigandStyle, reportPocketStyle, reportReceptorStyle } from "../src/components/reportPoseStyle.ts";

test("transparency percentages are clamped and converted to opacity with correct endpoints", () => {
  assert.equal(receptorOpacity(0), 1);
  assert.equal(receptorOpacity(100), 0);
  assert.equal(receptorOpacity(26), 0.74);
  assert.equal(receptorOpacity(78), 0.22);
  assert.equal(normalizeReceptorTransparency(-5), 0);
  assert.equal(normalizeReceptorTransparency(130), 100);
  assert.equal(normalizeReceptorTransparency(56.7), 57);
  assert.equal(normalizeReceptorTransparency(Number.NaN), 26);
});

test("both receptor representations fade together and ligand styles remain opaque", () => {
  for (const transparency of [0, 50, 100]) {
    const opacity = receptorOpacity(transparency);
    const workspace = workspaceReceptorStyle(opacity);
    assert.equal(workspace.cartoon.opacity, opacity);
    assert.equal(workspace.stick.opacity, opacity);
    assert.equal(reportReceptorStyle("gray", opacity).cartoon.opacity, opacity);
    assert.equal(reportPocketStyle("gray", opacity).stick.opacity, opacity);
    assert.equal("opacity" in reportLigandStyle().stick, false);
  }
});
