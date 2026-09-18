import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  DEFAULT_DOCKING_DEFAULTS,
  DOCKING_DEFAULT_LIMITS,
  DOCKING_DEFAULT_SCORING_FUNCTIONS,
  EMPTY_DOCKING_DEFAULTS_FORM,
  describeToolSource,
  dockingDefaultsEqual,
  dockingDefaultsToForm,
  formatBytes,
  normalizeDockingDefaults,
  normalizeSettings,
  parseDiagnosticsResponse,
  parseDockingDefaultsForm,
  parseSettingsResponse,
  parseToolchainSummary,
  toolStatusLabel,
  toolStatusTone,
} from "../src/utils/settingsForm.ts";
import { normalizeThemeMode } from "../src/utils/themePreference.ts";

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(desktopRoot, "..", "..");

function readBackendFile(relativePath: string): string {
  return readFileSync(join(repoRoot, relativePath), "utf8");
}

function dataclassBlock(source: string, name: string): string {
  const marker = `class ${name}:`;
  const start = source.indexOf(marker);
  assert.ok(start >= 0, `未找到 dataclass ${name}`);
  const rest = source.slice(start + marker.length);
  const end = rest.search(/\n(?:@|def |class )/);
  return end >= 0 ? rest.slice(0, end) : rest;
}

function fieldDefault(block: string, key: string): string {
  const match = block.match(new RegExp(`\\n\\s+${key}:\\s*[^=\\n]+=\\s*([^\\n]+)`));
  assert.ok(match, `dataclass 中缺少字段 ${key}`);
  return match[1].trim();
}

const projectSource = readBackendFile("backend/dockstart_core/project.py");
const settingsSource = readBackendFile("backend/dockstart_core/settings.py");

test("前端默认对接参数与后端 VinaSettings 字面量保持一致", () => {
  const block = dataclassBlock(projectSource, "VinaSettings");
  assert.equal(fieldDefault(block, "scoring"), '"vina"');
  assert.equal(Number(fieldDefault(block, "exhaustiveness")), DEFAULT_DOCKING_DEFAULTS.exhaustiveness);
  assert.equal(Number(fieldDefault(block, "num_modes")), DEFAULT_DOCKING_DEFAULTS.num_modes);
  assert.equal(Number(fieldDefault(block, "energy_range")), DEFAULT_DOCKING_DEFAULTS.energy_range);
  assert.equal(Number(fieldDefault(block, "cpu")), DEFAULT_DOCKING_DEFAULTS.cpu);
  assert.equal(fieldDefault(block, "seed"), "None");
  assert.equal(DEFAULT_DOCKING_DEFAULTS.seed, null);
});

test("后端 DockingDefaults 与前端默认值一致（允许两处真源，但不允许漂移）", () => {
  const block = dataclassBlock(settingsSource, "DockingDefaults");
  assert.equal(fieldDefault(block, "scoring"), '"vina"');
  assert.equal(Number(fieldDefault(block, "exhaustiveness")), DEFAULT_DOCKING_DEFAULTS.exhaustiveness);
  assert.equal(Number(fieldDefault(block, "num_modes")), DEFAULT_DOCKING_DEFAULTS.num_modes);
  assert.equal(Number(fieldDefault(block, "energy_range")), DEFAULT_DOCKING_DEFAULTS.energy_range);
  assert.equal(Number(fieldDefault(block, "cpu")), DEFAULT_DOCKING_DEFAULTS.cpu);
});

test("前端数值边界与后端 settings.py 的边界常量一致", () => {
  for (const [key, { min, max }] of Object.entries(DOCKING_DEFAULT_LIMITS)) {
    if (key === "energy_range" || key === "seed") continue;
    const match = settingsSource.match(new RegExp(`"${key}":\\s*\\((\\d+),\\s*(\\d+)\\)`));
    assert.ok(match, `settings.py 中缺少 ${key} 的边界`);
    assert.equal(Number(match[1]), min, `${key} 下界不一致`);
    assert.equal(Number(match[2]), max, `${key} 上界不一致`);
  }

  const energyMatch = settingsSource.match(/ENERGY_RANGE_BOUNDS = \(([\d.]+), ([\d.]+)\)/);
  assert.ok(energyMatch, "settings.py 中缺少 ENERGY_RANGE_BOUNDS");
  assert.equal(Number(energyMatch[1]), DOCKING_DEFAULT_LIMITS.energy_range.min);
  assert.equal(Number(energyMatch[2]), DOCKING_DEFAULT_LIMITS.energy_range.max);

  const seedMatch = settingsSource.match(/SEED_BOUNDS = \((\d[\d_]*), (\d[\d_]*)\)/);
  assert.ok(seedMatch, "settings.py 中缺少 SEED_BOUNDS");
  assert.equal(Number(seedMatch[1].replace(/_/g, "")), DOCKING_DEFAULT_LIMITS.seed.min);
  assert.equal(Number(seedMatch[2].replace(/_/g, "")), DOCKING_DEFAULT_LIMITS.seed.max);
});

test("后端评分函数白名单与前端一致", () => {
  const match = settingsSource.match(/DOCKING_DEFAULT_SCORING_FUNCTIONS = \(([^)]*)\)/);
  assert.ok(match, "settings.py 中缺少 DOCKING_DEFAULT_SCORING_FUNCTIONS");
  const parsed = match[1]
    .split(",")
    .map((entry) => entry.trim().replace(/^"|"$/g, ""))
    .filter(Boolean);
  assert.deepEqual(parsed, [...DOCKING_DEFAULT_SCORING_FUNCTIONS]);
});

test("旧版本设置文件缺少 docking_defaults 时回落到推荐默认值", () => {
  const settings = normalizeSettings({
    tool_paths: { vina: "C:\\tools\\vina.exe", python: "", autogrid4: "" },
    project: { default_project_dir: "D:\\projects" },
  });

  assert.deepEqual(settings.docking_defaults, DEFAULT_DOCKING_DEFAULTS);
  assert.equal(settings.tool_paths.vina, "C:\\tools\\vina.exe");
  assert.equal(settings.project.default_project_dir, "D:\\projects");
});

test("手写坏值时不会抛错，而是回落到默认并钳制越界值", () => {
  const settings = normalizeSettings({
    docking_defaults: {
      scoring: "VINARDO",
      exhaustiveness: "9999",
      num_modes: "-4",
      energy_range: "not-a-number",
      cpu: 3.7,
      seed: "",
    },
  });

  assert.equal(settings.docking_defaults.scoring, "vinardo");
  assert.equal(settings.docking_defaults.exhaustiveness, DOCKING_DEFAULT_LIMITS.exhaustiveness.max);
  assert.equal(settings.docking_defaults.num_modes, DOCKING_DEFAULT_LIMITS.num_modes.min);
  assert.equal(settings.docking_defaults.energy_range, DEFAULT_DOCKING_DEFAULTS.energy_range);
  // 3.7 是非法整数：回落默认值，与后端一致，绝不静默取整。
  assert.equal(settings.docking_defaults.cpu, DEFAULT_DOCKING_DEFAULTS.cpu);
  assert.equal(settings.docking_defaults.seed, null);

  assert.equal(normalizeDockingDefaults({ scoring: "unknown" }).scoring, "vina");
  assert.equal(normalizeDockingDefaults({ exhaustiveness: "16.0" }).exhaustiveness, 16);
  assert.equal(normalizeDockingDefaults({ seed: "12.5" }).seed, null);
});

test("表单校验拒绝越界与非法输入，并指出具体字段", () => {
  const tooLarge = parseDockingDefaultsForm({ ...EMPTY_DOCKING_DEFAULTS_FORM, exhaustiveness: "500" });
  assert.equal(tooLarge.ok, false);
  assert.equal(tooLarge.ok === false ? tooLarge.field : "", "exhaustiveness");

  const notANumber = parseDockingDefaultsForm({ ...EMPTY_DOCKING_DEFAULTS_FORM, energy_range: "四" });
  assert.equal(notANumber.ok, false);
  assert.equal(notANumber.ok === false ? notANumber.field : "", "energy_range");

  const notInteger = parseDockingDefaultsForm({ ...EMPTY_DOCKING_DEFAULTS_FORM, num_modes: "9.5" });
  assert.equal(notInteger.ok, false);

  const emptyRequired = parseDockingDefaultsForm({ ...EMPTY_DOCKING_DEFAULTS_FORM, cpu: "" });
  assert.equal(emptyRequired.ok, false);
  assert.equal(emptyRequired.ok === false ? emptyRequired.field : "", "cpu");

  const badScoring = parseDockingDefaultsForm({ ...EMPTY_DOCKING_DEFAULTS_FORM, scoring: "mmgbsa" });
  assert.equal(badScoring.ok, false);
  assert.equal(badScoring.ok === false ? badScoring.field : "", "scoring");
});

test("表单校验通过时允许留空 seed，并保留其它数值", () => {
  const parsed = parseDockingDefaultsForm({
    scoring: "vina",
    exhaustiveness: "16",
    num_modes: "20",
    energy_range: "3.5",
    cpu: "0",
    seed: "",
  });

  assert.equal(parsed.ok, true);
  assert.deepEqual(parsed.ok === true ? parsed.defaults : null, {
    scoring: "vina",
    exhaustiveness: 16,
    num_modes: 20,
    energy_range: 3.5,
    cpu: 0,
    seed: null,
  });

  const withSeed = parseDockingDefaultsForm({ ...EMPTY_DOCKING_DEFAULTS_FORM, seed: "12345" });
  assert.equal(withSeed.ok, true);
  assert.equal(withSeed.ok === true ? withSeed.defaults.seed : null, 12345);
});

test("表单与默认值可以往返转换，并能判断是否有未保存修改", () => {
  const form = dockingDefaultsToForm(DEFAULT_DOCKING_DEFAULTS);
  assert.deepEqual(form, EMPTY_DOCKING_DEFAULTS_FORM);

  const roundTrip = parseDockingDefaultsForm(form);
  assert.equal(roundTrip.ok, true);
  assert.ok(roundTrip.ok === true && dockingDefaultsEqual(roundTrip.defaults, DEFAULT_DOCKING_DEFAULTS));

  const changed = parseDockingDefaultsForm({ ...form, num_modes: "11" });
  assert.equal(changed.ok, true);
  assert.ok(changed.ok === true && !dockingDefaultsEqual(changed.defaults, DEFAULT_DOCKING_DEFAULTS));
});

test("settings 响应解析容错，并能读出后端返回的 docking_defaults", () => {
  const response = parseSettingsResponse(
    JSON.stringify({
      ok: true,
      settings_path: "C:\\app\\dockstart_settings.json",
      settings: {
        tool_paths: { vina: "", python: "C:\\Python313\\python.exe", autogrid4: "" },
        project: { default_project_dir: "" },
        docking_defaults: { scoring: "vina", exhaustiveness: 16, num_modes: 9, energy_range: 4, cpu: 0, seed: null },
      },
    }),
  );

  assert.equal(response.ok, true);
  assert.equal(response.settings?.docking_defaults.exhaustiveness, 16);

  const failure = parseSettingsResponse(
    JSON.stringify({ ok: false, settings_path: "x", settings: null, error: { message: "坏了", raw_error: "boom" } }),
  );
  assert.equal(failure.ok, false);
  assert.equal(failure.error?.message, "坏了");
  assert.equal(failure.error?.raw_error, "boom");
});

test("诊断响应解析出真实的读写状态与设置文件信息", () => {
  const response = parseDiagnosticsResponse(
    JSON.stringify({
      ok: true,
      settings_path: "C:\\app\\dockstart_settings.json",
      diagnostics: {
        settings_path: "C:\\app\\dockstart_settings.json",
        settings_dir: "C:\\app",
        dir_exists: true,
        file_exists: true,
        file_size_bytes: 512,
        file_modified_at: "2026-09-18T15:00:00+08:00",
        lock_file: "C:\\app\\.dockstart_settings.json.lock",
        env_override: "",
        readable: true,
        writable: false,
        load: { ok: true, message: "ok", raw_error: "", suggestion: "" },
        write_probe: { ok: false, message: "不可写", raw_error: "PermissionError", suggestion: "换目录" },
        current_settings: null,
      },
    }),
  );

  assert.equal(response.diagnostics?.readable, true);
  assert.equal(response.diagnostics?.writable, false);
  assert.equal(response.diagnostics?.write_probe.raw_error, "PermissionError");
  assert.equal(response.diagnostics?.file_size_bytes, 512);

  const missing = parseDiagnosticsResponse(JSON.stringify({ ok: false, settings_path: "", error: { message: "失败" } }));
  assert.equal(missing.diagnostics, null);
  assert.equal(missing.error?.message, "失败");
});

test("工具链状态只提取 Python 与 Vina，并给出可用的来源说明", () => {
  const summary = parseToolchainSummary(
    JSON.stringify({
      runtime_mode: "dev",
      resolved_python: {
        status: "ok",
        version: "3.13.12",
        path: "C:\\Python313\\python.exe",
        message: "",
        raw_error: "",
        source: "configured",
      },
      active_vina: {
        status: "ok",
        version: "1.2.7",
        path: "D:\\app\\resources\\vina\\vina.exe",
        source: "bundled",
      },
    }),
  );

  assert.equal(summary.runtimeMode, "dev");
  assert.equal(summary.python?.version, "3.13.12");
  assert.equal(summary.vina?.version, "1.2.7");
  assert.equal(describeToolSource(summary.vina?.source ?? "", ""), "使用随应用附带的内置版本");
  assert.equal(describeToolSource(summary.python?.source ?? "", ""), "使用设置中指定的路径");
  assert.equal(describeToolSource("configured", "C:\\custom\\vina.exe"), "使用设置中指定的路径");
  assert.equal(describeToolSource("path", ""), "从系统 PATH 自动检测");
});

test("工具状态映射为可读标签与徽标色调", () => {
  assert.equal(toolStatusTone("ok"), "ok");
  assert.equal(toolStatusTone("ready"), "ok");
  assert.equal(toolStatusTone("missing"), "warning");
  assert.equal(toolStatusTone(""), "warning");
  assert.equal(toolStatusTone("error"), "error");
  assert.equal(toolStatusTone("invalid"), "error");

  assert.equal(toolStatusLabel("ok"), "可用");
  assert.equal(toolStatusLabel("missing"), "未找到");
  assert.equal(toolStatusLabel(""), "未检测");
});

test("主题归一化只接受 light，其余一律回落深色", () => {
  assert.equal(normalizeThemeMode("light"), "light");
  assert.equal(normalizeThemeMode("LIGHT"), "light");
  assert.equal(normalizeThemeMode("dark"), "dark");
  assert.equal(normalizeThemeMode(null), "dark");
  assert.equal(normalizeThemeMode("solarized"), "dark");
});

test("文件大小按可读单位展示", () => {
  assert.equal(formatBytes(0), "0 B");
  assert.equal(formatBytes(512), "512 B");
  assert.equal(formatBytes(2048), "2.0 KB");
});
