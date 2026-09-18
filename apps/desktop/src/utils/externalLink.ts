/**
 * Opening external web pages.
 *
 * DockStart hands URLs to the operating system through the official Tauri
 * opener plugin, so a click opens the real default browser — never an in-app
 * browser and never a clipboard imitation.
 *
 * Every failure is converted into a value: the Settings page renders the
 * message inline and keeps working, instead of an exception escaping into the
 * React tree.
 */

import { openUrl } from "@tauri-apps/plugin-opener";

export type ExternalLinkResult =
  | { ok: true; url: string }
  | { ok: false; message: string; suggestion: string; rawError: string };

/** Only real web pages are ever handed to the shell. */
export function isOpenableExternalUrl(value: unknown): boolean {
  if (typeof value !== "string" || value.trim() === "") return false;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

export async function openExternalUrl(url: string): Promise<ExternalLinkResult> {
  if (!isOpenableExternalUrl(url)) {
    return {
      ok: false,
      message: "这个地址不是可以打开的网页链接。",
      suggestion: "DockStart 只允许打开 http 或 https 链接。",
      rawError: `rejected url: ${String(url)}`,
    };
  }

  try {
    await openUrl(url);
    return { ok: true, url };
  } catch (error) {
    return {
      ok: false,
      message: "无法调用系统默认浏览器打开链接。",
      suggestion: "可以手动复制下面的地址，粘贴到浏览器中打开。",
      rawError: error instanceof Error ? error.message : String(error),
    };
  }
}
