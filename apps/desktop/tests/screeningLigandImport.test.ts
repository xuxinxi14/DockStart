import assert from "node:assert/strict";
import test from "node:test";

import {
  defaultLigandSelection,
  dockingModeForLigandPreview,
  ligandTopologyCoverage,
  normalizeLigandImportPreview,
  retryableLigandCandidateIds,
  selectedLigandCandidateIds,
  selectedLigandFiles,
  toggleLigandSelection,
} from "../src/utils/screeningLigandImport.ts";

const SHA_A = "a".repeat(64);
const SHA_B = "b".repeat(64);
const SHA_C = "c".repeat(64);
const SHA_D = "d".repeat(64);
const SHA_E = "e".repeat(64);
const SHA_F = "f".repeat(64);

function facts(topologySha256: string, overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    status: "verified",
    source: "frozen_raw_topology",
    calculation_profile: "rdkit_source_formal_charge_heavy_atoms_strict_rotatable_bonds_v1",
    source_topology_sha256: topologySha256,
    rdkit_version: "2026.03.3",
    formal_charge: 0,
    heavy_atom_count: 12,
    rotatable_bond_count: 3,
    fragment_count: 1,
    max_ring_size: 0,
    has_macrocycle: false,
    canonical_topology_sha256: SHA_F,
    canonical_atom_count: 20,
    canonical_bond_count: 19,
    ...overrides,
  };
}

function evidence(importId: string, recordId: string, topologySha256: string) {
  return {
    schema_version: 2,
    status: "ready",
    preparation_profile: "dockstart_screening_rdkit_meeko_fail_closed_v2",
    hydrogen_policy: "rdkit_add_hs_preserve_source_indices_v1",
    import_id: importId,
    record_id: recordId,
    source_topology_sha256: topologySha256,
    toolchain: {
      python_version: "3.11.15",
      rdkit_version: "2026.03.3",
      meeko_version: "0.7.1",
    },
    worker_script_sha256: SHA_D,
    worker_manifest_sha256: SHA_E,
    worker_manifest_size_bytes: 256,
  };
}

function v2Candidate(
  id: string,
  index: number,
  overrides: Record<string, unknown> = {},
) {
  const importId = "import_000001";
  const recordId = `record_${String(index).repeat(64).slice(0, 64)}`;
  const topologySha256 = index % 2 ? SHA_A : SHA_B;
  const base = {
    candidate_id: id,
    import_id: importId,
    record_id: recordId,
    status: "ready",
    source_file: String.raw`D:\库\multi.sdf`,
    original_name: "multi.sdf",
    source_format: "sdf",
    source_record_index: index,
    source_record_name: `Molecule ${index}`,
    source_record_sha256: topologySha256,
    source_record_size_bytes: 200 + index,
    source_topology_file: `screening/staging/imports/${importId}/records/${recordId}.sdf`,
    source_topology_sha256: topologySha256,
    source_topology_size_bytes: 200 + index,
    topology_integrity: "verified",
    file: `screening/staging/${index}.pdbqt`,
    sha256: index % 2 ? SHA_C : SHA_D,
    size_bytes: 300 + index,
    chemical_facts: facts(topologySha256),
    preparation_evidence: evidence(importId, recordId, topologySha256),
    preparation_attempts: [{
      attempt: 1,
      recorded_at: "2026-07-29T08:00:00Z",
      status: "ready",
      source_topology_file: `screening/staging/imports/${importId}/records/${recordId}.sdf`,
      source_topology_sha256: topologySha256,
      source_topology_size_bytes: 200 + index,
      chemical_facts: facts(topologySha256),
      preparation_evidence: evidence(importId, recordId, topologySha256),
      output_file: `screening/staging/${index}.pdbqt`,
      output_sha256: index % 2 ? SHA_C : SHA_D,
      output_size_bytes: 300 + index,
      error: {},
    }],
    retryable: false,
    warnings: [],
  };
  return { ...base, ...overrides };
}

function v2Response(candidates: Array<Record<string, unknown>>) {
  const failureCount = candidates.filter((candidate) => (
    candidate.status === "invalid" || candidate.status === "review_required"
  )).length;
  return {
    import_preview: {
      schema_version: 2,
      import_id: "import_000001",
      revision_sha256: SHA_E,
      candidates,
      failure_manifest: {
        json_file: "screening/staging/imports/import_000001/preparation_failures.json",
        json_sha256: SHA_A,
        json_size_bytes: 456,
        csv_file: "screening/staging/imports/import_000001/preparation_failures.csv",
        csv_sha256: SHA_B,
        csv_size_bytes: 234,
        failure_count: failureCount,
      },
    },
  };
}

test("旧 staged 响应兼容且重复路径只保留首条", () => {
  const preview = normalizeLigandImportPreview({
    staged: [
      {
        file: "screening/staging/a.pdbqt",
        original_name: "中文 配体.pdbqt",
        source_file: String.raw`D:\配体库\中文 配体.pdbqt`,
      },
      {
        file: "screening/staging/a.pdbqt",
        original_name: "重复项.pdbqt",
      },
    ],
  });
  assert.equal(preview.schemaVersion, 1);
  assert.equal(preview.importId, null);
  assert.equal(preview.counts.ready, 1);
  assert.equal(preview.counts.reviewRequired, 0);
  assert.equal(preview.candidates[0]?.topologyIntegrity, "legacy_unverified");
  assert.deepEqual(
    selectedLigandFiles(preview, defaultLigandSelection(preview)),
    ["screening/staging/a.pdbqt"],
  );
});

test("现代 v1 预览重算状态且不虚构 v2 拓扑身份", () => {
  const preview = normalizeLigandImportPreview({
    import_preview: {
      schema_version: 1,
      candidates: [
        {
          candidate_id: "ready-a",
          status: "ready",
          source_file: "a.sdf",
          original_name: "a.sdf",
          source_format: "sdf",
          source_record_index: 1,
          file: "screening/staging/a.pdbqt",
          sha256: SHA_A,
          size_bytes: 123,
        },
        {
          candidate_id: "duplicate-a",
          status: "duplicate",
          source_file: "a.sdf",
          original_name: "a.sdf",
          source_format: "sdf",
          source_record_index: 2,
          file: "screening/staging/a.pdbqt",
          sha256: SHA_A,
          size_bytes: 123,
          duplicate_of: "ready-a",
        },
        {
          candidate_id: "invalid-a",
          status: "invalid",
          source_file: "a.sdf",
          original_name: "a.sdf",
          source_format: "sdf",
          source_record_index: 3,
          error: { code: "INVALID", message: "无法读取" },
        },
      ],
    },
  });
  assert.deepEqual(preview.counts, {
    total: 3,
    ready: 1,
    duplicate: 1,
    reviewRequired: 0,
    invalid: 1,
  });
  assert.equal(preview.candidates[0]?.sourceTopologySha256, null);
  assert.deepEqual(retryableLigandCandidateIds(preview), []);
});

test("v2 保留导入身份、拓扑、化学事实、准备证据、尝试与失败清单", () => {
  const invalid = v2Candidate("ligand_cccccccccccc_0002", 2, {
    status: "invalid",
    file: "",
    sha256: "",
    size_bytes: 0,
    retryable: true,
    error: {
      code: "LIGAND_PREPARATION_FAILED",
      message: "Meeko 准备失败。",
      suggestion: "检查结构后重试。",
    },
    preparation_attempts: [{
      ...v2Candidate("unused", 2).preparation_attempts[0],
      status: "invalid",
      output_file: "",
      output_sha256: "",
      output_size_bytes: 0,
      error: { code: "LIGAND_PREPARATION_FAILED", message: "Meeko 准备失败。" },
    }],
  });
  const preview = normalizeLigandImportPreview(v2Response([
    v2Candidate("ligand_aaaaaaaaaaaa_0001", 1),
    invalid,
  ]));

  assert.equal(preview.schemaVersion, 2);
  assert.equal(preview.importId, "import_000001");
  assert.equal(preview.revisionSha256, SHA_E);
  assert.equal(preview.failureManifest?.failureCount, 1);
  assert.equal(preview.candidates[0]?.chemicalFacts?.formalCharge, 0);
  assert.equal(preview.candidates[0]?.chemicalFacts?.heavyAtomCount, 12);
  assert.equal(preview.candidates[0]?.preparationEvidence?.toolchain?.meekoVersion, "0.7.1");
  assert.equal(preview.candidates[0]?.preparationAttempts.length, 1);
  assert.equal(preview.candidates[1]?.recordId, `record_${"2".repeat(64)}`);
  assert.deepEqual(retryableLigandCandidateIds(preview), ["ligand_cccccccccccc_0002"]);
});

test("大环 review_required 不可选择且不可自动重试", () => {
  const macrocycle = v2Candidate("ligand_bbbbbbbbbbbb_0001", 1, {
    status: "review_required",
    file: "",
    sha256: "",
    size_bytes: 0,
    retryable: false,
    chemical_facts: facts(SHA_A, {
      heavy_atom_count: 8,
      max_ring_size: 8,
      has_macrocycle: true,
      canonical_atom_count: 8,
      canonical_bond_count: 8,
    }),
    error: {
      code: "MACROCYCLE_REVIEW_REQUIRED",
      message: "检测到大环配体。",
    },
  });
  const preview = normalizeLigandImportPreview(v2Response([macrocycle]));
  assert.equal(preview.counts.reviewRequired, 1);
  assert.deepEqual(defaultLigandSelection(preview), new Set());
  assert.deepEqual(retryableLigandCandidateIds(preview), []);
  assert.deepEqual(
    [...toggleLigandSelection(preview, new Set(), macrocycle.candidate_id as string)],
    [],
  );
});

test("v2 逻辑候选保留重复物理 PDBQT 路径以供后端交叉校验", () => {
  const first = v2Candidate("ligand_aaaaaaaaaaaa_0001", 1, {
    file: "screening/staging/shared.pdbqt",
    sha256: SHA_C,
  });
  const second = v2Candidate("ligand_bbbbbbbbbbbb_0002", 2, {
    file: "screening/staging/shared.pdbqt",
    sha256: SHA_C,
  });
  const preview = normalizeLigandImportPreview(v2Response([first, second]));
  const selection = defaultLigandSelection(preview);
  assert.deepEqual(selectedLigandCandidateIds(preview, selection), [
    "ligand_aaaaaaaaaaaa_0001",
    "ligand_bbbbbbbbbbbb_0002",
  ]);
  assert.deepEqual(selectedLigandFiles(preview, selection), [
    "screening/staging/shared.pdbqt",
    "screening/staging/shared.pdbqt",
  ]);
});

test("拓扑覆盖仅统计当前选择的 ready 候选", () => {
  const raw = v2Candidate("ligand_aaaaaaaaaaaa_0001", 1);
  const pdbqt = {
    ...v2Candidate("ligand_bbbbbbbbbbbb_0002", 2),
    source_format: "pdbqt",
    source_topology_file: "",
    source_topology_sha256: "",
    source_topology_size_bytes: 0,
    topology_integrity: "not_available",
    chemical_facts: {
      schema_version: 1,
      status: "unavailable",
      source: "supplied_pdbqt",
      reason: "PDBQT 不保留可靠键级。",
      source_topology_sha256: "",
    },
    preparation_evidence: {
      schema_version: 2,
      status: "not_required",
      source: "supplied_pdbqt",
      import_id: "import_000001",
      record_id: `record_${"2".repeat(64)}`,
    },
    preparation_attempts: [],
  };
  const preview = normalizeLigandImportPreview(v2Response([raw, pdbqt]));
  const coverage = ligandTopologyCoverage(preview, defaultLigandSelection(preview));
  assert.deepEqual(coverage, {
    selected: 2,
    verified: 1,
    unavailable: 1,
    status: "partial",
  });
});

test("ready 数量而不是用户原选文件数决定单配体或批筛模式", () => {
  const single = normalizeLigandImportPreview(v2Response([
    v2Candidate("ligand_aaaaaaaaaaaa_0001", 1),
  ]));
  const batch = normalizeLigandImportPreview(v2Response([
    v2Candidate("ligand_aaaaaaaaaaaa_0001", 1),
    v2Candidate("ligand_bbbbbbbbbbbb_0002", 2),
  ]));
  assert.equal(dockingModeForLigandPreview(single), "single");
  assert.equal(dockingModeForLigandPreview(batch), "batch");
});

test("v2 缺失 revision、拓扑身份或事实绑定时 fail closed", () => {
  const missingRevision = v2Response([v2Candidate("ligand_aaaaaaaaaaaa_0001", 1)]);
  (missingRevision.import_preview as Record<string, unknown>).revision_sha256 = "";
  assert.throws(
    () => normalizeLigandImportPreview(missingRevision),
    /缺少有效的 import ID 或 revision/,
  );

  const brokenTopology = v2Candidate("ligand_aaaaaaaaaaaa_0001", 1, {
    source_topology_sha256: "",
  });
  assert.throws(
    () => normalizeLigandImportPreview(v2Response([brokenTopology])),
    /缺少已验证的冻结原始拓扑/,
  );

  const mismatchedFacts = v2Candidate("ligand_aaaaaaaaaaaa_0001", 1, {
    chemical_facts: facts(SHA_B),
  });
  assert.throws(
    () => normalizeLigandImportPreview(v2Response([mismatchedFacts])),
    /化学事实与冻结原始拓扑不一致/,
  );

  const mismatchedEvidence = v2Candidate("ligand_aaaaaaaaaaaa_0001", 1);
  mismatchedEvidence.preparation_evidence = {
    ...(mismatchedEvidence.preparation_evidence as Record<string, unknown>),
    record_id: `record_${"f".repeat(64)}`,
  };
  assert.throws(
    () => normalizeLigandImportPreview(v2Response([mismatchedEvidence])),
    /准备证据身份不一致/,
  );
});
