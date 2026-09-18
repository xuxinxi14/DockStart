/**
 * Theme preference, shared by the app shell (Topbar toggle) and the Settings page.
 *
 * The storage key and the ``data-theme`` attribute are the existing DockStart
 * convention; this module only centralises them so both entry points stay in
 * sync instead of drifting into two independent implementations.
 */

export type ThemeMode = "dark" | "light";

export const THEME_STORAGE_KEY = "dockstart-theme";

/** Fired on `window` whenever the preference changes from any entry point. */
export const THEME_CHANGE_EVENT = "dockstart-theme-change";

export function normalizeThemeMode(value: unknown): ThemeMode {
  return typeof value === "string" && value.trim().toLowerCase() === "light" ? "light" : "dark";
}

export function readThemePreference(): ThemeMode {
  if (typeof window === "undefined") return "dark";
  try {
    return normalizeThemeMode(window.localStorage.getItem(THEME_STORAGE_KEY));
  } catch {
    return "dark";
  }
}

export function applyThemePreference(mode: ThemeMode): void {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.theme = mode;
  document.documentElement.style.colorScheme = mode;
}

export function setThemePreference(mode: ThemeMode): void {
  applyThemePreference(mode);
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, mode);
    } catch {
      // A blocked localStorage must not break the theme switch itself.
    }
    window.dispatchEvent(new CustomEvent<ThemeMode>(THEME_CHANGE_EVENT, { detail: mode }));
  }
}
