export type ScreeningArchiveExportIntegrity = {
  bundle: string;
  resource: string;
  attempt: string;
  output: string;
};

export type ScreeningArchiveExportRecord = {
  archive_id: string;
  zip_file: string;
  zip_sha256: string;
  size_bytes: number;
  entry_count: number;
  payload_uncompressed_bytes: number;
  payload_tree_sha256: string;
  source_integrity: ScreeningArchiveExportIntegrity;
  warnings: string[];
  overwritten: boolean;
  exported_at?: string;
};

export type ScreeningArchiveExportResponse = Partial<ScreeningArchiveExportRecord> & {
  ok: boolean;
  project_dir?: string;
  message?: string;
  error?: {
    code?: string;
    title?: string;
    message?: string;
    raw_error?: string;
    suggestion?: string;
    details?: unknown;
  };
};

export type ScreeningArchiveExportConflict = {
  destination_file: string;
  destination_sha256: string;
  destination_size_bytes: number;
};

const SHA256_PATTERN = /^[0-9a-f]{64}$/i;
const ARCHIVE_ID_PATTERN = /^screening_\d{3,}_\d{14}(?:_\d{2})?$/;
const INTEGRITY_STATES = new Set([
  "verified",
  "not_applicable",
  "partially_verified",
  "legacy_unverified",
]);

function isIntegrityState(value: unknown): value is string {
  return typeof value === "string" && INTEGRITY_STATES.has(value);
}

export function screeningArchiveExportDefaultName(archiveId: string): string {
  const safeId = ARCHIVE_ID_PATTERN.test(archiveId) ? archiveId : "screening_archive";
  return `DockStart_${safeId}.zip`;
}

export function ensureZipExtension(path: string): string {
  return path.toLowerCase().endsWith(".zip") ? path : `${path}.zip`;
}

export function formatArchiveExportBytes(value: number): string {
  if (!Number.isFinite(value) || value < 0) return "未记录";
  if (value < 1024) return `${Math.trunc(value)} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(2)} GB`;
}

export function archiveExportIntegrityLabel(
  integrity: ScreeningArchiveExportIntegrity,
): string {
  const label = (value: string) => {
    if (value === "verified") return "已核对";
    if (value === "not_applicable") return "不适用";
    if (value === "partially_verified") return "部分证据";
    if (value === "legacy_unverified") return "旧记录未完全验证";
    return "未记录";
  };
  return [
    `资源策略：${label(integrity.resource)}`,
    `逐次运行：${label(integrity.attempt)}`,
    `构象与结果产物：${label(integrity.output)}`,
  ].join("；");
}

export function readScreeningArchiveExportConflict(
  response: ScreeningArchiveExportResponse,
): ScreeningArchiveExportConflict | null {
  if (
    response.ok
    || response.error?.code !== "SCREENING_ARCHIVE_EXPORT_EXISTS"
    || !response.error.details
    || typeof response.error.details !== "object"
    || Array.isArray(response.error.details)
  ) {
    return null;
  }
  const details = response.error.details as Record<string, unknown>;
  if (
    typeof details.destination_file !== "string"
    || !details.destination_file.trim()
    || typeof details.destination_sha256 !== "string"
    || !SHA256_PATTERN.test(details.destination_sha256)
    || typeof details.destination_size_bytes !== "number"
    || !Number.isSafeInteger(details.destination_size_bytes)
    || details.destination_size_bytes < 0
  ) {
    return null;
  }
  return {
    destination_file: details.destination_file,
    destination_sha256: details.destination_sha256.toLowerCase(),
    destination_size_bytes: details.destination_size_bytes,
  };
}

export function readScreeningArchiveExportRecord(
  response: ScreeningArchiveExportResponse,
): ScreeningArchiveExportRecord | null {
  if (
    !response.ok
    || typeof response.project_dir !== "string"
    || !response.project_dir.trim()
    || typeof response.archive_id !== "string"
    || !ARCHIVE_ID_PATTERN.test(response.archive_id)
    || typeof response.zip_file !== "string"
    || !response.zip_file.trim()
    || typeof response.zip_sha256 !== "string"
    || !SHA256_PATTERN.test(response.zip_sha256)
    || typeof response.size_bytes !== "number"
    || !Number.isSafeInteger(response.size_bytes)
    || response.size_bytes <= 0
    || typeof response.entry_count !== "number"
    || !Number.isSafeInteger(response.entry_count)
    || response.entry_count < 1
    || typeof response.payload_uncompressed_bytes !== "number"
    || !Number.isSafeInteger(response.payload_uncompressed_bytes)
    || response.payload_uncompressed_bytes < 0
    || typeof response.payload_tree_sha256 !== "string"
    || !SHA256_PATTERN.test(response.payload_tree_sha256)
    || !response.source_integrity
    || typeof response.source_integrity !== "object"
    || Array.isArray(response.source_integrity)
    || response.source_integrity.bundle !== "verified"
    || !isIntegrityState(response.source_integrity.resource)
    || !isIntegrityState(response.source_integrity.attempt)
    || !isIntegrityState(response.source_integrity.output)
    || !Array.isArray(response.warnings)
    || !response.warnings.every(
      (value) => typeof value === "string" && Boolean(value.trim()),
    )
    || typeof response.overwritten !== "boolean"
    || typeof response.exported_at !== "string"
    || !response.exported_at.trim()
    || Number.isNaN(Date.parse(response.exported_at))
  ) {
    return null;
  }
  return {
    archive_id: response.archive_id,
    zip_file: response.zip_file,
    zip_sha256: response.zip_sha256.toLowerCase(),
    size_bytes: response.size_bytes,
    entry_count: response.entry_count,
    payload_uncompressed_bytes: response.payload_uncompressed_bytes,
    payload_tree_sha256: response.payload_tree_sha256.toLowerCase(),
    source_integrity: response.source_integrity,
    warnings: response.warnings,
    overwritten: response.overwritten,
    exported_at: response.exported_at,
  };
}
