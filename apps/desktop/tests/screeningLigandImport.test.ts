import assert from "node:assert/strict";
import test from "node:test";

import {
  defaultLigandSelection,
  normalizeLigandImportPreview,
  selectedLigandFiles,
  toggleLigandSelection,
} from "../src/utils/screeningLigandImport.ts";

const SHA_A = "a".repeat(64);
const SHA_B = "b".repeat(64);

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
  assert.equal(preview.counts.ready, 1);
  assert.equal(preview.candidates[0]?.displayName, "中文 配体.pdbqt");
  const selection = defaultLigandSelection(preview);
  assert.deepEqual(
    selectedLigandFiles(preview, selection),
    ["screening/staging/a.pdbqt"],
  );
});
test("现代预览重算 ready、duplicate、invalid，并只默认选择可用项", () => {
  const preview = normalizeLigandImportPreview({
    staged: [],
    import_preview: {
      schema_version: 1,
      summary: { total: 99, ready: 99, duplicate: 0, invalid: 0 },
      candidates: [
        {
          candidate_id: "ready-a",
          status: "ready",
          source_file: String.raw`D:\库\multi.sdf`,
          original_name: "multi.sdf",
          source_format: "sdf",
          source_record_index: 1,
          source_record_name: "Molecule A",
          file: "screening/staging/a.pdbqt",
          sha256: SHA_A,
          size_bytes: 123,
        },
        {
          candidate_id: "duplicate-a",
          status: "duplicate",
          source_file: String.raw`D:\库\multi.sdf`,
          original_name: "multi.sdf",
          source_format: "sdf",
          source_record_index: 2,
          source_record_name: "",
          file: "screening/staging/a.pdbqt",
          sha256: SHA_A,
          size_bytes: 123,
          duplicate_of: "ready-a",
        },
        {
          candidate_id: "invalid-b",
          status: "invalid",
          source_file: String.raw`D:\库\multi.sdf`,
          original_name: "multi.sdf",
          source_format: "sdf",
          source_record_index: 3,
          source_record_name: "",
          error: {
            code: "RDKIT_RECORD_INVALID",
            message: "RDKit 未能读取该分子记录。",
          },
        },
        {
          candidate_id: "ready-b",
          status: "ready",
          source_file: "/tmp/second.mol",
          original_name: "second.mol",
          source_format: "mol",
          source_record_index: 1,
          file: "screening/staging/b.pdbqt",
          sha256: SHA_B,
          size_bytes: 456,
        },
      ],
    },
  });

  assert.deepEqual(preview.counts, {
    total: 4,
    ready: 2,
    duplicate: 1,
    invalid: 1,
  });
  const selection = defaultLigandSelection(preview);
  assert.deepEqual(
    selectedLigandFiles(preview, selection),
    ["screening/staging/a.pdbqt", "screening/staging/b.pdbqt"],
  );
  assert.equal(preview.candidates[2]?.displayName, "multi.sdf");
  assert.equal(preview.candidates[2]?.issue?.code, "RDKIT_RECORD_INVALID");
});

test("重复和失败项不可切换，选择结果保持后端候选顺序并去重", () => {
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
          error: { code: "INVALID", message: "invalid" },
        },
      ],
    },
  });
  const initial = defaultLigandSelection(preview);
  assert.deepEqual(
    [...toggleLigandSelection(preview, initial, "duplicate-a")],
    [...initial],
  );
  assert.deepEqual(
    [...toggleLigandSelection(preview, initial, "invalid-a")],
    [...initial],
  );
  const empty = toggleLigandSelection(preview, initial, "ready-a");
  assert.deepEqual(selectedLigandFiles(preview, empty), []);
});

test("全坏记录可显示，但没有可创建队列的文件", () => {
  const preview = normalizeLigandImportPreview({
    import_preview: {
      schema_version: 1,
      candidates: [
        {
          candidate_id: "invalid-only",
          status: "invalid",
          source_file: "bad.sdf",
          original_name: "bad.sdf",
          source_format: "sdf",
          source_record_index: 1,
          error: { code: "INVALID", message: "无法读取" },
        },
      ],
    },
  });
  const selection = defaultLigandSelection(preview);
  assert.equal(preview.counts.invalid, 1);
  assert.deepEqual(selectedLigandFiles(preview, selection), []);
});

test("现代可用项缺少完整 staging 身份时拒绝响应", () => {
  assert.throws(
    () => normalizeLigandImportPreview({
      import_preview: {
        schema_version: 1,
        candidates: [
          {
            candidate_id: "broken-ready",
            status: "ready",
            source_file: "a.sdf",
            original_name: "a.sdf",
            source_format: "sdf",
            source_record_index: 1,
            file: "screening/staging/a.pdbqt",
            sha256: "invalid",
            size_bytes: 10,
          },
        ],
      },
    }),
    /缺少有效的 staging 文件信息/,
  );
});
