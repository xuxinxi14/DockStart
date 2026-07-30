import type {
  MacrocyclePreparationEvidence,
  MacrocyclePreparationOptions,
  MacrocycleReviewOptions,
  MacrocycleSelection,
  MacrocycleStatusResponse,
} from "../types";

export type MacrocycleInvoke = (
  command: string,
  args?: Record<string, unknown>,
) => Promise<string>;

const SHA256_PATTERN = /^[0-9a-f]{64}$/i;

export const defaultMacrocycleReviewOptions: MacrocycleReviewOptions = {
  min_ring_size: 7,
  max_breaks: 4,
  allow_atom_type_a_endpoints: false,
  keep_chorded_rings: false,
  keep_equivalent_rings: false,
};

export function normalizeMacrocycleReviewOptions(
  options: MacrocycleReviewOptions,
): MacrocycleReviewOptions {
  const keepChorded = Boolean(options.keep_chorded_rings);
  return {
    min_ring_size: Math.max(7, Math.min(33, Math.trunc(options.min_ring_size || 7))),
    max_breaks: Math.max(1, Math.min(4, Math.trunc(options.max_breaks || 4))),
    allow_atom_type_a_endpoints: Boolean(options.allow_atom_type_a_endpoints),
    keep_chorded_rings: keepChorded,
    keep_equivalent_rings: keepChorded || Boolean(options.keep_equivalent_rings),
  };
}

export function decodeMacrocycleResponse(
  payload: string,
  command: string,
): MacrocycleStatusResponse {
  const parsed = JSON.parse(payload) as MacrocycleStatusResponse;
  if (!parsed || typeof parsed !== "object" || typeof parsed.ok !== "boolean") {
    throw new Error(`${command} 返回结果缺少布尔型 ok 字段。`);
  }
  return parsed;
}

export function createMacrocycleApi(invoke: MacrocycleInvoke) {
  const call = async (
    command: string,
    args: Record<string, unknown>,
  ): Promise<MacrocycleStatusResponse> => (
    decodeMacrocycleResponse(await invoke(command, args), command)
  );

  return {
    getStatus(projectDir: string) {
      return call("get_macrocycle_status", { projectDir });
    },
    review(projectDir: string, options: MacrocycleReviewOptions) {
      return call("review_ligand_macrocycle", {
        projectDir,
        optionsJson: JSON.stringify(normalizeMacrocycleReviewOptions(options)),
      });
    },
    confirmCandidate(projectDir: string, reviewId: string, candidateId: string) {
      return call("confirm_ligand_macrocycle", {
        projectDir,
        reviewId,
        candidateId,
        rigid: false,
      });
    },
    confirmRigid(projectDir: string, reviewId: string) {
      return call("confirm_ligand_macrocycle", {
        projectDir,
        reviewId,
        candidateId: null,
        rigid: true,
      });
    },
    resetConfirmation(projectDir: string) {
      return call("reset_ligand_macrocycle_confirmation", { projectDir });
    },
  };
}

export function macrocycleReviewMatchesOptions(
  status: MacrocycleStatusResponse | null,
  options: MacrocycleReviewOptions,
): boolean {
  const reviewed = status?.review?.options;
  if (!status?.ok || !status.review?.valid || !reviewed) return false;
  const normalized = normalizeMacrocycleReviewOptions(options);
  return (
    Number(reviewed.min_ring_size) === normalized.min_ring_size
    && Number(reviewed.max_breaks) === normalized.max_breaks
    && Boolean(reviewed.allow_atom_type_a_endpoints) === normalized.allow_atom_type_a_endpoints
    && Boolean(reviewed.keep_chorded_rings) === normalized.keep_chorded_rings
    && Boolean(reviewed.keep_equivalent_rings) === normalized.keep_equivalent_rings
  );
}

export function selectionFromMacrocycleStatus(
  status: MacrocycleStatusResponse | null,
): MacrocycleSelection {
  const confirmation = status?.confirmation;
  if (confirmation?.valid && confirmation.selection_mode === "rigid") {
    return { kind: "rigid" };
  }
  if (
    confirmation?.valid
    && confirmation.selection_mode === "candidate"
    && confirmation.candidate_id
  ) {
    return { kind: "candidate", candidateId: confirmation.candidate_id };
  }
  const recommended = status?.review?.recommended_candidate_id;
  return recommended ? { kind: "candidate", candidateId: recommended } : null;
}

export function buildReviewedMacrocyclePreparationOptions(
  status: MacrocycleStatusResponse | null,
  selection: MacrocycleSelection,
  reviewOptions: MacrocycleReviewOptions,
): MacrocyclePreparationOptions | null {
  const review = status?.review;
  const confirmation = status?.confirmation;
  const confirmationSha = confirmation?.record?.sha256 || "";
  if (
    !status?.ok
    || status.can_prepare !== true
    || status.integrity?.ok === false
    || !review?.valid
    || !review.review_id
    || !confirmation?.valid
    || confirmation.review_id !== review.review_id
    || !SHA256_PATTERN.test(confirmationSha)
    || !macrocycleReviewMatchesOptions(status, reviewOptions)
    || !selection
  ) {
    return null;
  }
  if (
    selection.kind === "rigid"
    ? confirmation.selection_mode !== "rigid"
    : (
      confirmation.selection_mode !== "candidate"
      || confirmation.candidate_id !== selection.candidateId
    )
  ) {
    return null;
  }
  return {
    protocol: "meeko_macrocycle",
    macrocycle: {
      mode: "reviewed",
      review_id: review.review_id,
      confirmation_sha256: confirmationSha.toLowerCase(),
    },
  };
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function bondPairs(value: unknown): number[][] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((pair) => (
    Array.isArray(pair)
      && pair.length === 2
      && pair.every((item) => typeof item === "number" && Number.isInteger(item))
      ? [[Number(pair[0]), Number(pair[1])]]
      : []
  ));
}

function normalizedBondKey(pairs: number[][]): string {
  return JSON.stringify(
    pairs
      .map(([left, right]) => left < right ? [left, right] : [right, left])
      .sort(([leftA, rightA], [leftB, rightB]) => leftA - leftB || rightA - rightB),
  );
}

export function extractMacrocyclePreparationEvidence(
  metadataValue: unknown,
): MacrocyclePreparationEvidence | null {
  const metadata = record(metadataValue);
  if (
    metadata.protocol !== "meeko_macrocycle"
    && metadata.method !== "meeko_macrocycle"
    && !metadata.macrocycle_contract
    && !metadata.macrocycle_expected_output_evidence
  ) {
    return null;
  }
  const contract = record(metadata.macrocycle_contract);
  const expectedRecord = record(metadata.macrocycle_expected_output_evidence);
  const protocolEvidence = record(metadata.protocol_evidence);
  const evidence = record(
    protocolEvidence.evidence
    || protocolEvidence.worker
    || protocolEvidence.result
    || protocolEvidence,
  );
  const expectedBonds = bondPairs(
    expectedRecord.exact_bonds
    || expectedRecord.expected_bonds
    || contract.exact_bonds,
  );
  const actualBonds = bondPairs(
    evidence.actual_bonds
    || evidence.bonds_removed,
  );
  const rawGlueCount = evidence.glue_pseudo_atom_count;
  const gluePseudoAtomCount = (
    typeof rawGlueCount === "number" && Number.isInteger(rawGlueCount)
      ? rawGlueCount
      : null
  );
  const selectionMode = String(
    evidence.selection_mode
    || contract.selection_mode
    || "",
  ) as MacrocyclePreparationEvidence["selectionMode"];
  return {
    selectionMode,
    candidateId: String(evidence.candidate_id || contract.candidate_id || ""),
    reviewId: String(evidence.review_id || contract.review_id || ""),
    confirmationSha256: String(
      evidence.confirmation_sha256
      || expectedRecord.confirmation_sha256
      || contract.confirmation_sha256
      || "",
    ),
    expectedBonds,
    actualBonds,
    gluePseudoAtomCount,
    bondsMatch: selectionMode === "rigid"
      ? expectedBonds.length === 0 && actualBonds.length === 0
      : actualBonds.length
        ? normalizedBondKey(expectedBonds) === normalizedBondKey(actualBonds)
        : null,
    meekoVersion: String(evidence.meeko_version || metadata.meeko_version || ""),
    rdkitVersion: String(evidence.rdkit_version || metadata.rdkit_version || ""),
  };
}

export function formatZeroBasedBondPairs(pairs: number[][]): string {
  return pairs.length
    ? pairs.map(([left, right]) => `${left + 1}–${right + 1}`).join("、")
    : "无";
}
