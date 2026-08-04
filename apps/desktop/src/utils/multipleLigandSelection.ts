import type {
  LigandImportCandidate,
  LigandImportPreview,
} from "./screeningLigandImport";

export type MultipleLigandCompatibilityInput = {
  engine?: string;
  protocolId?: string;
  receptorMode?: string;
  runMode?: string;
  capabilityChecked?: boolean;
  capabilitySupported?: boolean | null;
};

export function readyMultipleLigandCandidates(
  preview: LigandImportPreview | null,
): LigandImportCandidate[] {
  return (preview?.candidates ?? []).filter(
    (candidate) => candidate.status === "ready" && Boolean(candidate.stagedFile),
  );
}

export function normalizeMultipleLigandSelection(
  candidates: readonly LigandImportCandidate[],
  selection: readonly string[],
): string[] {
  const available = new Set(candidates.map((candidate) => candidate.id));
  const seen = new Set<string>();
  const normalized: string[] = [];
  for (const id of selection) {
    if (!available.has(id) || seen.has(id)) continue;
    seen.add(id);
    normalized.push(id);
    if (normalized.length === 2) break;
  }
  return normalized;
}

export function toggleMultipleLigandMember(
  candidates: readonly LigandImportCandidate[],
  selection: readonly string[],
  candidateId: string,
): { selection: string[]; limitReached: boolean } {
  const normalized = normalizeMultipleLigandSelection(candidates, selection);
  if (!candidates.some((candidate) => candidate.id === candidateId)) {
    return { selection: normalized, limitReached: false };
  }
  if (normalized.includes(candidateId)) {
    return {
      selection: normalized.filter((id) => id !== candidateId),
      limitReached: false,
    };
  }
  if (normalized.length >= 2) {
    return { selection: normalized, limitReached: true };
  }
  return { selection: [...normalized, candidateId], limitReached: false };
}

export function moveMultipleLigandMember(
  selection: readonly string[],
  candidateId: string,
  direction: -1 | 1,
): string[] {
  const next = [...selection];
  const index = next.indexOf(candidateId);
  const destination = index + direction;
  if (index < 0 || destination < 0 || destination >= next.length) return next;
  [next[index], next[destination]] = [next[destination], next[index]];
  return next;
}

export function selectedMultipleLigandFiles(
  candidates: readonly LigandImportCandidate[],
  selection: readonly string[],
): string[] {
  const byId = new Map(candidates.map((candidate) => [candidate.id, candidate]));
  return normalizeMultipleLigandSelection(candidates, selection)
    .map((id) => byId.get(id)?.stagedFile ?? "")
    .filter(Boolean);
}

export function multipleLigandCompatibilityIssues(
  input: MultipleLigandCompatibilityInput,
): string[] {
  const issues: string[] = [];
  const engine = String(input.engine || "vina").toLowerCase();
  const protocolId = String(input.protocolId || "").toLowerCase();
  const standardAd4 = engine === "ad4_maps" && (!protocolId || protocolId === "ad4_maps");
  if (!standardAd4 && (
    engine !== "vina"
    || protocolId === "ad4zn_beta"
    || protocolId === "vina_maps"
  )) {
    issues.push("仅支持 Vina/Vinardo 或标准 AutoDock4 maps");
  }
  if (String(input.receptorMode || "rigid").toLowerCase() !== "rigid") {
    issues.push("仅支持刚性受体");
  }
  if (String(input.runMode || "dock").toLowerCase() !== "dock") {
    issues.push("仅支持全局对接");
  }
  if (!input.capabilityChecked) {
    issues.push("尚未确认当前 Vina 的多配体命令能力");
  } else if (input.capabilitySupported !== true) {
    issues.push("当前 Vina 不支持一个 --ligand 后跟多个输入文件");
  }
  return issues;
}
