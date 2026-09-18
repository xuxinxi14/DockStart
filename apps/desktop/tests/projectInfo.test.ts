import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  DOCKSTART_LICENSE,
  DOCKSTART_REPOSITORY_URL,
  repositoryDisplayUrl,
} from "../src/utils/projectInfo.ts";
import { isOpenableExternalUrl } from "../src/utils/externalLink.ts";

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(desktopRoot, "..", "..");

function read(relativePath: string): string {
  return readFileSync(join(repoRoot, relativePath), "utf8");
}

function parseJson(relativePath: string): Record<string, unknown> {
  return JSON.parse(read(relativePath)) as Record<string, unknown>;
}

function normalizeRepositoryUrl(value: string): string {
  return value.trim().replace(/\.git$/, "").replace(/\/+$/, "");
}

function collectSourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return collectSourceFiles(path);
    return entry.isFile() && /\.(ts|tsx|rs|json|toml|html)$/.test(entry.name) ? [path] : [];
  });
}

const packageJson = parseJson("apps/desktop/package.json");
const cargoToml = read("apps/desktop/src-tauri/Cargo.toml");
const tauriConfig = parseJson("apps/desktop/src-tauri/tauri.conf.json");
const capabilities = parseJson("apps/desktop/src-tauri/capabilities/default.json");
const mainRust = read("apps/desktop/src-tauri/src/main.rs");

test("仓库地址指向官方上游项目，而不是任何个人 fork", () => {
  const url = new URL(DOCKSTART_REPOSITORY_URL);
  assert.equal(url.protocol, "https:");
  assert.equal(url.host, "github.com");
  assert.equal(url.pathname, "/xuxinxi14/DockStart");
  assert.ok(!DOCKSTART_REPOSITORY_URL.includes("naihe386"), "软件内不能指向开发者自己的 fork");
});

test("仓库地址与 package.json / Cargo.toml 中的项目定义一致（单一事实来源）", () => {
  const repository = packageJson.repository as { type?: string; url?: string } | undefined;
  assert.ok(repository?.url, "package.json 缺少 repository.url");
  assert.equal(normalizeRepositoryUrl(repository.url), DOCKSTART_REPOSITORY_URL);

  const cargoMatch = cargoToml.match(/^\s*repository\s*=\s*"([^"]+)"/m);
  assert.ok(cargoMatch, "Cargo.toml 缺少 repository 字段");
  assert.equal(normalizeRepositoryUrl(cargoMatch[1]), DOCKSTART_REPOSITORY_URL);
});

test("许可证与版本信息取自既有可靠来源", () => {
  assert.equal(DOCKSTART_LICENSE, packageJson.license);
  assert.equal(DOCKSTART_LICENSE, cargoToml.match(/^\s*license\s*=\s*"([^"]+)"/m)?.[1]);
  assert.equal(tauriConfig.version, packageJson.version);
});

test("显示用的仓库地址去掉协议后缀，便于用户核对", () => {
  assert.equal(repositoryDisplayUrl(), "github.com/xuxinxi14/DockStart");
  assert.equal(repositoryDisplayUrl("https://github.com/foo/bar/"), "github.com/foo/bar");
  assert.equal(repositoryDisplayUrl("not a url"), "not a url");
});

test("只有 http/https 链接会交给系统浏览器", () => {
  assert.equal(isOpenableExternalUrl(DOCKSTART_REPOSITORY_URL), true);
  assert.equal(isOpenableExternalUrl("http://example.com"), true);
  assert.equal(isOpenableExternalUrl(""), false);
  assert.equal(isOpenableExternalUrl(null), false);
  assert.equal(isOpenableExternalUrl(7), false);
  assert.equal(isOpenableExternalUrl("not a url"), false);
  assert.equal(isOpenableExternalUrl("file:///C:/Windows/System32/calc.exe"), false);
  assert.equal(isOpenableExternalUrl("javascript:alert(1)"), false);
  assert.equal(isOpenableExternalUrl("mailto:someone@example.com"), false);
});

test("外部链接能力在前后端都已接线（缺一环点击就会静默失败）", () => {
  const dependencies = packageJson.dependencies as Record<string, string>;
  assert.ok(dependencies["@tauri-apps/plugin-opener"], "前端缺少 opener 插件依赖");

  const externalLinkSource = read("apps/desktop/src/utils/externalLink.ts");
  assert.ok(
    externalLinkSource.includes('from "@tauri-apps/plugin-opener"'),
    "externalLink.ts 必须复用官方 opener 插件，而不是自造打开方式",
  );

  assert.ok(tauriTomlHasOpener(), "Cargo.toml 缺少 tauri-plugin-opener 依赖");
  assert.ok(mainRust.includes("tauri_plugin_opener::init()"), "main.rs 未注册 opener 插件");

  const permissions = (capabilities.permissions ?? []) as string[];
  assert.ok(permissions.includes("opener:allow-open-url"), "capabilities 未授予 open_url 权限");
  assert.ok(
    permissions.some((permission) => permission.startsWith("opener:") && permission.includes("default-urls")),
    "capabilities 未授予 http/https 的默认 URL 作用域",
  );
});

function tauriTomlHasOpener(): boolean {
  return /^\s*tauri-plugin-opener\s*=/m.test(cargoToml);
}

test("源码中不硬编码任何人的 fork 地址", () => {
  const roots = ["apps/desktop/src", "apps/desktop/src-tauri", "backend/dockstart_core"];
  const offenders: string[] = [];
  for (const root of roots) {
    for (const path of collectSourceFiles(join(repoRoot, root))) {
      const contents = readFileSync(path, "utf8");
      if (contents.includes("github.com/naihe386")) offenders.push(path);
    }
  }
  assert.deepEqual(offenders, [], `以下文件硬编码了个人 fork 地址：${offenders.join(", ")}`);
});
