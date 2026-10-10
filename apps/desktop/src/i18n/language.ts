export type Language = "zh-CN" | "en-US";

export const LANGUAGE_STORAGE_KEY = "dockstart-language";
export const DEFAULT_LANGUAGE: Language = "zh-CN";

export function normalizeLanguage(value: unknown): Language {
  if (typeof value !== "string") return DEFAULT_LANGUAGE;
  return /^(en|en-us)$/i.test(value.trim()) ? "en-US" : DEFAULT_LANGUAGE;
}

export function readLanguagePreference(): Language {
  try {
    return typeof window === "undefined"
      ? DEFAULT_LANGUAGE
      : normalizeLanguage(window.localStorage.getItem(LANGUAGE_STORAGE_KEY));
  } catch {
    return DEFAULT_LANGUAGE;
  }
}

let language: Language = readLanguagePreference();
const listeners = new Set<() => void>();

export function getLanguage(): Language {
  return language;
}

export function getLocale(): Language {
  return language;
}

function applyLanguage(next: Language): void {
  language = next;
  if (typeof document !== "undefined") document.documentElement.lang = next;
  for (const listener of listeners) listener();
}

export function initializeLanguage(): void {
  applyLanguage(readLanguagePreference());
}

export function setLanguage(value: Language): void {
  const next = normalizeLanguage(value);
  try {
    if (typeof window !== "undefined") window.localStorage.setItem(LANGUAGE_STORAGE_KEY, next);
  } catch {
    // Switching still works for this session when storage is unavailable.
  }
  applyLanguage(next);
}

function onStorage(event: StorageEvent): void {
  if (event.key !== LANGUAGE_STORAGE_KEY && event.key !== null) return;
  applyLanguage(event.key === null ? readLanguagePreference() : normalizeLanguage(event.newValue));
}

export function subscribeLanguage(listener: () => void): () => void {
  if (listeners.size === 0 && typeof window !== "undefined") window.addEventListener("storage", onStorage);
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && typeof window !== "undefined") window.removeEventListener("storage", onStorage);
  };
}
