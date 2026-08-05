import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const sourceRoot = join(desktopRoot, "src");
const mainRustPath = join(desktopRoot, "src-tauri", "src", "main.rs");

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return entry.isFile() && /\.tsx?$/.test(entry.name) ? [path] : [];
  });
}

function staticInvokeCommands(): string[] {
  const pattern = /\binvoke(?:<[^>]*>)?\s*\(\s*["']([^"']+)["']/g;
  const commands = new Set<string>();
  for (const path of sourceFiles(sourceRoot)) {
    for (const match of readFileSync(path, "utf8").matchAll(pattern)) {
      commands.add(match[1]);
    }
  }
  return [...commands].sort();
}

function registeredHandlers(): string[] {
  const source = readFileSync(mainRustPath, "utf8");
  const block = source.match(/tauri::generate_handler!\s*\[([\s\S]*?)\]\)/);
  assert.ok(block, "main.rs 中缺少 tauri::generate_handler! 注册块");
  return block[1]
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\/\/.*$/gm, "")
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean);
}

test("every static frontend invoke is registered by the Tauri handler", () => {
  const handlers = new Set(registeredHandlers());
  const commands = staticInvokeCommands();
  assert.ok(commands.length > 0, "未提取到任何静态 invoke 命令");
  const missing = commands.filter((command) => !handlers.has(command));
  assert.deepEqual(
    missing,
    [],
    `以下静态 invoke 命令未在 main.rs 注册：${missing.join(", ")}`,
  );
});

test("Tauri generate_handler does not register duplicate command names", () => {
  const handlers = registeredHandlers();
  assert.ok(handlers.length > 0, "Tauri handler 注册表为空");
  const counts = new Map<string, number>();
  for (const handler of handlers) {
    assert.match(handler, /^[A-Za-z_][A-Za-z0-9_]*$/);
    counts.set(handler, (counts.get(handler) || 0) + 1);
  }
  const duplicates = [...counts]
    .filter(([, count]) => count > 1)
    .map(([handler]) => handler)
    .sort();
  assert.deepEqual(duplicates, [], `Tauri handler 重复注册：${duplicates.join(", ")}`);
});

// Dynamic wrappers intentionally stay outside the static extractor. Their
// command-to-method mappings remain covered by hydratedApi.test.ts and
// macrocyclePreparation.test.ts.
