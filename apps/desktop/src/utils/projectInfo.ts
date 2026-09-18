/**
 * Project identity used by Settings → About.
 *
 * The link must always point at the *official* DockStart project, never at a
 * contributor's fork. Git remotes on a working copy are not authoritative for
 * shipped software, so the canonical value is pinned here and cross-checked by
 * `tests/projectInfo.test.ts` against `package.json`, `Cargo.toml` and the
 * `upstream` git remote. Change it in one place only if the project moves.
 */

export const DOCKSTART_REPOSITORY_URL = "https://github.com/xuxinxi14/DockStart";

export const DOCKSTART_LICENSE = "Apache-2.0";

export const DOCKSTART_PROJECT_SUMMARY =
  "DockStart 是一个完全在本机运行的分子对接工作台，负责结构准备、AutoDock Vina 对接、结果分析与实验记录。";

/** `github.com/xuxinxi14/DockStart` — shown under the button for verification. */
export function repositoryDisplayUrl(url: string = DOCKSTART_REPOSITORY_URL): string {
  try {
    const parsed = new URL(url);
    return `${parsed.host}${parsed.pathname}`.replace(/\/+$/, "");
  } catch {
    return url;
  }
}
