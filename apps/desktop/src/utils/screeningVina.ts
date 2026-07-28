import type { VinaSettings } from "../types";

export const SCREENING_ADVANCED_VINA_KEYS = [
  "max_evals",
  "min_rmsd",
  "spacing",
  "verbosity",
  "no_refine",
  "force_even_voxels",
] as const;

export type ScreeningAdvancedVinaKey = (typeof SCREENING_ADVANCED_VINA_KEYS)[number];
export type ScreeningVinaSettings = Omit<VinaSettings, "unbound_energy">;

export type ScreeningAdvancedVinaSummaryItem = {
  key: ScreeningAdvancedVinaKey;
  label: string;
  value: string;
};

const ADVANCED_DEFAULTS: Pick<VinaSettings, ScreeningAdvancedVinaKey> = {
  max_evals: 0,
  min_rmsd: 1,
  spacing: 0.375,
  verbosity: 1,
  no_refine: false,
  force_even_voxels: false,
};

function numberOrDefault(value: number | undefined, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function formatNumber(value: number): string {
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(6)));
}

export function hasCompleteScreeningAdvancedVinaSettings(
  vina: Partial<VinaSettings> | null | undefined,
  inferredFields: readonly string[] = [],
): boolean {
  const inferred = new Set(inferredFields);
  return SCREENING_ADVANCED_VINA_KEYS.every((key) => (
    Object.prototype.hasOwnProperty.call(vina ?? {}, key)
    && !inferred.has(key)
  ));
}

export function buildScreeningVinaSettings(
  vina: VinaSettings,
  cpuPerTask: number,
): ScreeningVinaSettings {
  return {
    scoring: vina.scoring,
    exhaustiveness: vina.exhaustiveness,
    max_evals: numberOrDefault(vina.max_evals, ADVANCED_DEFAULTS.max_evals),
    num_modes: vina.num_modes,
    min_rmsd: numberOrDefault(vina.min_rmsd, ADVANCED_DEFAULTS.min_rmsd),
    energy_range: vina.energy_range,
    spacing: numberOrDefault(vina.spacing, ADVANCED_DEFAULTS.spacing),
    verbosity: numberOrDefault(
      vina.verbosity,
      ADVANCED_DEFAULTS.verbosity,
    ) as VinaSettings["verbosity"],
    no_refine: vina.no_refine === true,
    force_even_voxels: vina.force_even_voxels === true,
    cpu: cpuPerTask,
    seed: vina.seed ?? null,
  };
}

export function serializeScreeningVinaSettings(
  vina: VinaSettings,
  cpuPerTask: number,
): string {
  return JSON.stringify(buildScreeningVinaSettings(vina, cpuPerTask));
}

export function buildScreeningAdvancedVinaSummary(
  vina: Partial<VinaSettings>,
): ScreeningAdvancedVinaSummaryItem[] {
  const maxEvals = numberOrDefault(vina.max_evals, ADVANCED_DEFAULTS.max_evals);
  const minRmsd = numberOrDefault(vina.min_rmsd, ADVANCED_DEFAULTS.min_rmsd);
  const spacing = numberOrDefault(vina.spacing, ADVANCED_DEFAULTS.spacing);
  const verbosity = numberOrDefault(vina.verbosity, ADVANCED_DEFAULTS.verbosity);

  return [
    {
      key: "max_evals",
      label: "评估上限",
      value: maxEvals === 0 ? "Vina 自动" : formatNumber(maxEvals),
    },
    {
      key: "min_rmsd",
      label: "构象最小间距",
      value: `${formatNumber(minRmsd)} Å`,
    },
    {
      key: "spacing",
      label: "网格间距",
      value: `${formatNumber(spacing)} Å`,
    },
    {
      key: "verbosity",
      label: "日志",
      value: verbosity === 1
        ? "标准（1）"
        : verbosity === 2
          ? "详细（2）"
          : formatNumber(verbosity),
    },
    {
      key: "no_refine",
      label: "最终精修",
      value: vina.no_refine === true ? "关闭" : "保留",
    },
    {
      key: "force_even_voxels",
      label: "偶数体素",
      value: vina.force_even_voxels === true ? "强制" : "不强制",
    },
  ];
}
