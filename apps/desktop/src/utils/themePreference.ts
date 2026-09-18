/**
 * Appearance preferences, shared by the app shell (Topbar toggle) and the
 * Settings page.
 *
 * This is the single source of truth for every visual preference DockStart
 * exposes. There is deliberately no second theme mechanism:
 *
 *   theme mode  system | light | dark       stored in `dockstart-theme`
 *   appearance  default | soft | contrast   stored in `dockstart-appearance`
 *   accent      default | cyan | graphite   stored in `dockstart-accent`
 *
 * The palette itself lives in `styles/tokens.css` and is selected purely by
 * the `data-*` attributes written below, so no component needs to know which
 * combination is active — it keeps reading the same `--ds-*` tokens.
 *
 * `data-theme` keeps its historical meaning: it always receives the *resolved*
 * `light`/`dark` value, because the whole stylesheet keys off it. The raw
 * preference (including `system`) is mirrored to `data-theme-mode` for
 * debugging and for the settings UI.
 */

export type ThemeMode = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";
export type AppearanceId = "default" | "soft" | "contrast";
export type AccentId = "default" | "cyan" | "graphite";

export type AppearanceState = {
  mode: ThemeMode;
  appearance: AppearanceId;
  accent: AccentId;
};

/** `dockstart-theme` keeps storing the mode; existing installs stay valid. */
export const THEME_STORAGE_KEY = "dockstart-theme";
export const APPEARANCE_STORAGE_KEY = "dockstart-appearance";
export const ACCENT_STORAGE_KEY = "dockstart-accent";

/** Fired on `window` whenever any visual preference changes. */
export const APPEARANCE_CHANGE_EVENT = "dockstart-appearance-change";

export const THEME_DATASET_KEY = "theme";
export const THEME_MODE_DATASET_KEY = "themeMode";
export const APPEARANCE_DATASET_KEY = "appearance";
export const ACCENT_DATASET_KEY = "accent";
/** Must match the media query used in `index.html`. */
export const SYSTEM_DARK_MEDIA_QUERY = "(prefers-color-scheme: dark)";

/**
 * DockStart has always started dark, and adding personalisation must not
 * change that for anyone who never opens Settings.
 */
export const DEFAULT_THEME_MODE: ThemeMode = "dark";
export const DEFAULT_APPEARANCE: AppearanceId = "default";
export const DEFAULT_ACCENT: AccentId = "default";

export const DEFAULT_APPEARANCE_STATE: AppearanceState = {
  mode: DEFAULT_THEME_MODE,
  appearance: DEFAULT_APPEARANCE,
  accent: DEFAULT_ACCENT,
};

export const THEME_MODES: readonly ThemeMode[] = ["system", "light", "dark"];
export const APPEARANCE_IDS: readonly AppearanceId[] = ["default", "soft", "contrast"];
export const ACCENT_IDS: readonly AccentId[] = ["default", "cyan", "graphite"];

export function normalizeThemeMode(value: unknown): ThemeMode {
  if (typeof value !== "string") return DEFAULT_THEME_MODE;
  const normalized = value.trim().toLowerCase();
  if (normalized === "light") return "light";
  if (normalized === "system") return "system";
  return "dark";
}

export function normalizeAppearanceId(value: unknown): AppearanceId {
  if (typeof value !== "string") return DEFAULT_APPEARANCE;
  const normalized = value.trim().toLowerCase();
  if (normalized === "soft" || normalized === "contrast") return normalized;
  return DEFAULT_APPEARANCE;
}

export function normalizeAccentId(value: unknown): AccentId {
  if (typeof value !== "string") return DEFAULT_ACCENT;
  const normalized = value.trim().toLowerCase();
  if (normalized === "cyan" || normalized === "graphite") return normalized;
  return DEFAULT_ACCENT;
}

/**
 * Read the operating system preference. Falls back to `true` (dark) so a
 * missing or blocked `matchMedia` behaves exactly like the old default.
 */
export function prefersDarkColorScheme(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return true;
  try {
    return window.matchMedia(SYSTEM_DARK_MEDIA_QUERY).matches;
  } catch {
    return true;
  }
}

/** `system` resolves against the OS; the other two modes are absolute. */
export function resolveThemeMode(
  mode: ThemeMode,
  prefersDark: boolean = prefersDarkColorScheme(),
): ResolvedTheme {
  if (mode === "light") return "light";
  if (mode === "dark") return "dark";
  return prefersDark ? "dark" : "light";
}

function readStored(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStored(key: string, value: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // A blocked localStorage must never break switching itself.
  }
}

/** Backwards-compatible accessor: the raw mode, defaulting to `dark`. */
export function readThemePreference(): ThemeMode {
  return normalizeThemeMode(readStored(THEME_STORAGE_KEY));
}

export function readAppearanceState(): AppearanceState {
  return {
    mode: normalizeThemeMode(readStored(THEME_STORAGE_KEY)),
    appearance: normalizeAppearanceId(readStored(APPEARANCE_STORAGE_KEY)),
    accent: normalizeAccentId(readStored(ACCENT_STORAGE_KEY)),
  };
}

/**
 * Write every visual attribute the stylesheet keys off, and return the
 * resolved theme so callers can render mode-dependent UI (icon, label).
 */
export function applyAppearanceState(
  state: AppearanceState,
  prefersDark: boolean = prefersDarkColorScheme(),
): ResolvedTheme {
  const resolved = resolveThemeMode(state.mode, prefersDark);
  if (typeof document === "undefined") return resolved;
  const root = document.documentElement;
  root.dataset[THEME_DATASET_KEY] = resolved;
  root.dataset[THEME_MODE_DATASET_KEY] = state.mode;
  root.dataset[APPEARANCE_DATASET_KEY] = state.appearance;
  root.dataset[ACCENT_DATASET_KEY] = state.accent;
  root.style.colorScheme = resolved;
  return resolved;
}

function dispatchChange(state: AppearanceState, resolved: ResolvedTheme): void {
  if (typeof window === "undefined") return;
  try {
    window.dispatchEvent(
      new CustomEvent<AppearanceState & { resolved: ResolvedTheme }>(APPEARANCE_CHANGE_EVENT, {
        detail: { ...state, resolved },
      }),
    );
  } catch {
    // Older environments without CustomEvent simply skip the broadcast.
  }
}

function commit(state: AppearanceState, prefersDark?: boolean): ResolvedTheme {
  const resolved = applyAppearanceState(state, prefersDark);
  dispatchChange(state, resolved);
  return resolved;
}

export function setThemePreference(mode: ThemeMode): ResolvedTheme {
  const next: AppearanceState = { ...readAppearanceState(), mode: normalizeThemeMode(mode) };
  writeStored(THEME_STORAGE_KEY, next.mode);
  return commit(next);
}

export function setAppearancePreference(appearance: AppearanceId): ResolvedTheme {
  const next: AppearanceState = { ...readAppearanceState(), appearance: normalizeAppearanceId(appearance) };
  writeStored(APPEARANCE_STORAGE_KEY, next.appearance);
  return commit(next);
}

export function setAccentPreference(accent: AccentId): ResolvedTheme {
  const next: AppearanceState = { ...readAppearanceState(), accent: normalizeAccentId(accent) };
  writeStored(ACCENT_STORAGE_KEY, next.accent);
  return commit(next);
}
