import type { VinaCliFeatureCapability, VinaRunMode, VinaSettings } from "../types";

export const VINA_EXPERT_TOGGLE_KEYS = ["no_refine", "force_even_voxels"] as const;
export type VinaExpertToggleKey = (typeof VINA_EXPERT_TOGGLE_KEYS)[number];
export type VinaTextKey = Exclude<keyof VinaSettings, VinaExpertToggleKey>;
export type VinaNumericKey = Exclude<VinaTextKey, "scoring">;
export type VinaForm = Record<VinaTextKey, string> & Record<VinaExpertToggleKey, boolean>;

export const VINA_NUMERIC_ADVANCED_KEYS = ["max_evals", "min_rmsd", "spacing", "verbosity"] as const;
export const VINA_OPTIONAL_ADVANCED_KEYS = ["unbound_energy"] as const;
export const VINA_ADVANCED_KEYS = [
  ...VINA_NUMERIC_ADVANCED_KEYS,
  ...VINA_OPTIONAL_ADVANCED_KEYS,
  ...VINA_EXPERT_TOGGLE_KEYS,
] as const;
export type VinaAdvancedKey = (typeof VINA_ADVANCED_KEYS)[number];

const VINA_TEXT_KEYS: readonly VinaTextKey[] = [
  "scoring",
  "exhaustiveness",
  "max_evals",
  "num_modes",
  "min_rmsd",
  "energy_range",
  "spacing",
  "verbosity",
  "unbound_energy",
  "cpu",
  "seed",
];

export const VINA_ADVANCED_DEFAULTS: Pick<VinaForm, VinaAdvancedKey> = {
  max_evals: "0",
  min_rmsd: "1",
  spacing: "0.375",
  verbosity: "1",
  unbound_energy: "",
  no_refine: false,
  force_even_voxels: false,
};

export type VinaExpertToggleControlState = {
  status: "supported" | "unsupported" | "unknown";
  disabled: boolean;
  blocking: boolean;
  label: string;
};

export type VinaFormContext = {
  engine?: "vina" | "ad4_maps";
  runMode: VinaRunMode;
  receptorMode?: "rigid" | "flexible";
};

export function vinaSettingsToForm(vina: VinaSettings): VinaForm {
  return {
    scoring: vina.scoring ?? "vina",
    exhaustiveness: String(vina.exhaustiveness),
    max_evals: String(vina.max_evals ?? 0),
    num_modes: String(vina.num_modes),
    min_rmsd: String(vina.min_rmsd ?? 1),
    energy_range: String(vina.energy_range),
    spacing: String(vina.spacing ?? 0.375),
    verbosity: String(vina.verbosity ?? 1),
    unbound_energy: vina.unbound_energy === null || vina.unbound_energy === undefined
      ? ""
      : String(vina.unbound_energy),
    no_refine: vina.no_refine === true,
    force_even_voxels: vina.force_even_voxels === true,
    cpu: String(vina.cpu),
    seed: vina.seed === null ? "" : String(vina.seed),
  };
}

export function parseVinaForm(form: VinaForm, context?: VinaFormContext): VinaSettings | null {
  if (typeof form.no_refine !== "boolean" || typeof form.force_even_voxels !== "boolean") return null;
  const scoring = form.scoring.trim().toLowerCase();
  const exhaustiveness = Number(form.exhaustiveness);
  const maxEvals = Number(form.max_evals);
  const numModes = Number(form.num_modes);
  const minRmsd = Number(form.min_rmsd);
  const energyRange = Number(form.energy_range);
  const spacing = Number(form.spacing);
  const verbosity = Number(form.verbosity);
  const unboundEnergyText = form.unbound_energy.trim();
  const unboundEnergyNumber = unboundEnergyText ? Number(unboundEnergyText) : null;
  const unboundEnergyApplicable = !context
    || getApplicableAdvancedVinaKeys(context.engine, context.runMode, context.receptorMode).includes("unbound_energy");
  const cpu = Number(form.cpu);
  const seed = form.seed.trim() ? Number(form.seed) : null;
  if (scoring !== "vina" && scoring !== "vinardo") return null;
  if (!Number.isInteger(exhaustiveness) || exhaustiveness <= 0) return null;
  if (!Number.isInteger(maxEvals) || maxEvals < 0 || maxEvals > 2_147_483_647) return null;
  if (!Number.isInteger(numModes) || numModes <= 0) return null;
  if (!Number.isFinite(minRmsd) || minRmsd < 0 || minRmsd > 100) return null;
  if (!Number.isFinite(energyRange) || energyRange <= 0) return null;
  if (!Number.isFinite(spacing) || spacing < 0.1 || spacing > 2) return null;
  if (verbosity !== 1 && verbosity !== 2) return null;
  if (unboundEnergyApplicable && unboundEnergyNumber !== null && !Number.isFinite(unboundEnergyNumber)) return null;
  if (!Number.isInteger(cpu) || cpu < 0) return null;
  if (seed !== null && !Number.isInteger(seed)) return null;
  return {
    scoring,
    exhaustiveness,
    max_evals: maxEvals,
    num_modes: numModes,
    min_rmsd: minRmsd,
    energy_range: energyRange,
    spacing,
    verbosity,
    unbound_energy: unboundEnergyNumber !== null && Number.isFinite(unboundEnergyNumber)
      ? unboundEnergyNumber
      : null,
    no_refine: form.no_refine,
    force_even_voxels: form.force_even_voxels,
    cpu,
    seed,
  };
}

export function vinaFormsEqual(left: VinaForm, right: VinaForm): boolean {
  if (left.no_refine !== right.no_refine || left.force_even_voxels !== right.force_even_voxels) return false;
  return VINA_TEXT_KEYS.every((key) => {
    if (key === "scoring") return left[key] === right[key];
    if (key === "seed" || key === "unbound_energy") {
      const leftEmpty = left[key].trim() === "";
      const rightEmpty = right[key].trim() === "";
      if (leftEmpty || rightEmpty) return leftEmpty && rightEmpty;
    }
    return Number(left[key]) === Number(right[key]);
  });
}

export function customizedAdvancedVinaCount(
  form: VinaForm,
  keys: readonly VinaAdvancedKey[] = VINA_ADVANCED_KEYS,
): number {
  return keys.filter((key) => {
    if (key === "no_refine" || key === "force_even_voxels") {
      return form[key] !== VINA_ADVANCED_DEFAULTS[key];
    }
    if (key === "unbound_energy") return form.unbound_energy.trim() !== "";
    return Number(form[key]) !== Number(VINA_ADVANCED_DEFAULTS[key]);
  }).length;
}

export function resetAdvancedVinaFields(
  form: VinaForm,
  keys: readonly VinaAdvancedKey[] = VINA_ADVANCED_KEYS,
): VinaForm {
  return keys.reduce<VinaForm>(
    (current, key) => ({ ...current, [key]: VINA_ADVANCED_DEFAULTS[key] }),
    form,
  );
}

export function getApplicableAdvancedVinaKeys(
  engine: "vina" | "ad4_maps" | undefined,
  runMode: VinaRunMode,
  receptorMode?: "rigid" | "flexible",
): readonly VinaAdvancedKey[] {
  if (engine === "ad4_maps") {
    return runMode === "dock" ? ["max_evals", "min_rmsd", "verbosity"] : [];
  }
  if (runMode === "dock") {
    return ["max_evals", "min_rmsd", "spacing", "verbosity", "no_refine", "force_even_voxels"];
  }
  if (runMode === "score_only") {
    return receptorMode === "flexible"
      ? ["spacing", "verbosity", "no_refine", "force_even_voxels"]
      : ["spacing", "verbosity", "unbound_energy", "no_refine", "force_even_voxels"];
  }
  if (runMode === "local_only") {
    return ["spacing", "verbosity", "no_refine", "force_even_voxels"];
  }
  return [];
}

export function isVinaExpertOptionApplicable(
  engine: "vina" | "ad4_maps" | undefined,
  runMode: VinaRunMode,
  option: VinaExpertToggleKey,
): boolean {
  if (engine === "ad4_maps") return false;
  if (runMode !== "dock" && runMode !== "score_only" && runMode !== "local_only") return false;
  return option === "no_refine" || option === "force_even_voxels";
}

export function getVinaExpertToggleState(
  feature: VinaCliFeatureCapability | undefined,
  checked: boolean,
  busy = false,
): VinaExpertToggleControlState {
  const status = feature?.status === "supported" && feature.supported === true
    ? "supported"
    : feature?.status === "unsupported" && feature.supported === false
      ? "unsupported"
      : "unknown";
  return {
    status,
    disabled: busy || (!checked && status !== "supported"),
    blocking: checked && status !== "supported",
    label: status === "supported"
      ? "当前 Vina 支持"
      : status === "unsupported"
        ? "当前 Vina 不支持"
        : "尚未确认支持",
  };
}

export function getVinaExpertValueState(
  feature: VinaCliFeatureCapability | undefined,
  hasValue: boolean,
  busy = false,
): VinaExpertToggleControlState {
  return getVinaExpertToggleState(feature, hasValue, busy);
}
