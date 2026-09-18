/**
 * Pure helpers behind the Settings page.
 *
 * Everything in here is intentionally free of Tauri / DOM access so the
 * frontend test runner (node:test) can exercise it directly.  The numeric
 * bounds mirror `backend/dockstart_core/settings.py`; the frontend test suite
 * asserts that the two never drift apart.
 */

import type {
  DockStartSettings,
  DockingDefaults,
  SettingsDiagnosticsResponse,
  SettingsResponse,
} from "../types";

export type DockingDefaultKey =
  | "exhaustiveness"
  | "num_modes"
  | "energy_range"
  | "cpu"
  | "seed";

export type DockingDefaultField = {
  key: DockingDefaultKey;
  label: string;
  hint: string;
  explain: string;
  integer: boolean;
  min: number;
  max: number;
  optional: boolean;
};

/** Inclusive numeric bounds; mirrored from `settings.py`. */
export const DOCKING_DEFAULT_LIMITS: Record<DockingDefaultKey, { min: number; max: number }> = {
  exhaustiveness: { min: 1, max: 128 },
  num_modes: { min: 1, max: 50 },
  energy_range: { min: 0, max: 20 },
  cpu: { min: 0, max: 64 },
  seed: { min: 0, max: 2147483647 },
};

/**
 * Scoring functions accepted by the backend for a global default.
 * `ad4` is intentionally absent: it needs pre-computed affinity maps and is only
 * reachable through the batch screening workflow's `ad4_maps` protocol, while the
 * project-level validator (`validate_vina_params`) accepts `vina`/`vinardo` only.
 */
export const DOCKING_DEFAULT_SCORING_FUNCTIONS = ["vina", "vinardo"] as const;

export const DOCKING_DEFAULT_FIELDS: DockingDefaultField[] = [
  {
    key: "exhaustiveness",
    label: "搜索彻底程度",
    hint: "建议从 8 开始",
    explain:
      "控制 Vina 搜索投入的计算量。值越大搜索越充分、越耗时，但不保证结果一定更好。",
    integer: true,
    min: DOCKING_DEFAULT_LIMITS.exhaustiveness.min,
    max: DOCKING_DEFAULT_LIMITS.exhaustiveness.max,
    optional: false,
  },
  {
    key: "num_modes",
    label: "输出构象数量",
    hint: "建议 9",
    explain: "最多保留多少个候选结合构象，按评分从好到差排列。",
    integer: true,
    min: DOCKING_DEFAULT_LIMITS.num_modes.min,
    max: DOCKING_DEFAULT_LIMITS.num_modes.max,
    optional: false,
  },
  {
    key: "energy_range",
    label: "能量范围",
    hint: "kcal/mol",
    explain: "只保留与最佳构象能量差在此范围内的候选。单位 kcal/mol。",
    integer: false,
    min: DOCKING_DEFAULT_LIMITS.energy_range.min,
    max: DOCKING_DEFAULT_LIMITS.energy_range.max,
    optional: false,
  },
  {
    key: "cpu",
    label: "CPU 线程",
    hint: "0 为自动",
    explain: "用多少 CPU 核心计算；0 表示由 Vina 自行决定。线程越多不一定越快。",
    integer: true,
    min: DOCKING_DEFAULT_LIMITS.cpu.min,
    max: DOCKING_DEFAULT_LIMITS.cpu.max,
    optional: false,
  },
  {
    key: "seed",
    label: "随机种子",
    hint: "留空表示不固定",
    explain:
      "固定随机种子可以让同一批输入得到可复现的结果；留空表示每次运行都不固定。",
    integer: true,
    min: DOCKING_DEFAULT_LIMITS.seed.min,
    max: DOCKING_DEFAULT_LIMITS.seed.max,
    optional: true,
  },
];

/**
 * Literal defaults; mirrored from `project.VinaSettings` and `settings.DockingDefaults`.
 * `tests/settingsForm.test.ts` compares these numbers with the backend source.
 */
export const DEFAULT_DOCKING_DEFAULTS: DockingDefaults = {
  scoring: "vina",
  exhaustiveness: 8,
  num_modes: 9,
  energy_range: 4,
  cpu: 0,
  seed: null,
};

export type DockingDefaultsForm = Record<DockingDefaultKey, string> & { scoring: string };

export const EMPTY_DOCKING_DEFAULTS_FORM: DockingDefaultsForm = {
  scoring: DEFAULT_DOCKING_DEFAULTS.scoring,
  exhaustiveness: String(DEFAULT_DOCKING_DEFAULTS.exhaustiveness),
  num_modes: String(DEFAULT_DOCKING_DEFAULTS.num_modes),
  energy_range: String(DEFAULT_DOCKING_DEFAULTS.energy_range),
  cpu: String(DEFAULT_DOCKING_DEFAULTS.cpu),
  seed: "",
};

export const EMPTY_SETTINGS: DockStartSettings = {
  tool_paths: { vina: "", python: "", autogrid4: "" },
  project: { default_project_dir: "" },
  docking_defaults: { ...DEFAULT_DOCKING_DEFAULTS },
};

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : value === null || value === undefined ? "" : String(value);
}

export function normalizeDockingDefaults(raw: unknown): DockingDefaults {
  const source = asRecord(raw);
  const scoring = asString(source.scoring).trim().toLowerCase();

  return {
    scoring: (DOCKING_DEFAULT_SCORING_FUNCTIONS as readonly string[]).includes(scoring)
      ? scoring
      : DEFAULT_DOCKING_DEFAULTS.scoring,
    exhaustiveness: normalizeBoundedNumber(
      source.exhaustiveness,
      DEFAULT_DOCKING_DEFAULTS.exhaustiveness,
      "exhaustiveness",
      true,
    ),
    num_modes: normalizeBoundedNumber(
      source.num_modes,
      DEFAULT_DOCKING_DEFAULTS.num_modes,
      "num_modes",
      true,
    ),
    energy_range: normalizeBoundedNumber(
      source.energy_range,
      DEFAULT_DOCKING_DEFAULTS.energy_range,
      "energy_range",
      false,
    ),
    cpu: normalizeBoundedNumber(source.cpu, DEFAULT_DOCKING_DEFAULTS.cpu, "cpu", true),
    seed: normalizeOptionalSeed(source.seed),
  };
}

function normalizeBoundedNumber(
  raw: unknown,
  fallback: number,
  key: DockingDefaultKey,
  integer: boolean,
): number {
  if (raw === null || raw === undefined || raw === "") return fallback;
  if (typeof raw === "boolean") return fallback;
  const value = Number(raw);
  if (!Number.isFinite(value)) return fallback;
  // A non-integral value is rejected instead of rounded: these are docking
  // parameters, and silently changing their meaning is worse than falling back.
  if (integer && !Number.isInteger(value)) return fallback;
  const { min, max } = DOCKING_DEFAULT_LIMITS[key];
  return Math.min(max, Math.max(min, value));
}

function normalizeOptionalSeed(raw: unknown): number | null {
  if (raw === null || raw === undefined || raw === "") return null;
  if (typeof raw === "boolean") return null;
  const value = Number(raw);
  if (!Number.isFinite(value) || !Number.isInteger(value)) return null;
  const { min, max } = DOCKING_DEFAULT_LIMITS.seed;
  return Math.min(max, Math.max(min, value));
}

/** Tolerant reader for payloads written by older DockStart versions. */
export function normalizeSettings(raw: unknown): DockStartSettings {
  const source = asRecord(raw);
  const toolPaths = asRecord(source.tool_paths);
  const project = asRecord(source.project);

  return {
    tool_paths: {
      vina: asString(toolPaths.vina),
      python: asString(toolPaths.python),
      autogrid4: asString(toolPaths.autogrid4),
    },
    project: {
      default_project_dir: asString(project.default_project_dir),
    },
    docking_defaults: normalizeDockingDefaults(source.docking_defaults),
  };
}

export function parseSettingsResponse(rawPayload: string): SettingsResponse {
  const parsed = asRecord(JSON.parse(rawPayload));
  const error = asRecord(parsed.error);
  return {
    ok: Boolean(parsed.ok),
    settings_path: asString(parsed.settings_path),
    settings: parsed.settings ? normalizeSettings(parsed.settings) : null,
    error:
      parsed.error === undefined || parsed.error === null
        ? undefined
        : {
            message: asString(error.message) || "设置操作失败。",
            raw_error: asString(error.raw_error),
          },
  };
}

export function parseDiagnosticsResponse(rawPayload: string): SettingsDiagnosticsResponse {
  const parsed = asRecord(JSON.parse(rawPayload));
  const error = asRecord(parsed.error);
  const diagnostics = asRecord(parsed.diagnostics);
  const hasDiagnostics = parsed.diagnostics !== null && typeof parsed.diagnostics === "object";

  return {
    ok: Boolean(parsed.ok),
    settings_path: asString(parsed.settings_path),
    diagnostics: hasDiagnostics
      ? {
          settings_path: asString(diagnostics.settings_path),
          settings_dir: asString(diagnostics.settings_dir),
          dir_exists: Boolean(diagnostics.dir_exists),
          file_exists: Boolean(diagnostics.file_exists),
          file_size_bytes: Number(diagnostics.file_size_bytes) || 0,
          file_modified_at: asString(diagnostics.file_modified_at),
          lock_file: asString(diagnostics.lock_file),
          env_override: asString(diagnostics.env_override),
          readable: Boolean(diagnostics.readable),
          writable: Boolean(diagnostics.writable),
          load: normalizeCheckReport(diagnostics.load),
          write_probe: normalizeCheckReport(diagnostics.write_probe),
          current_settings:
            diagnostics.current_settings === null || diagnostics.current_settings === undefined
              ? null
              : normalizeSettings(diagnostics.current_settings),
        }
      : null,
    error:
      parsed.error === undefined || parsed.error === null
        ? undefined
        : {
            message: asString(error.message) || "设置诊断失败。",
            raw_error: asString(error.raw_error),
          },
  };
}

function normalizeCheckReport(raw: unknown) {
  const source = asRecord(raw);
  return {
    ok: Boolean(source.ok),
    message: asString(source.message),
    raw_error: asString(source.raw_error),
    suggestion: asString(source.suggestion),
  };
}

export type DockingDefaultsParseResult =
  | { ok: true; defaults: DockingDefaults }
  | { ok: false; field: DockingDefaultKey | "scoring"; message: string; suggestion: string; rawError: string };

export function dockingDefaultsToForm(defaults: DockingDefaults): DockingDefaultsForm {
  return {
    scoring: defaults.scoring,
    exhaustiveness: String(defaults.exhaustiveness),
    num_modes: String(defaults.num_modes),
    energy_range: String(defaults.energy_range),
    cpu: String(defaults.cpu),
    seed: defaults.seed === null ? "" : String(defaults.seed),
  };
}

/**
 * Validate the editable form.  Values are rejected rather than silently
 * clamped, because the UI must tell the user exactly what was wrong.
 */
export function parseDockingDefaultsForm(form: DockingDefaultsForm): DockingDefaultsParseResult {
  const scoring = String(form.scoring ?? "").trim().toLowerCase();
  if (!(DOCKING_DEFAULT_SCORING_FUNCTIONS as readonly string[]).includes(scoring)) {
    return {
      ok: false,
      field: "scoring",
      message: "评分函数不受支持。",
      suggestion: `请选择 ${DOCKING_DEFAULT_SCORING_FUNCTIONS.join(" / ")} 之一。`,
      rawError: `scoring=${JSON.stringify(form.scoring)}`,
    };
  }

  const parsed: Partial<Record<DockingDefaultKey, number | null>> = {};

  for (const field of DOCKING_DEFAULT_FIELDS) {
    const raw = String(form[field.key] ?? "").trim();
    if (!raw) {
      if (field.optional) {
        parsed[field.key] = null;
        continue;
      }
      return {
        ok: false,
        field: field.key,
        message: `${field.label}不能为空。`,
        suggestion: `${field.label}需要填写一个 ${field.min} 到 ${field.max} 之间的数值。`,
        rawError: `${field.key}=<empty>`,
      };
    }
    const value = Number(raw);
    if (!Number.isFinite(value)) {
      return {
        ok: false,
        field: field.key,
        message: `${field.label}必须是数字。`,
        suggestion: `${field.label}需要填写一个 ${field.min} 到 ${field.max} 之间的数值。`,
        rawError: `${field.key}=${JSON.stringify(form[field.key])}`,
      };
    }
    if (field.integer && !Number.isInteger(value)) {
      return {
        ok: false,
        field: field.key,
        message: `${field.label}必须是整数。`,
        suggestion: `${field.label}需要填写一个 ${field.min} 到 ${field.max} 之间的整数。`,
        rawError: `${field.key}=${JSON.stringify(form[field.key])}`,
      };
    }
    if (value < field.min || value > field.max) {
      return {
        ok: false,
        field: field.key,
        message: `${field.label}超出允许范围。`,
        suggestion: `${field.label}需要在 ${field.min} 到 ${field.max} 之间，当前是 ${value}。`,
        rawError: `${field.key}=${value}`,
      };
    }
    parsed[field.key] = value;
  }

  return {
    ok: true,
    defaults: {
      scoring,
      exhaustiveness: parsed.exhaustiveness as number,
      num_modes: parsed.num_modes as number,
      energy_range: parsed.energy_range as number,
      cpu: parsed.cpu as number,
      seed: (parsed.seed ?? null) as number | null,
    },
  };
}

export function dockingDefaultsEqual(left: DockingDefaults, right: DockingDefaults): boolean {
  return (
    left.scoring === right.scoring
    && left.exhaustiveness === right.exhaustiveness
    && left.num_modes === right.num_modes
    && left.energy_range === right.energy_range
    && left.cpu === right.cpu
    && left.seed === right.seed
  );
}

export type ToolStatusEntry = {
  status: string;
  version: string;
  path: string;
  message: string;
  rawError: string;
  source: string;
};

export type ToolchainSummary = {
  runtimeMode: string;
  python: ToolStatusEntry | null;
  vina: ToolStatusEntry | null;
};

function readToolEntry(raw: unknown): ToolStatusEntry | null {
  if (raw === null || raw === undefined || typeof raw !== "object") return null;
  const entry = raw as Record<string, unknown>;
  return {
    status: asString(entry.status),
    version: asString(entry.version),
    path: asString(entry.path),
    message: asString(entry.message),
    rawError: asString(entry.raw_error),
    source: asString(entry.source),
  };
}

/** Extract just the two tool entries the Settings page displays. */
export function parseToolchainSummary(rawPayload: string): ToolchainSummary {
  const parsed = asRecord(JSON.parse(rawPayload));
  return {
    runtimeMode: asString(parsed.runtime_mode),
    python: readToolEntry(parsed.resolved_python),
    vina: readToolEntry(parsed.active_vina),
  };
}

export function toolStatusTone(status: string): "ok" | "warning" | "error" {
  const normalized = status.trim().toLowerCase();
  if (normalized === "ok" || normalized === "ready" || normalized === "finished") return "ok";
  if (normalized === "missing" || normalized === "not_started" || normalized === "" || normalized === "unknown") {
    return "warning";
  }
  return "error";
}

export function toolStatusLabel(status: string): string {
  const normalized = status.trim().toLowerCase();
  if (normalized === "ok" || normalized === "ready") return "可用";
  if (normalized === "missing" || normalized === "not_found") return "未找到";
  if (normalized === "invalid") return "不可用";
  if (normalized === "error" || normalized === "failed") return "检测失败";
  if (!normalized) return "未检测";
  return normalized;
}

/** Human wording for where the active tool came from. */
export function describeToolSource(source: string, configuredPath: string): string {
  const normalized = source.trim().toLowerCase();
  if (configuredPath.trim()) return "使用设置中指定的路径";
  if (normalized === "bundled") return "使用随应用附带的内置版本";
  if (normalized === "configured") return "使用设置中指定的路径";
  if (normalized === "path" || normalized === "current_environment") return "从系统 PATH 自动检测";
  return "自动检测";
}

export function formatBytes(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return "0 B";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}
