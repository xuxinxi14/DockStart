import type {
  DockStartProject,
  VinaMapsCurrentContext,
  VinaMapsManifest,
  VinaMapsStatusResponse,
} from "../types";

const SHA256_PATTERN = /^[0-9a-f]{64}$/i;
const RAW_MAPS_ATTESTATION_STATEMENT =
  "source maps belong to the stated receptor and scoring function";

export type RawMapsConfirmations = {
  receptor: boolean;
  box: boolean;
  scoring: boolean;
};

export type VinaMapsImportOptions = {
  activate: true;
  attestation?: {
    version: number;
    confirmed: true;
    scoring_function: "vina" | "vinardo";
    receptor_sha256: string;
    vina_binary_sha256: string;
    statement: string;
  };
};

export function vinaMapsCompatibilityIssues(project: DockStartProject): string[] {
  const issues: string[] = [];
  const receptorMode =
    project.docking_protocol?.receptor_mode
    ?? project.docking_protocol?.mode
    ?? "rigid";
  const runMode = project.docking_protocol?.run_mode ?? "dock";

  if (receptorMode !== "rigid") {
    issues.push("仅支持刚性受体");
  }
  if (runMode !== "dock") {
    issues.push("仅支持全局对接");
  }
  if (project.docking_protocol?.autobox === true) {
    issues.push("不支持 autobox");
  }
  return issues;
}

export function vinaMapsCurrentContext(
  status: VinaMapsStatusResponse | null,
): VinaMapsCurrentContext | null {
  return status?.current_context ?? null;
}

export function buildRawVinaMapsImportOptions(
  status: VinaMapsStatusResponse | null,
  confirmations: RawMapsConfirmations,
): VinaMapsImportOptions | null {
  if (!confirmations.receptor || !confirmations.box || !confirmations.scoring) {
    return null;
  }

  const context = vinaMapsCurrentContext(status);
  const template = context?.raw_import_attestation_template;
  const scoring = String(
    template?.scoring_function ?? context?.scoring_function ?? "",
  ).toLowerCase();
  const receptorSha = String(
    template?.receptor_sha256 ?? context?.receptor?.source_sha256 ?? "",
  ).toLowerCase();
  const vinaSha = String(
    template?.vina_binary_sha256 ?? context?.vina?.sha256 ?? "",
  ).toLowerCase();

  if (
    (scoring !== "vina" && scoring !== "vinardo")
    || !SHA256_PATTERN.test(receptorSha)
    || !SHA256_PATTERN.test(vinaSha)
  ) {
    return null;
  }

  return {
    activate: true,
    attestation: {
      version:
        typeof template?.version === "number" && Number.isInteger(template.version)
          ? template.version
          : 1,
      confirmed: true,
      scoring_function: scoring,
      receptor_sha256: receptorSha,
      vina_binary_sha256: vinaSha,
      statement: template?.statement || RAW_MAPS_ATTESTATION_STATEMENT,
    },
  };
}
function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function xyzText(
  value: { x?: number; y?: number; z?: number } | undefined,
  digits = 2,
): string {
  if (!value || !finite(value.x) || !finite(value.y) || !finite(value.z)) {
    return "未记录";
  }
  return `${value.x.toFixed(digits)} × ${value.y.toFixed(digits)} × ${value.z.toFixed(digits)}`;
}

export function vinaMapsGridSummary(manifest: VinaMapsManifest | null | undefined): {
  center: string;
  size: string;
  elements: string;
  spacing: string;
} {
  const requested = manifest?.grid?.requested_box;
  const center = requested?.center ?? manifest?.grid?.center;
  const size = requested?.size ?? manifest?.grid?.actual_size;
  const nelements = manifest?.grid?.nelements;
  return {
    center: xyzText(center),
    size: xyzText(size),
    elements: xyzText(nelements, 0),
    spacing: finite(manifest?.grid?.spacing)
      ? `${manifest.grid.spacing.toFixed(3)} Å`
      : "未记录",
  };
}
