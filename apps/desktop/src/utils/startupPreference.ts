export type StartupPage = "help" | "home";
const key = "dockstart.startup-page";
export function readStartupPage(): StartupPage {
  try { return window.localStorage.getItem(key) === "home" ? "home" : "help"; }
  catch { return "help"; }
}
export function saveStartupPage(page: StartupPage): boolean {
  try { window.localStorage.setItem(key, page); return true; } catch { return false; }
}
