import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  ACCENT_DATASET_KEY,
  ACCENT_IDS,
  ACCENT_STORAGE_KEY,
  APPEARANCE_CHANGE_EVENT,
  APPEARANCE_DATASET_KEY,
  APPEARANCE_IDS,
  APPEARANCE_STORAGE_KEY,
  DEFAULT_ACCENT,
  DEFAULT_APPEARANCE,
  DEFAULT_THEME_MODE,
  SYSTEM_DARK_MEDIA_QUERY,
  THEME_DATASET_KEY,
  THEME_MODES,
  THEME_MODE_DATASET_KEY,
  THEME_STORAGE_KEY,
  applyAppearanceState,
  normalizeAccentId,
  normalizeAppearanceId,
  normalizeThemeMode,
  prefersDarkColorScheme,
  readAppearanceState,
  readThemePreference,
  resolveThemeMode,
  setAccentPreference,
  setAppearancePreference,
  setThemePreference,
} from "../src/utils/themePreference.ts";

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

type DomOptions = {
  prefersDark?: boolean;
  /** Simulates a hardened browser profile where localStorage throws. */
  storageBlocked?: boolean;
  matchMediaMissing?: boolean;
  matchMediaThrows?: boolean;
};

type Dom = {
  storage: Map<string, string>;
  dataset: Record<string, string>;
  colorScheme: string;
  events: Array<{ type: string; detail: unknown }>;
};

function installDom(options: DomOptions = {}): Dom {
  const dom: Dom = { storage: new Map(), dataset: {}, colorScheme: "", events: [] };
  const storage = {
    getItem: (key: string) => {
      if (options.storageBlocked) throw new Error("localStorage is blocked");
      return dom.storage.has(key) ? dom.storage.get(key)! : null;
    },
    setItem: (key: string, value: string) => {
      if (options.storageBlocked) throw new Error("localStorage is blocked");
      dom.storage.set(key, String(value));
    },
  };
  const windowStub: Record<string, unknown> = {
    localStorage: storage,
    dispatchEvent: (event: { type?: string; detail?: unknown }) => {
      dom.events.push({ type: event?.type ?? "", detail: event?.detail });
      return true;
    },
  };
  if (!options.matchMediaMissing) {
    windowStub.matchMedia = (query: string) => {
      if (options.matchMediaThrows) throw new Error("matchMedia unavailable");
      return {
        media: query,
        matches: options.prefersDark ?? true,
        addEventListener: () => {},
        removeEventListener: () => {},
      };
    };
  }
  const style: Record<string, string> = {};
  (globalThis as Record<string, unknown>).window = windowStub;
  (globalThis as Record<string, unknown>).document = {
    documentElement: { dataset: dom.dataset, style },
  };
  Object.defineProperty(dom, "colorScheme", {
    get: () => style.colorScheme ?? "",
    enumerable: true,
  });
  return dom;
}

function removeDom(): void {
  delete (globalThis as Record<string, unknown>).window;
  delete (globalThis as Record<string, unknown>).document;
}

test("存储键与属性名保持既有约定，新增轴不与旧数据冲突", () => {
  assert.equal(THEME_STORAGE_KEY, "dockstart-theme");
  assert.equal(THEME_DATASET_KEY, "theme");
  assert.equal(THEME_MODE_DATASET_KEY, "themeMode");
  assert.equal(APPEARANCE_DATASET_KEY, "appearance");
  assert.notEqual(APPEARANCE_STORAGE_KEY, THEME_STORAGE_KEY);
  assert.notEqual(ACCENT_STORAGE_KEY, THEME_STORAGE_KEY);
  assert.notEqual(ACCENT_STORAGE_KEY, APPEARANCE_STORAGE_KEY);
});

test("默认值保持既有行为：不设置时是深色 + 默认外观 + 默认强调色", () => {
  installDom();
  assert.equal(DEFAULT_THEME_MODE, "dark");
  assert.equal(DEFAULT_APPEARANCE, "default");
  assert.equal(DEFAULT_ACCENT, "default");
  assert.deepEqual(readAppearanceState(), { mode: "dark", appearance: "default", accent: "default" });
  assert.equal(readThemePreference(), "dark");
  removeDom();
});

test("没有 window / document 时读取不抛异常并返回默认值", () => {
  removeDom();
  assert.deepEqual(readAppearanceState(), { mode: "dark", appearance: "default", accent: "default" });
  assert.equal(readThemePreference(), "dark");
  assert.equal(prefersDarkColorScheme(), true);
  assert.equal(applyAppearanceState({ mode: "light", appearance: "soft", accent: "cyan" }), "light");
});

test("主题模式归一化保留旧语义并新增 system", () => {
  assert.equal(normalizeThemeMode("light"), "light");
  assert.equal(normalizeThemeMode("LIGHT"), "light");
  assert.equal(normalizeThemeMode("dark"), "dark");
  assert.equal(normalizeThemeMode("system"), "system");
  assert.equal(normalizeThemeMode("  System  "), "system");
  assert.equal(normalizeThemeMode(null), "dark");
  assert.equal(normalizeThemeMode(undefined), "dark");
  assert.equal(normalizeThemeMode("solarized"), "dark");
  assert.equal(normalizeThemeMode(42), "dark");
  assert.deepEqual([...THEME_MODES].sort(), ["dark", "light", "system"]);
});

test("外观与强调色只接受白名单取值", () => {
  assert.equal(normalizeAppearanceId("soft"), "soft");
  assert.equal(normalizeAppearanceId("CONTRAST"), "contrast");
  assert.equal(normalizeAppearanceId("default"), "default");
  assert.equal(normalizeAppearanceId("neon"), "default");
  assert.equal(normalizeAppearanceId(null), "default");
  assert.equal(normalizeAccentId("cyan"), "cyan");
  assert.equal(normalizeAccentId("Graphite"), "graphite");
  assert.equal(normalizeAccentId("rainbow"), "default");
  assert.equal(normalizeAccentId(7), "default");
  assert.deepEqual([...APPEARANCE_IDS].sort(), ["contrast", "default", "soft"]);
  assert.deepEqual([...ACCENT_IDS].sort(), ["cyan", "default", "graphite"]);
});

test("system 模式按系统偏好解析，其余两种模式是绝对的", () => {
  assert.equal(resolveThemeMode("light", true), "light");
  assert.equal(resolveThemeMode("light", false), "light");
  assert.equal(resolveThemeMode("dark", false), "dark");
  assert.equal(resolveThemeMode("dark", true), "dark");
  assert.equal(resolveThemeMode("system", true), "dark");
  assert.equal(resolveThemeMode("system", false), "light");
});

test("系统偏好不可用或抛异常时按深色处理，与旧默认一致", () => {
  installDom({ matchMediaMissing: true });
  assert.equal(prefersDarkColorScheme(), true);
  removeDom();

  installDom({ matchMediaThrows: true });
  assert.equal(prefersDarkColorScheme(), true);
  assert.equal(resolveThemeMode("system"), "dark");
  removeDom();

  installDom({ prefersDark: false });
  assert.equal(prefersDarkColorScheme(), false);
  assert.equal(resolveThemeMode("system"), "light");
  removeDom();
});

test("媒体查询字符串与 index.html 首屏脚本保持一致", () => {
  const html = readFileSync(join(desktopRoot, "index.html"), "utf8");
  assert.equal(SYSTEM_DARK_MEDIA_QUERY, "(prefers-color-scheme: dark)");
  assert.ok(html.includes(`"${SYSTEM_DARK_MEDIA_QUERY}"`), "index.html 未使用同一个媒体查询");
  for (const key of [THEME_STORAGE_KEY, APPEARANCE_STORAGE_KEY, ACCENT_STORAGE_KEY]) {
    assert.ok(html.includes(`"${key}"`), `index.html 首屏脚本未读取 ${key}`);
  }
  for (const attribute of [THEME_DATASET_KEY, THEME_MODE_DATASET_KEY, APPEARANCE_DATASET_KEY, ACCENT_DATASET_KEY]) {
    assert.ok(html.includes(`dataset.${attribute}`), `index.html 首屏脚本未写入 data-${attribute}`);
  }
});

test("应用偏好会写入全部属性，data-theme 始终是解析后的值", () => {
  const dom = installDom({ prefersDark: false });
  const resolved = applyAppearanceState({ mode: "system", appearance: "contrast", accent: "cyan" });
  assert.equal(resolved, "light");
  assert.equal(dom.dataset.theme, "light", "data-theme 必须是解析后的 light/dark，既有样式表依赖它");
  assert.equal(dom.dataset.themeMode, "system", "原始偏好写在 data-theme-mode");
  assert.equal(dom.dataset.appearance, "contrast");
  assert.equal(dom.dataset.accent, "cyan");
  assert.equal(dom.colorScheme, "light");
  removeDom();
});

test("三个轴的修改可以往返持久化", () => {
  installDom();
  setThemePreference("light");
  setAppearancePreference("soft");
  setAccentPreference("graphite");

  const state = readAppearanceState();
  assert.deepEqual(state, { mode: "light", appearance: "soft", accent: "graphite" });
  assert.equal(readThemePreference(), "light");
  removeDom();
});

test("修改一个轴不会重置另外两个轴（同一次交互只改一个偏好）", () => {
  installDom();
  setThemePreference("light");
  setAppearancePreference("contrast");
  setAccentPreference("cyan");

  setAccentPreference("default");
  assert.deepEqual(readAppearanceState(), { mode: "light", appearance: "contrast", accent: "default" });

  setThemePreference("system");
  assert.deepEqual(readAppearanceState(), { mode: "system", appearance: "contrast", accent: "default" });

  setAppearancePreference("default");
  assert.deepEqual(readAppearanceState(), { mode: "system", appearance: "default", accent: "default" });
  removeDom();
});

test("非法或损坏的存储值回落到默认值，不抛异常", () => {
  const dom = installDom();
  dom.storage.set(THEME_STORAGE_KEY, "solarized");
  dom.storage.set(APPEARANCE_STORAGE_KEY, "neon");
  dom.storage.set(ACCENT_STORAGE_KEY, "{}");
  assert.deepEqual(readAppearanceState(), { mode: "dark", appearance: "default", accent: "default" });
  removeDom();
});

test("localStorage 被禁用时切换仍然生效，只是不落盘", () => {
  const dom = installDom({ storageBlocked: true });
  assert.doesNotThrow(() => setAppearancePreference("soft"));
  assert.equal(dom.dataset.appearance, "soft", "属性必须仍然写入，否则界面不会变化");
  assert.equal(dom.storage.size, 0);
  assert.deepEqual(readAppearanceState(), { mode: "dark", appearance: "default", accent: "default" });
  removeDom();
});

test("每个可选外观/强调色在 tokens.css 中都有深色与浅色两层实现", () => {
  const css = readFileSync(join(desktopRoot, "src", "styles", "tokens.css"), "utf8");
  const themes = ["dark", "light"];

  for (const id of APPEARANCE_IDS) {
    if (id === DEFAULT_APPEARANCE) continue;
    for (const theme of themes) {
      assert.ok(
        css.includes(`[data-theme="${theme}"][data-appearance="${id}"]`),
        `tokens.css 缺少 ${theme} + appearance=${id} 的配色层`,
      );
    }
  }
  for (const id of ACCENT_IDS) {
    if (id === DEFAULT_ACCENT) continue;
    for (const theme of themes) {
      assert.ok(
        css.includes(`[data-theme="${theme}"][data-accent="${id}"]`),
        `tokens.css 缺少 ${theme} + accent=${id} 的配色层`,
      );
    }
  }

  // 反向：样式表里不应存在界面无法选中的组合（否则就是死代码或写错的 id）。
  for (const match of css.matchAll(/\[data-appearance="([a-z-]+)"\]/g)) {
    assert.ok(
      (APPEARANCE_IDS as readonly string[]).includes(match[1]),
      `tokens.css 定义了界面上选不到的 appearance：${match[1]}`,
    );
  }
  for (const match of css.matchAll(/\[data-accent="([a-z-]+)"\]/g)) {
    assert.ok(
      (ACCENT_IDS as readonly string[]).includes(match[1]),
      `tokens.css 定义了界面上选不到的 accent：${match[1]}`,
    );
  }
});

test("每次修改都会广播一次完整状态，供外壳与其他入口同步", () => {
  const dom = installDom();
  setAppearancePreference("soft");
  setAccentPreference("cyan");
  setThemePreference("light");

  assert.equal(dom.events.length, 3);
  for (const event of dom.events) assert.equal(event.type, APPEARANCE_CHANGE_EVENT);
  assert.deepEqual(dom.events[0].detail, { mode: "dark", appearance: "soft", accent: "default", resolved: "dark" });
  assert.deepEqual(dom.events[2].detail, { mode: "light", appearance: "soft", accent: "cyan", resolved: "light" });
  removeDom();
});
