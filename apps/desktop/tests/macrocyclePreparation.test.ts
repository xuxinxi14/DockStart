import assert from "node:assert/strict";
import test from "node:test";
import type {
  MacrocycleReviewOptions,
  MacrocycleStatusResponse,
} from "../src/types.ts";
import {
  buildReviewedMacrocyclePreparationOptions,
  createMacrocycleApi,
  decodeMacrocycleResponse,
  extractMacrocyclePreparationEvidence,
  macrocycleReviewMatchesOptions,
  normalizeMacrocycleReviewOptions,
} from "../src/utils/macrocyclePreparation.ts";

const reviewOptions: MacrocycleReviewOptions = {
  min_ring_size: 7,
  max_breaks: 4,
  allow_atom_type_a_endpoints: false,
  keep_chorded_rings: false,
  keep_equivalent_rings: false,
};

function confirmedStatus(): MacrocycleStatusResponse {
  return {
    ok: true,
    state: "confirmed",
    can_prepare: true,
    integrity: { ok: true, issues: [] },
    review: {
      review_id: "review_001",
      valid: true,
      protocol_id: "meeko_macrocycle",
      analysis_sha256: "a".repeat(64),
      source: {
        relative_path: "raw/ligand.sdf",
        sha256: "b".repeat(64),
        size_bytes: 100,
      },
      options: reviewOptions,
      molecule: {
        name: "BACE_1",
        atom_count: 20,
        bond_count: 21,
        conformer_count: 1,
        record_count: 1,
        has_3d_coordinates: true,
      },
      rings: [],
      macrocycle_ring_ids: ["ring_001"],
      is_macrocycle: true,
      candidate_sets: [],
      candidate_count_total: 2,
      recommended_candidate_id: "candidate_default",
      flexible_supported: true,
      rigid_supported: true,
      unsupported_reasons: [],
      tool_versions: { meeko: "0.7.1", rdkit: "2026.03.3" },
      record: {
        relative_path: "preparation/macrocycle_reviews/review_001/review.json",
        sha256: "c".repeat(64),
        size_bytes: 1000,
        created_at: "",
      },
    },
    confirmation: {
      confirmation_id: "confirmation_001",
      review_id: "review_001",
      selection_mode: "candidate",
      candidate_id: "candidate_default",
      exact_bonds: [[4, 5]],
      selected_bonds: [],
      binding_sha256: "d".repeat(64),
      valid: true,
      record: {
        relative_path: "preparation/macrocycle_reviews/review_001/confirmation_001.json",
        sha256: "e".repeat(64),
        size_bytes: 500,
        created_at: "",
      },
    },
  };
}

test("reviewed preparation only sends server review id and confirmation SHA", () => {
  const options = buildReviewedMacrocyclePreparationOptions(
    confirmedStatus(),
    { kind: "candidate", candidateId: "candidate_default" },
    reviewOptions,
  );
  assert.deepEqual(options, {
    protocol: "meeko_macrocycle",
    macrocycle: {
      mode: "reviewed",
      review_id: "review_001",
      confirmation_sha256: "e".repeat(64),
    },
  });
  assert.equal(JSON.stringify(options).includes("bond"), false);
  assert.equal(JSON.stringify(options).includes("candidate"), false);
});

test("preparation gate rejects changed options or a different frontend selection", () => {
  const status = confirmedStatus();
  assert.equal(
    buildReviewedMacrocyclePreparationOptions(
      status,
      { kind: "candidate", candidateId: "candidate_other" },
      reviewOptions,
    ),
    null,
  );
  assert.equal(
    buildReviewedMacrocyclePreparationOptions(
      status,
      { kind: "candidate", candidateId: "candidate_default" },
      { ...reviewOptions, min_ring_size: 8 },
    ),
    null,
  );
  assert.equal(
    macrocycleReviewMatchesOptions(status, { ...reviewOptions, min_ring_size: 8 }),
    false,
  );
  assert.equal(
    macrocycleReviewMatchesOptions(status, { ...reviewOptions, max_breaks: 3 }),
    false,
  );
});

test("formal review clamps the supported minimum ring size to seven", () => {
  assert.equal(
    normalizeMacrocycleReviewOptions({ ...reviewOptions, min_ring_size: 3 }).min_ring_size,
    7,
  );
  assert.equal(
    normalizeMacrocycleReviewOptions({ ...reviewOptions, min_ring_size: 40 }).min_ring_size,
    33,
  );
  assert.equal(
    normalizeMacrocycleReviewOptions({ ...reviewOptions, max_breaks: 0 }).max_breaks,
    4,
  );
  assert.equal(
    normalizeMacrocycleReviewOptions({ ...reviewOptions, max_breaks: 9 }).max_breaks,
    4,
  );
  for (const maxBreaks of [1, 3, 4]) {
    assert.equal(
      normalizeMacrocycleReviewOptions({
        ...reviewOptions,
        max_breaks: maxBreaks,
      }).max_breaks,
      maxBreaks,
    );
  }
});

test("rigid confirmation is accepted only with a rigid frontend selection", () => {
  const status = confirmedStatus();
  if (status.confirmation) {
    status.confirmation.selection_mode = "rigid";
    status.confirmation.candidate_id = "";
  }
  assert.ok(
    buildReviewedMacrocyclePreparationOptions(status, { kind: "rigid" }, reviewOptions),
  );
  assert.equal(
    buildReviewedMacrocyclePreparationOptions(
      status,
      { kind: "candidate", candidateId: "candidate_default" },
      reviewOptions,
    ),
    null,
  );
});

test("macrocycle API maps review, candidate, rigid and reset to controlled Tauri commands", async () => {
  const calls: Array<{ command: string; args?: Record<string, unknown> }> = [];
  const api = createMacrocycleApi(async (command, args) => {
    calls.push({ command, args });
    return '{"ok":true}';
  });
  await api.getStatus("D:\\project");
  await api.review("D:\\project", reviewOptions);
  await api.confirmCandidate("D:\\project", "review_001", "candidate_1");
  await api.confirmRigid("D:\\project", "review_001");
  await api.resetConfirmation("D:\\project");

  assert.deepEqual(calls, [
    { command: "get_macrocycle_status", args: { projectDir: "D:\\project" } },
    {
      command: "review_ligand_macrocycle",
      args: {
        projectDir: "D:\\project",
        optionsJson: JSON.stringify(reviewOptions),
      },
    },
    {
      command: "confirm_ligand_macrocycle",
      args: {
        projectDir: "D:\\project",
        reviewId: "review_001",
        candidateId: "candidate_1",
        rigid: false,
      },
    },
    {
      command: "confirm_ligand_macrocycle",
      args: {
        projectDir: "D:\\project",
        reviewId: "review_001",
        candidateId: null,
        rigid: true,
      },
    },
    {
      command: "reset_ligand_macrocycle_confirmation",
      args: { projectDir: "D:\\project" },
    },
  ]);
});

test("macrocycle IPC decoder preserves business failures and rejects invalid JSON", () => {
  assert.deepEqual(
    decodeMacrocycleResponse(
      '{"ok":false,"error":{"code":"MACROCYCLE_REVIEW_REQUIRED","message":"请先完成大环复核"}}',
      "get_macrocycle_status",
    ),
    {
      ok: false,
      error: {
        code: "MACROCYCLE_REVIEW_REQUIRED",
        message: "请先完成大环复核",
      },
    },
  );
  assert.throws(
    () => decodeMacrocycleResponse("not-json", "get_macrocycle_status"),
    /IPC 契约错误：get_macrocycle_status 返回了无效 JSON/,
  );
});

test("macrocycle IPC decoder rejects incomplete or mistyped business errors", () => {
  for (const payload of [
    '{"ok":false,"error":{"message":"失败"}}',
    '{"ok":false,"error":{"code":"FAILED"}}',
    '{"ok":false,"error":{"code":"FAILED","message":"失败","suggestion":false}}',
  ]) {
    assert.throws(
      () => decodeMacrocycleResponse(payload, "get_macrocycle_status"),
      /IPC 契约错误：get_macrocycle_status 业务失败响应/,
    );
  }
});

test("evidence view compares expected and actual bonds and records G count", () => {
  const evidence = extractMacrocyclePreparationEvidence({
    method: "meeko_macrocycle",
    protocol: "meeko_macrocycle",
    meeko_version: "0.7.1",
    rdkit_version: "2026.03.3",
    macrocycle_contract: {
      review_id: "review_001",
      selection_mode: "candidate",
      candidate_id: "candidate_1",
      exact_bonds: [[9, 2]],
      confirmation_sha256: "f".repeat(64),
    },
    protocol_evidence: {
      ok: true,
      evidence: {
        actual_bonds: [[2, 9]],
        glue_pseudo_atom_count: 2,
      },
    },
  });
  assert.equal(evidence?.bondsMatch, true);
  assert.equal(evidence?.gluePseudoAtomCount, 2);
  assert.deepEqual(evidence?.expectedBonds, [[9, 2]]);
});
