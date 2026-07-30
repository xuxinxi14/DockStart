export type LigandImportStatus =
  | "ready"
  | "duplicate"
  | "review_required"
  | "invalid";

export type LigandImportIssue = {
  code: string;
  message: string;
  detail?: string;
  suggestion?: string;
};

export type LigandChemicalFacts = {
  schemaVersion: number;
  status: string;
  source: string;
  reason: string;
  calculationProfile: string;
  sourceTopologySha256: string;
  rdkitVersion: string;
  formalCharge: number | null;
  heavyAtomCount: number | null;
  rotatableBondCount: number | null;
  fragmentCount: number | null;
  maxRingSize: number | null;
  hasMacrocycle: boolean | null;
  canonicalTopologySha256: string;
  canonicalAtomCount: number | null;
  canonicalBondCount: number | null;
};

export type LigandPreparationEvidence = {
  schemaVersion: number;
  status: string;
  source: string;
  preparationProfile: string;
  hydrogenPolicy: string;
  importId: string;
  recordId: string;
  sourceTopologySha256: string;
  errorCode: string;
  toolchain: {
    pythonVersion: string;
    rdkitVersion: string;
    meekoVersion: string;
  } | null;
  workerScriptSha256: string;
  workerManifestSha256: string;
  workerManifestSizeBytes: number | null;
  raw: Record<string, unknown>;
};

export type LigandPreparationAttempt = {
  attempt: number;
  recordedAt: string;
  status: string;
  sourceTopologyFile: string;
  sourceTopologySha256: string;
  sourceTopologySizeBytes: number | null;
  outputFile: string;
  outputSha256: string;
  outputSizeBytes: number | null;
  error: LigandImportIssue | null;
  chemicalFacts: LigandChemicalFacts | null;
  preparationEvidence: LigandPreparationEvidence | null;
  raw: Record<string, unknown>;
};

export type LigandImportFailureManifest = {
  jsonFile: string;
  jsonSha256: string;
  jsonSizeBytes: number;
  csvFile: string;
  csvSha256: string;
  csvSizeBytes: number;
  failureCount: number;
};

export type LigandImportCandidate = {
  id: string;
  importId: string;
  recordId: string;
  status: LigandImportStatus;
  displayName: string;
  sourceFile: string;
  originalName: string;
  sourceFormat: string;
  recordIndex: number;
  sourceRecordSha256: string;
  sourceRecordSizeBytes: number | null;
  stagedFile: string | null;
  sha256: string | null;
  sizeBytes: number | null;
  duplicateOf: string | null;
  sourceTopologyFile: string | null;
  sourceTopologySha256: string | null;
  sourceTopologySizeBytes: number | null;
  topologyIntegrity: string;
  chemicalFacts: LigandChemicalFacts | null;
  preparationEvidence: LigandPreparationEvidence | null;
  preparationAttempts: LigandPreparationAttempt[];
  retryable: boolean;
  issue: LigandImportIssue | null;
  warnings: string[];
};

export type LigandImportPreview = {
  schemaVersion: 1 | 2;
  importId: string | null;
  revisionSha256: string | null;
  candidates: LigandImportCandidate[];
  counts: {
    total: number;
    ready: number;
    duplicate: number;
    reviewRequired: number;
    invalid: number;
  };
  failureManifest: LigandImportFailureManifest | null;
};

export type LigandTopologyCoverage = {
  selected: number;
  verified: number;
  unavailable: number;
  status: "complete" | "partial" | "unavailable";
};

type RawStageResponse = {
  staged?: unknown;
  import_preview?: unknown;
};

const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const IMPORT_ID_PATTERN = /^import_\d{6}$/;
const RECORD_ID_PATTERN = /^record_[0-9a-f]{64}$/;

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

function positiveIntegerOrNull(value: unknown): number | null {
  return Number.isInteger(value) && Number(value) > 0 ? Number(value) : null;
}

function nonNegativeIntegerOrNull(value: unknown): number | null {
  return Number.isInteger(value) && Number(value) >= 0 ? Number(value) : null;
}

function integerOrNull(value: unknown): number | null {
  return Number.isInteger(value) ? Number(value) : null;
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
    reviewRequired: candidates.filter((item) => item.status === "review_required").length,
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

function normalizeChemicalFacts(
  value: unknown,
  sourceTopologySha256: string,
  label: string,
): LigandChemicalFacts | null {
  if (!isRecord(value)) return null;
  const status = readString(value.status);
  const recordedTopologySha256 = readString(value.source_topology_sha256).toLowerCase();
  const canonicalTopologySha256 = readString(value.canonical_topology_sha256).toLowerCase();
  const facts: LigandChemicalFacts = {
    schemaVersion: Number.isInteger(value.schema_version) ? Number(value.schema_version) : 0,
    status,
    source: readString(value.source),
    reason: readString(value.reason),
    calculationProfile: readString(value.calculation_profile),
    sourceTopologySha256: recordedTopologySha256,
    rdkitVersion: readString(value.rdkit_version),
    formalCharge: integerOrNull(value.formal_charge),
    heavyAtomCount: nonNegativeIntegerOrNull(value.heavy_atom_count),
    rotatableBondCount: nonNegativeIntegerOrNull(value.rotatable_bond_count),
    fragmentCount: nonNegativeIntegerOrNull(value.fragment_count),
    maxRingSize: nonNegativeIntegerOrNull(value.max_ring_size),
    hasMacrocycle: typeof value.has_macrocycle === "boolean" ? value.has_macrocycle : null,
    canonicalTopologySha256,
    canonicalAtomCount: nonNegativeIntegerOrNull(value.canonical_atom_count),
    canonicalBondCount: nonNegativeIntegerOrNull(value.canonical_bond_count),
  };
  if (facts.schemaVersion !== 1 || !["verified", "unavailable"].includes(status)) {
    throw new Error(`${label} 的化学事实结构无效。`);
  }
  if (sourceTopologySha256 && recordedTopologySha256 !== sourceTopologySha256) {
    throw new Error(`${label} 的化学事实与冻结原始拓扑不一致。`);
  }
  if (status === "verified") {
    if (
      facts.source !== "frozen_raw_topology"
      || !facts.rdkitVersion
      || facts.formalCharge === null
      || facts.heavyAtomCount === null
      || facts.rotatableBondCount === null
      || facts.fragmentCount === null
      || facts.fragmentCount < 1
      || facts.maxRingSize === null
      || facts.hasMacrocycle === null
      || !SHA256_PATTERN.test(canonicalTopologySha256)
      || facts.canonicalAtomCount === null
      || facts.canonicalAtomCount < 1
      || facts.canonicalBondCount === null
    ) {
      throw new Error(`${label} 缺少完整、已验证的化学事实。`);
    }
  } else if (!facts.source || !facts.reason) {
    throw new Error(`${label} 缺少化学事实不可用的来源或原因。`);
  }
  return facts;
}

function normalizePreparationEvidence(
  value: unknown,
  label: string,
): LigandPreparationEvidence | null {
  if (!isRecord(value)) return null;
  const toolchain = isRecord(value.toolchain)
    ? {
        pythonVersion: readString(value.toolchain.python_version),
        rdkitVersion: readString(value.toolchain.rdkit_version),
        meekoVersion: readString(value.toolchain.meeko_version),
      }
    : null;
  const result: LigandPreparationEvidence = {
    schemaVersion: Number.isInteger(value.schema_version) ? Number(value.schema_version) : 0,
    status: readString(value.status),
    source: readString(value.source),
    preparationProfile: readString(value.preparation_profile),
    hydrogenPolicy: readString(value.hydrogen_policy),
    importId: readString(value.import_id),
    recordId: readString(value.record_id),
    sourceTopologySha256: readString(value.source_topology_sha256).toLowerCase(),
    errorCode: readString(value.error_code),
    toolchain,
    workerScriptSha256: readString(value.worker_script_sha256).toLowerCase(),
    workerManifestSha256: readString(value.worker_manifest_sha256).toLowerCase(),
    workerManifestSizeBytes: positiveIntegerOrNull(value.worker_manifest_size_bytes),
    raw: { ...value },
  };
  if (result.schemaVersion !== 2 || !result.status) {
    throw new Error(`${label} 的准备证据结构无效。`);
  }
  return result;
}

function normalizePreparationAttempts(
  value: unknown,
  label: string,
): LigandPreparationAttempt[] {
  if (!Array.isArray(value)) return [];
  return value.map((raw, index) => {
    if (!isRecord(raw)) throw new Error(`${label} 的第 ${index + 1} 次准备记录无效。`);
    const attempt = positiveInteger(raw.attempt, 0);
    if (!attempt) throw new Error(`${label} 的准备次数无效。`);
    const sourceTopologySha256 = readString(raw.source_topology_sha256).toLowerCase();
    const outputSha256 = readString(raw.output_sha256).toLowerCase();
    if (sourceTopologySha256 && !SHA256_PATTERN.test(sourceTopologySha256)) {
      throw new Error(`${label} 的准备记录包含无效拓扑 SHA256。`);
    }
    if (outputSha256 && !SHA256_PATTERN.test(outputSha256)) {
      throw new Error(`${label} 的准备记录包含无效输出 SHA256。`);
    }
    return {
      attempt,
      recordedAt: readString(raw.recorded_at),
      status: readString(raw.status),
      sourceTopologyFile: readString(raw.source_topology_file),
      sourceTopologySha256,
      sourceTopologySizeBytes: positiveIntegerOrNull(raw.source_topology_size_bytes),
      outputFile: readString(raw.output_file),
      outputSha256,
      outputSizeBytes: positiveIntegerOrNull(raw.output_size_bytes),
      error: isRecord(raw.error) && Object.keys(raw.error).length
        ? normalizeIssue(raw.error, "配体准备失败。")
        : null,
      chemicalFacts: normalizeChemicalFacts(
        raw.chemical_facts,
        sourceTopologySha256,
        `${label} 的准备记录`,
      ),
      preparationEvidence: normalizePreparationEvidence(
        raw.preparation_evidence,
        `${label} 的准备记录`,
      ),
      raw: { ...raw },
    };
  });
}

function normalizeFailureManifest(value: unknown): LigandImportFailureManifest | null {
  if (!isRecord(value)) return null;
  const result: LigandImportFailureManifest = {
    jsonFile: readString(value.json_file),
    jsonSha256: readString(value.json_sha256).toLowerCase(),
    jsonSizeBytes: Number(value.json_size_bytes),
    csvFile: readString(value.csv_file),
    csvSha256: readString(value.csv_sha256).toLowerCase(),
    csvSizeBytes: Number(value.csv_size_bytes),
    failureCount: Number(value.failure_count),
  };
  if (
    !result.jsonFile
    || !result.csvFile
    || !SHA256_PATTERN.test(result.jsonSha256)
    || !SHA256_PATTERN.test(result.csvSha256)
    || !Number.isInteger(result.jsonSizeBytes)
    || result.jsonSizeBytes <= 0
    || !Number.isInteger(result.csvSizeBytes)
    || result.csvSizeBytes <= 0
    || !Number.isInteger(result.failureCount)
    || result.failureCount < 0
  ) {
    throw new Error("配体准备失败清单结构无效。");
  }
  return result;
}

function normalizeCandidate(
  raw: Record<string, unknown>,
  offset: number,
  schemaVersion: 1 | 2,
  previewImportId: string,
): LigandImportCandidate {
  const id = readString(raw.candidate_id);
  const status = readString(raw.status) as LigandImportStatus;
  const allowed = schemaVersion === 2
    ? ["ready", "duplicate", "review_required", "invalid"]
    : ["ready", "duplicate", "invalid"];
  if (!id || !allowed.includes(status)) {
    throw new Error(`第 ${offset + 1} 条配体导入记录的 ID 或状态无效。`);
  }

  const sourceFile = readString(raw.source_file);
  const originalName = readString(raw.original_name);
  const sourceFormat = readString(raw.source_format).toLowerCase();
  const recordIndex = positiveInteger(raw.source_record_index);
  const recordName = readString(raw.source_record_name);
  const stagedFile = readString(raw.file);
  const sha256 = readString(raw.sha256).toLowerCase();
  const sizeBytes = positiveIntegerOrNull(raw.size_bytes);
  const duplicateOf = readString(raw.duplicate_of);
  if (!sourceFile || !originalName || !["pdbqt", "sdf", "mol"].includes(sourceFormat)) {
    throw new Error(`配体导入记录 ${id} 的来源信息不完整。`);
  }
  if (["ready", "duplicate"].includes(status) && (
    !stagedFile || !SHA256_PATTERN.test(sha256) || sizeBytes === null
  )) {
    throw new Error(`可用配体 ${id} 缺少有效的 staging 文件信息。`);
  }
  if (status === "duplicate" && !duplicateOf) {
    throw new Error(`重复配体 ${id} 缺少 canonical 记录。`);
  }

  const importId = readString(raw.import_id);
  const recordId = readString(raw.record_id);
  const sourceRecordSha256 = readString(raw.source_record_sha256).toLowerCase();
  const sourceRecordSizeBytes = positiveIntegerOrNull(raw.source_record_size_bytes);
  const sourceTopologyFile = readString(raw.source_topology_file);
  const sourceTopologySha256 = readString(raw.source_topology_sha256).toLowerCase();
  const sourceTopologySizeBytes = positiveIntegerOrNull(raw.source_topology_size_bytes);
  const topologyIntegrity = readString(raw.topology_integrity) || "not_available";
  if (schemaVersion === 2) {
    if (
      importId !== previewImportId
      || !RECORD_ID_PATTERN.test(recordId)
      || !SHA256_PATTERN.test(sourceRecordSha256)
      || sourceRecordSizeBytes === null
    ) {
      throw new Error(`配体导入记录 ${id} 缺少有效的 v2 原始记录身份。`);
    }
    const rawFormat = sourceFormat === "sdf" || sourceFormat === "mol";
    if (rawFormat && (
      topologyIntegrity !== "verified"
      || !sourceTopologyFile
      || !SHA256_PATTERN.test(sourceTopologySha256)
      || sourceTopologySizeBytes === null
    )) {
      throw new Error(`配体导入记录 ${id} 缺少已验证的冻结原始拓扑。`);
    }
    if (!rawFormat && topologyIntegrity !== "not_available") {
      throw new Error(`PDBQT 配体 ${id} 的拓扑状态无效。`);
    }
  }

  const chemicalFacts = normalizeChemicalFacts(
    raw.chemical_facts,
    sourceTopologySha256,
    `配体导入记录 ${id}`,
  );
  const preparationEvidence = normalizePreparationEvidence(
    raw.preparation_evidence,
    `配体导入记录 ${id}`,
  );
  const preparationAttempts = normalizePreparationAttempts(
    raw.preparation_attempts,
    `配体导入记录 ${id}`,
  );
  const retryable = raw.retryable === true;
  if (status === "review_required" && retryable) {
    throw new Error(`大环配体 ${id} 不能自动重试准备。`);
  }
  if (schemaVersion === 2) {
    if (!chemicalFacts || !preparationEvidence) {
      throw new Error(`配体导入记录 ${id} 缺少化学事实或准备证据。`);
    }
    if (
      preparationEvidence.importId !== importId
      || preparationEvidence.recordId !== recordId
      || preparationEvidence.sourceTopologySha256 !== sourceTopologySha256
    ) {
      throw new Error(`配体导入记录 ${id} 的准备证据身份不一致。`);
    }
    if (sourceTopologySha256) {
      if (
        !preparationEvidence.toolchain
        || !preparationEvidence.toolchain.pythonVersion
        || !preparationEvidence.toolchain.rdkitVersion
        || !preparationEvidence.toolchain.meekoVersion
        || !preparationEvidence.preparationProfile
        || !preparationEvidence.hydrogenPolicy
        || !SHA256_PATTERN.test(preparationEvidence.workerScriptSha256)
        || !SHA256_PATTERN.test(preparationEvidence.workerManifestSha256)
        || preparationEvidence.workerManifestSizeBytes === null
      ) {
        throw new Error(`配体导入记录 ${id} 缺少完整的准备工具链证据。`);
      }
    } else if (
      preparationEvidence.status !== "not_required"
      || preparationEvidence.source !== "supplied_pdbqt"
    ) {
      throw new Error(`PDBQT 配体 ${id} 的免准备证据无效。`);
    }
    for (const attempt of preparationAttempts) {
      if (attempt.sourceTopologySha256 !== sourceTopologySha256) {
        throw new Error(`配体导入记录 ${id} 的准备历史拓扑身份不一致。`);
      }
      if (
        attempt.preparationEvidence
        && (
          attempt.preparationEvidence.importId !== importId
          || attempt.preparationEvidence.recordId !== recordId
          || attempt.preparationEvidence.sourceTopologySha256 !== sourceTopologySha256
        )
      ) {
        throw new Error(`配体导入记录 ${id} 的准备历史证据身份不一致。`);
      }
    }
  }

  return {
    id,
    importId,
    recordId,
    status,
    displayName: displayName(recordName, originalName, sourceFile, stagedFile, recordIndex),
    sourceFile,
    originalName,
    sourceFormat,
    recordIndex,
    sourceRecordSha256,
    sourceRecordSizeBytes,
    stagedFile: stagedFile || null,
    sha256: sha256 || null,
    sizeBytes,
    duplicateOf: duplicateOf || null,
    sourceTopologyFile: sourceTopologyFile || null,
    sourceTopologySha256: sourceTopologySha256 || null,
    sourceTopologySizeBytes,
    topologyIntegrity,
    chemicalFacts,
    preparationEvidence,
    preparationAttempts,
    retryable,
    issue: status === "invalid" || status === "review_required"
      ? normalizeIssue(
          raw.error,
          status === "review_required"
            ? "该大环配体需要先完成正式人工审查。"
            : "该分子记录无法导入。",
        )
      : null,
    warnings: Array.isArray(raw.warnings)
      ? raw.warnings.map(readString).filter(Boolean)
      : [],
  };
}

function normalizeModernPreview(value: Record<string, unknown>): LigandImportPreview {
  const schemaVersion = value.schema_version;
  if ((schemaVersion !== 1 && schemaVersion !== 2) || !Array.isArray(value.candidates)) {
    throw new Error("配体导入预览结构无效。");
  }
  const importId = readString(value.import_id);
  const revisionSha256 = readString(value.revision_sha256).toLowerCase();
  if (schemaVersion === 2 && (
    !IMPORT_ID_PATTERN.test(importId) || !SHA256_PATTERN.test(revisionSha256)
  )) {
    throw new Error("配体导入预览缺少有效的 import ID 或 revision。");
  }

  const seenIds = new Set<string>();
  const candidates = value.candidates.map((raw, offset) => {
    if (!isRecord(raw)) throw new Error(`第 ${offset + 1} 条配体导入记录无效。`);
    const candidate = normalizeCandidate(raw, offset, schemaVersion, importId);
    if (seenIds.has(candidate.id)) throw new Error("配体导入记录 ID 缺失或重复。");
    seenIds.add(candidate.id);
    return candidate;
  });
  const failureManifest = schemaVersion === 2
    ? normalizeFailureManifest(value.failure_manifest)
    : null;
  const counts = countsFor(candidates);
  if (schemaVersion === 2 && failureManifest?.failureCount !== (
    counts.invalid + counts.reviewRequired
  )) {
    throw new Error("配体准备失败清单与导入记录数量不一致。");
  }
  return {
    schemaVersion,
    importId: importId || null,
    revisionSha256: revisionSha256 || null,
    candidates,
    counts,
    failureManifest,
  };
}

function normalizeLegacyStaged(value: unknown): LigandImportPreview {
  const rawItems = Array.isArray(value) ? value : [];
  const seenFiles = new Set<string>();
  const candidates: LigandImportCandidate[] = [];
  rawItems.forEach((raw) => {
    if (!isRecord(raw)) return;
    const stagedFile = readString(raw.file);
    if (!stagedFile || seenFiles.has(stagedFile)) return;
    seenFiles.add(stagedFile);
    const originalName = readString(raw.original_name);
    const sourceFile = readString(raw.source_file);
    const sourceFormat = readString(raw.source_format).toLowerCase() || "pdbqt";
    const recordIndex = positiveInteger(raw.source_record_index);
    const recordName = readString(raw.source_record_name);
    candidates.push({
      id: `legacy:${stagedFile}`,
      importId: "",
      recordId: "",
      status: "ready",
      displayName: displayName(recordName, originalName, sourceFile, stagedFile, recordIndex),
      sourceFile: sourceFile || stagedFile,
      originalName: originalName || basename(sourceFile) || basename(stagedFile),
      sourceFormat,
      recordIndex,
      sourceRecordSha256: readString(raw.source_record_sha256).toLowerCase(),
      sourceRecordSizeBytes: positiveIntegerOrNull(raw.source_record_size_bytes),
      stagedFile,
      sha256: readString(raw.sha256).toLowerCase() || null,
      sizeBytes: positiveIntegerOrNull(raw.size_bytes),
      duplicateOf: null,
      sourceTopologyFile: null,
      sourceTopologySha256: null,
      sourceTopologySizeBytes: null,
      topologyIntegrity: "legacy_unverified",
      chemicalFacts: null,
      preparationEvidence: null,
      preparationAttempts: [],
      retryable: false,
      issue: null,
      warnings: [],
    });
  });
  return {
    schemaVersion: 1,
    importId: null,
    revisionSha256: null,
    candidates,
    counts: countsFor(candidates),
    failureManifest: null,
  };
}

export function normalizeLigandImportPreview(response: unknown): LigandImportPreview {
  if (!isRecord(response)) throw new Error("配体导入响应不是 JSON 对象。");
  const source = response as RawStageResponse;
  if (isRecord(source.import_preview)) return normalizeModernPreview(source.import_preview);
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

export function selectedLigandCandidates(
  preview: LigandImportPreview,
  selection: ReadonlySet<string>,
): LigandImportCandidate[] {
  return preview.candidates.filter((candidate) => (
    candidate.status === "ready"
    && Boolean(candidate.stagedFile)
    && selection.has(candidate.id)
  ));
}

export function selectedLigandCandidateIds(
  preview: LigandImportPreview,
  selection: ReadonlySet<string>,
): string[] {
  return selectedLigandCandidates(preview, selection).map((candidate) => candidate.id);
}

export function selectedLigandFiles(
  preview: LigandImportPreview,
  selection: ReadonlySet<string>,
): string[] {
  const selected = selectedLigandCandidates(preview, selection);
  if (preview.schemaVersion === 2) {
    // v2 candidates are logical records. Two distinct frozen topologies may
    // intentionally cross-check against the same physical PDBQT snapshot.
    return selected.map((candidate) => candidate.stagedFile as string);
  }
  const seen = new Set<string>();
  return selected.flatMap((candidate) => {
    const file = candidate.stagedFile as string;
    if (seen.has(file)) return [];
    seen.add(file);
    return [file];
  });
}

export function retryableLigandCandidateIds(preview: LigandImportPreview): string[] {
  return preview.candidates
    .filter((candidate) => candidate.status === "invalid" && candidate.retryable)
    .map((candidate) => candidate.id);
}

export function ligandTopologyCoverage(
  preview: LigandImportPreview,
  selection: ReadonlySet<string>,
): LigandTopologyCoverage {
  const selected = selectedLigandCandidates(preview, selection);
  const verified = selected.filter((candidate) => (
    candidate.topologyIntegrity === "verified"
    && Boolean(candidate.sourceTopologyFile)
    && Boolean(candidate.sourceTopologySha256)
  )).length;
  return {
    selected: selected.length,
    verified,
    unavailable: selected.length - verified,
    status: verified === selected.length && selected.length
      ? "complete"
      : verified
        ? "partial"
        : "unavailable",
  };
}

export function readyLigandCount(preview: LigandImportPreview): number {
  return preview.counts.ready;
}

export function dockingModeForLigandPreview(
  preview: LigandImportPreview,
): "single" | "batch" {
  return readyLigandCount(preview) >= 2 ? "batch" : "single";
}
