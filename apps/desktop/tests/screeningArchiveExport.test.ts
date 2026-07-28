import assert from "node:assert/strict";
import test from "node:test";

import {
  archiveExportIntegrityLabel,
  ensureZipExtension,
  formatArchiveExportBytes,
  readScreeningArchiveExportConflict,
  readScreeningArchiveExportRecord,
  screeningArchiveExportDefaultName,
} from "../src/utils/screeningArchiveExport.ts";

const SHA = "a".repeat(64);

test("归档导出默认名称只接受安全归档编号", () => {
  assert.equal(
    screeningArchiveExportDefaultName("screening_001_20260728123456"),
    "DockStart_screening_001_20260728123456.zip",
  );
  assert.equal(
    screeningArchiveExportDefaultName("../screening_001_20260728123456"),
    "DockStart_screening_archive.zip",
  );
});

test("ZIP 扩展名、大小和完整性状态使用稳定用户文案", () => {
  assert.equal(ensureZipExtension("D:\\结果\\batch"), "D:\\结果\\batch.zip");
  assert.equal(ensureZipExtension("D:\\结果\\batch.ZIP"), "D:\\结果\\batch.ZIP");
  assert.equal(formatArchiveExportBytes(1024), "1.0 KB");
  assert.equal(formatArchiveExportBytes(3 * 1024 ** 2), "3.0 MB");
  assert.equal(
    archiveExportIntegrityLabel({
      bundle: "verified",
      resource: "verified",
      attempt: "partially_verified",
      output: "legacy_unverified",
    }),
    "资源策略：已核对；逐次运行：部分证据；构象与结果产物：旧记录未完全验证",
  );
});

test("已有目标只有在返回完整身份时才展示冲突诊断", () => {
  const conflict = readScreeningArchiveExportConflict({
    ok: false,
    error: {
      code: "SCREENING_ARCHIVE_EXPORT_EXISTS",
      details: {
        destination_file: "D:\\结果\\batch.zip",
        destination_sha256: SHA.toUpperCase(),
        destination_size_bytes: 2048,
      },
    },
  });
  assert.deepEqual(conflict, {
    destination_file: "D:\\结果\\batch.zip",
    destination_sha256: SHA,
    destination_size_bytes: 2048,
  });
  assert.equal(readScreeningArchiveExportConflict({
    ok: false,
    error: {
      code: "SCREENING_ARCHIVE_EXPORT_EXISTS",
      details: {
        destination_file: "D:\\结果\\batch.zip",
        destination_sha256: "invalid",
        destination_size_bytes: 2048,
      },
    },
  }), null);
  assert.equal(readScreeningArchiveExportConflict({
    ok: true,
    error: {
      code: "SCREENING_ARCHIVE_EXPORT_EXISTS",
      details: {
        destination_file: "D:\\结果\\batch.zip",
        destination_sha256: SHA,
        destination_size_bytes: 2048,
      },
    },
  }), null);
});

test("成功响应必须包含可核对的 ZIP 与载荷身份", () => {
  const parsed = readScreeningArchiveExportRecord({
    ok: true,
    project_dir: "D:\\项目",
    archive_id: "screening_001_20260728123456",
    zip_file: "D:\\结果\\batch.zip",
    zip_sha256: SHA.toUpperCase(),
    size_bytes: 1024,
    entry_count: 12,
    payload_uncompressed_bytes: 4096,
    payload_tree_sha256: SHA.toUpperCase(),
    source_integrity: {
      bundle: "verified",
      resource: "verified",
      attempt: "verified",
      output: "verified",
    },
    warnings: ["旧记录提示"],
    overwritten: false,
    exported_at: "2026-07-28T13:40:00+08:00",
  });
  assert.ok(parsed);
  assert.equal(parsed.zip_sha256, SHA);
  assert.equal(parsed.payload_tree_sha256, SHA);
  assert.deepEqual(parsed.warnings, ["旧记录提示"]);

  assert.equal(readScreeningArchiveExportRecord({
    ok: true,
    project_dir: "D:\\项目",
    archive_id: "screening_001_20260728123456",
    zip_file: "D:\\结果\\batch.zip",
    zip_sha256: "invalid",
    size_bytes: 1024,
    entry_count: 12,
    payload_uncompressed_bytes: 4096,
    payload_tree_sha256: SHA,
    source_integrity: {
      bundle: "verified",
      resource: "verified",
      attempt: "verified",
      output: "verified",
    },
    warnings: [],
    overwritten: false,
    exported_at: "2026-07-28T13:40:00+08:00",
  }), null);
});

test("成功响应拒绝缺失完整性状态、时间或业务上下文", () => {
  const valid = {
    ok: true,
    project_dir: "D:\\项目",
    archive_id: "screening_001_20260728123456",
    zip_file: "D:\\结果\\batch.zip",
    zip_sha256: SHA,
    size_bytes: 1024,
    entry_count: 12,
    payload_uncompressed_bytes: 4096,
    payload_tree_sha256: SHA,
    source_integrity: {
      bundle: "verified",
      resource: "verified",
      attempt: "verified",
      output: "verified",
    },
    warnings: ["未匿名化"],
    overwritten: false,
    exported_at: "2026-07-28T13:40:00+08:00",
  };

  assert.ok(readScreeningArchiveExportRecord(valid));
  assert.equal(readScreeningArchiveExportRecord({
    ...valid,
    project_dir: "",
  }), null);
  assert.equal(readScreeningArchiveExportRecord({
    ...valid,
    source_integrity: { ...valid.source_integrity, output: "unknown" },
  }), null);
  assert.equal(readScreeningArchiveExportRecord({
    ...valid,
    warnings: undefined,
  }), null);
  assert.equal(readScreeningArchiveExportRecord({
    ...valid,
    exported_at: "not-a-date",
  }), null);
});
