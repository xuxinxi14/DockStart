export type LigandImportStatus = "ready" | "duplicate" | "invalid";

export type LigandImportIssue = {
  code: string;
  message: string;
  detail?: string;
  suggestion?: string;
};

export type LigandImportCandidate = {
  id: string;
  status: LigandImportStatus;
  displayName: string;
  sourceFile: string;
  originalName: string;
  sourceFormat: string;
  recordIndex: number;
  stagedFile: string | null;
  sha256: string | null;
  sizeBytes: number | null;
  duplicateOf: string | null;
  issue: LigandImportIssue | null;
  warnings: string[];
};

export type LigandImportPreview = {
  schemaVersion: 1;
  candidates: LigandImportCandidate[];
  counts: {
    total: number;
    ready: number;
    duplicate: number;
    invalid: number;
  };
};

type RawStageResponse = {
  staged?: unknown;
  import_preview?: unknown;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function readString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function basename(value: string): string {
  const parts = value.split(/[\\/]/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : "";
}

function positiveInteger(value: unknown, fallback = 1): number {
  return Number.isInteger(value) && Number(value) > 0 ? Number(value) : fallback;
}

function displayName(
  recordName: string,
  originalName: string,
  sourceFile: string,
  stagedFile: string,
  recordIndex: number,
): string {
  return recordName
    || originalName
    || basename(sourceFile)
    || basename(stagedFile)
    || `配体 #${recordIndex}`;
}

function countsFor(candidates: LigandImportCandidate[]): LigandImportPreview["counts"] {
  return {
    total: candidates.length,
    ready: candidates.filter((item) => item.status === "ready").length,
    duplicate: candidates.filter((item) => item.status === "duplicate").length,
    invalid: candidates.filter((item) => item.status === "invalid").length,
  };
}

function normalizeIssue(value: unknown, fallback: string): LigandImportIssue {
  const source = isRecord(value) ? value : {};
  return {
    code: readString(source.code) || "LIGAND_IMPORT_RECORD_INVALID",
    message: readString(source.message) || fallback,
    detail: readString(source.raw_error) || undefined,
    suggestion: readString(source.suggestion) || undefined,
  };
}

function normalizeModernPreview(value: Record<string, unknown>): LigandImportPreview {
  if (value.schema_version !== 1 || !Array.isArray(value.candidates)) {
    throw new Error("配体导入预览结构无效。");
  }
  const seenIds = new Set<string>();
  const candidates = value.candidates.map((raw, offset): LigandImportCandidate => {
    if (!isRecord(raw)) throw new Error(`第 ${offset + 1} 条配体导入记录无效。`);
    const id = readString(raw.candidate_id);
    const status = readString(raw.status) as LigandImportStatus;
    if (!id || seenIds.has(id)) throw new Error("配体导入记录 ID 缺失或重复。");
    if (!["ready", "duplicate", "invalid"].includes(status)) {
      throw new Error(`配体导入记录 ${id} 的状态无效。`);
    }
    seenIds.add(id);

    const sourceFile = readString(raw.source_file);
    const originalName = readString(raw.original_name);
    const sourceFormat = readString(raw.source_format).toLowerCase();
    const recordIndex = positiveInteger(raw.source_record_index);
    const recordName = readString(raw.source_record_name);
    const stagedFile = readString(raw.file);
    const sha256 = readString(raw.sha256).toLowerCase();
    const sizeBytes = Number.isInteger(raw.size_bytes) && Number(raw.size_bytes) > 0
      ? Number(raw.size_bytes)
      : null;
    const duplicateOf = readString(raw.duplicate_of);

    if (!sourceFile || !originalName || !["pdbqt", "sdf", "mol"].includes(sourceFormat)) {
      throw new Error(`配体导入记录 ${id} 的来源信息不完整。`);
    }
    if (status === "ready" && (
      !stagedFile
      || !/^[0-9a-f]{64}$/.test(sha256)
      || sizeBytes === null
    )) {
      throw new Error(`可用配体 ${id} 缺少有效的 staging 文件信息。`);
    }
    if (status === "duplicate" && !duplicateOf) {
      throw new Error(`重复配体 ${id} 缺少 canonical 记录。`);
    }

    return {
      id,
      status,
      displayName: displayName(recordName, originalName, sourceFile, stagedFile, recordIndex),
      sourceFile,
      originalName,
      sourceFormat,
      recordIndex,
      stagedFile: stagedFile || null,
      sha256: sha256 || null,
      sizeBytes,
      duplicateOf: duplicateOf || null,
      issue: status === "invalid"
        ? normalizeIssue(raw.error, "该分子记录无法导入。")
        : null,
      warnings: Array.isArray(raw.warnings)
        ? raw.warnings.map(readString).filter(Boolean)
        : [],
    };
  });

  return {
    schemaVersion: 1,
    candidates,
    counts: countsFor(candidates),
  };
}

function normalizeLegacyStaged(value: unknown): LigandImportPreview {
  const rawItems = Array.isArray(value) ? value : [];
  const seenFiles = new Set<string>();
  const candidates: LigandImportCandidate[] = [];
  rawItems.forEach((raw, offset) => {
    if (!isRecord(raw)) return;
    const stagedFile = readString(raw.file);
    if (!stagedFile || seenFiles.has(stagedFile)) return;
    seenFiles.add(stagedFile);
    const originalName = readString(raw.original_name);
    const sourceFile = readString(raw.source_file);
    const sourceFormat = readString(raw.source_format).toLowerCase() || "pdbqt";
    const recordIndex = positiveInteger(raw.source_record_index);
    const recordName = readString(raw.source_record_name);
    const id = `legacy:${stagedFile}`;
    candidates.push({
      id,
      status: "ready",
      displayName: displayName(recordName, originalName, sourceFile, stagedFile, recordIndex),
      sourceFile: sourceFile || stagedFile,
      originalName: originalName || basename(sourceFile) || basename(stagedFile),
      sourceFormat,
      recordIndex,
      stagedFile,
      sha256: readString(raw.sha256).toLowerCase() || null,
      sizeBytes: Number.isInteger(raw.size_bytes) && Number(raw.size_bytes) > 0
        ? Number(raw.size_bytes)
        : null,
      duplicateOf: null,
      issue: null,
      warnings: [],
    });
  });
  return {
    schemaVersion: 1,
    candidates,
    counts: countsFor(candidates),
  };
}

export function normalizeLigandImportPreview(response: unknown): LigandImportPreview {
  if (!isRecord(response)) throw new Error("配体导入响应不是 JSON 对象。");
  const source = response as RawStageResponse;
  if (isRecord(source.import_preview)) {
    return normalizeModernPreview(source.import_preview);
  }
  return normalizeLegacyStaged(source.staged);
}

export function defaultLigandSelection(preview: LigandImportPreview): Set<string> {
  return new Set(
    preview.candidates
      .filter((item) => item.status === "ready" && item.stagedFile)
      .map((item) => item.id),
  );
}

export function selectAllReadyLigands(preview: LigandImportPreview): Set<string> {
  return defaultLigandSelection(preview);
}

export function toggleLigandSelection(
  preview: LigandImportPreview,
  selection: ReadonlySet<string>,
  candidateId: string,
): Set<string> {
  const candidate = preview.candidates.find((item) => item.id === candidateId);
  if (!candidate || candidate.status !== "ready" || !candidate.stagedFile) {
    return new Set(selection);
  }
  const next = new Set(selection);
  if (next.has(candidateId)) next.delete(candidateId);
  else next.add(candidateId);
  return next;
}

export function selectedLigandFiles(
  preview: LigandImportPreview,
  selection: ReadonlySet<string>,
): string[] {
  const seen = new Set<string>();
  const files: string[] = [];
  for (const candidate of preview.candidates) {
    if (
      candidate.status !== "ready"
      || !candidate.stagedFile
      || !selection.has(candidate.id)
      || seen.has(candidate.stagedFile)
    ) {
      continue;
    }
    seen.add(candidate.stagedFile);
    files.push(candidate.stagedFile);
  }
  return files;
}
