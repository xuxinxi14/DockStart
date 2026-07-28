import assert from "node:assert/strict";
import test from "node:test";
import type { VinaCliFeatureCapability, VinaSettings } from "../src/types.ts";
import {
  VINA_ADVANCED_DEFAULTS,
  VINA_NUMERIC_ADVANCED_KEYS,
  customizedAdvancedVinaCount,
  getApplicableAdvancedVinaKeys,
  getVinaExpertToggleState,
  getVinaExpertValueState,
  isVinaExpertOptionApplicable,
  parseVinaForm,
  resetAdvancedVinaFields,
  vinaFormsEqual,
  vinaSettingsToForm,
  type VinaForm,
} from "../src/utils/vinaForm.ts";

const defaults: VinaSettings = {
  scoring: "vina",
  exhaustiveness: 8,
  max_evals: 0,
  num_modes: 9,
  min_rmsd: 1,
  energy_range: 4,
  spacing: 0.375,
  verbosity: 1,
  no_refine: false,
  force_even_voxels: false,
  unbound_energy: null,
  cpu: 0,
  seed: null,
};

function feature(
  status: VinaCliFeatureCapability["status"],
  supported: boolean | null,
): VinaCliFeatureCapability {
  return {
    option: "--no_refine",
    status,
    supported,
    advertised: supported,
    minimum_version: "1.2.0",
    version_compatible: supported,
    message: "",
  };
}

test("高级 Vina 默认值往返不产生脏状态", () => {
  const form = vinaSettingsToForm(defaults);
  assert.deepEqual(parseVinaForm(form), defaults);
  assert.equal(customizedAdvancedVinaCount(form), 0);
});

test("非默认高级参数被解析并计数", () => {
  const form = {
    ...vinaSettingsToForm(defaults),
    max_evals: "25000",
    spacing: "0.5",
    verbosity: "2",
  };
  const parsed = parseVinaForm(form);
  assert.ok(parsed);
  assert.equal(parsed.max_evals, 25000);
  assert.equal(parsed.spacing, 0.5);
  assert.equal(parsed.verbosity, 2);
  assert.equal(customizedAdvancedVinaCount(form), 3);
  assert.deepEqual(VINA_ADVANCED_DEFAULTS, {
    max_evals: "0",
    min_rmsd: "1",
    spacing: "0.375",
    verbosity: "1",
    unbound_energy: "",
    no_refine: false,
    force_even_voxels: false,
  });
});

test("不安全的高级参数在保存前被拒绝", () => {
  const base = vinaSettingsToForm(defaults);
  for (const patch of [
    { max_evals: "-1" },
    { max_evals: "2147483648" },
    { min_rmsd: "-0.1" },
    { spacing: "0.09" },
    { spacing: "2.01" },
    { verbosity: "0" },
    { verbosity: "3" },
  ]) {
    assert.equal(parseVinaForm({ ...base, ...patch }), null);
  }
});

test("旧项目缺少专家布尔字段时按 false 读取", () => {
  const legacy = { ...defaults } as Partial<VinaSettings>;
  delete legacy.no_refine;
  delete legacy.force_even_voxels;
  delete legacy.unbound_energy;
  const form = vinaSettingsToForm(legacy as VinaSettings);
  assert.equal(form.no_refine, false);
  assert.equal(form.force_even_voxels, false);
  assert.equal(form.unbound_energy, "");
  assert.deepEqual(parseVinaForm(form), defaults);
});

test("专家布尔字段严格往返、计数并可恢复默认", () => {
  const enabled = {
    ...vinaSettingsToForm(defaults),
    no_refine: true,
    force_even_voxels: true,
  };
  const parsed = parseVinaForm(enabled);
  assert.ok(parsed);
  assert.equal(parsed.no_refine, true);
  assert.equal(parsed.force_even_voxels, true);
  assert.equal(customizedAdvancedVinaCount(enabled), 2);

  const reset = resetAdvancedVinaFields(enabled);
  assert.equal(reset.no_refine, false);
  assert.equal(reset.force_even_voxels, false);
  assert.equal(customizedAdvancedVinaCount(reset), 0);
});

test("专家布尔字段拒绝字符串和整数伪值", () => {
  const base = vinaSettingsToForm(defaults);
  assert.equal(
    parseVinaForm({ ...base, no_refine: "false" } as unknown as VinaForm),
    null,
  );
  assert.equal(
    parseVinaForm({ ...base, force_even_voxels: 1 } as unknown as VinaForm),
    null,
  );
});

test("表单比较区分布尔值，同时保留数值字符串等价", () => {
  const base = vinaSettingsToForm(defaults);
  assert.equal(vinaFormsEqual(base, { ...base, max_evals: "0.0", spacing: "0.3750" }), true);
  assert.equal(vinaFormsEqual(base, { ...base, no_refine: true }), false);
  assert.equal(
    customizedAdvancedVinaCount(
      { ...base, no_refine: true },
      VINA_NUMERIC_ADVANCED_KEYS,
    ),
    0,
  );
});

test("专家选项只适用于 Vina 和 Vinardo 的三种运行模式", () => {
  for (const mode of ["dock", "score_only", "local_only"] as const) {
    assert.equal(isVinaExpertOptionApplicable("vina", mode, "no_refine"), true);
    assert.equal(isVinaExpertOptionApplicable("vina", mode, "force_even_voxels"), true);
    assert.equal(isVinaExpertOptionApplicable("ad4_maps", mode, "no_refine"), false);
    assert.equal(isVinaExpertOptionApplicable("ad4_maps", mode, "force_even_voxels"), false);
  }
});

test("显式未结合体系能量区分空值和零，并拒绝非有限数值", () => {
  const base = vinaSettingsToForm(defaults);
  const explicitZero = { ...base, unbound_energy: "0" };
  const parsed = parseVinaForm(explicitZero, { engine: "vina", runMode: "score_only" });
  assert.ok(parsed);
  assert.equal(parsed.unbound_energy, 0);
  assert.equal(vinaFormsEqual(base, explicitZero), false);
  assert.equal(
    customizedAdvancedVinaCount(
      explicitZero,
      getApplicableAdvancedVinaKeys("vina", "score_only"),
    ),
    1,
  );

  for (const value of ["NaN", "Infinity", "-Infinity"]) {
    assert.equal(
      parseVinaForm({ ...base, unbound_energy: value }, { engine: "vina", runMode: "score_only" }),
      null,
    );
  }
});

test("未结合体系能量只在 Vina 仅评分模式适用", () => {
  assert.equal(getApplicableAdvancedVinaKeys("vina", "score_only").includes("unbound_energy"), true);
  assert.equal(getApplicableAdvancedVinaKeys("vina", "score_only", "rigid").includes("unbound_energy"), true);
  assert.equal(getApplicableAdvancedVinaKeys("vina", "score_only", "flexible").includes("unbound_energy"), false);
  assert.equal(getApplicableAdvancedVinaKeys("vina", "dock").includes("unbound_energy"), false);
  assert.equal(getApplicableAdvancedVinaKeys("vina", "local_only").includes("unbound_energy"), false);
  assert.equal(getApplicableAdvancedVinaKeys("ad4_maps", "dock").includes("unbound_energy"), false);

  const form = {
    ...vinaSettingsToForm(defaults),
    unbound_energy: "12.5",
  };
  assert.equal(
    customizedAdvancedVinaCount(form, getApplicableAdvancedVinaKeys("vina", "dock")),
    0,
  );
  assert.equal(
    customizedAdvancedVinaCount(form, getApplicableAdvancedVinaKeys("vina", "local_only")),
    0,
  );
  assert.equal(
    customizedAdvancedVinaCount(form, getApplicableAdvancedVinaKeys("ad4_maps", "dock")),
    0,
  );
  assert.equal(
    customizedAdvancedVinaCount(form, getApplicableAdvancedVinaKeys("vina", "score_only")),
    1,
  );
  assert.equal(
    customizedAdvancedVinaCount(form, getApplicableAdvancedVinaKeys("vina", "score_only", "flexible")),
    0,
  );
});

test("不适用模式不会被隐藏的未结合体系能量草稿阻断", () => {
  const invalid = {
    ...vinaSettingsToForm(defaults),
    unbound_energy: "not-a-number",
  };
  const dock = parseVinaForm(invalid, { engine: "vina", runMode: "dock" });
  const local = parseVinaForm(invalid, { engine: "vina", runMode: "local_only" });
  const ad4 = parseVinaForm(invalid, { engine: "ad4_maps", runMode: "dock" });
  const flexibleScoreOnly = parseVinaForm(invalid, {
    engine: "vina",
    runMode: "score_only",
    receptorMode: "flexible",
  });
  assert.ok(dock);
  assert.ok(local);
  assert.ok(ad4);
  assert.ok(flexibleScoreOnly);
  assert.equal(dock.unbound_energy, null);
  assert.equal(local.unbound_energy, null);
  assert.equal(ad4.unbound_energy, null);
  assert.equal(flexibleScoreOnly.unbound_energy, null);
});

test("未知或不支持的未结合体系能量空值禁用，旧值仍可清除", () => {
  const unsupportedEmpty = getVinaExpertValueState(feature("unsupported", false), false);
  assert.equal(unsupportedEmpty.disabled, true);
  assert.equal(unsupportedEmpty.blocking, false);

  const unsupportedSet = getVinaExpertValueState(feature("unsupported", false), true);
  assert.equal(unsupportedSet.disabled, false);
  assert.equal(unsupportedSet.blocking, true);

  const unknownSet = getVinaExpertValueState(feature("unknown", null), true);
  assert.equal(unknownSet.disabled, false);
  assert.equal(unknownSet.blocking, true);
});

test("不支持或未知的已启用选项仍可关闭修复", () => {
  const supported = getVinaExpertToggleState(feature("supported", true), false);
  assert.equal(supported.disabled, false);
  assert.equal(supported.blocking, false);

  const unsupportedOff = getVinaExpertToggleState(feature("unsupported", false), false);
  assert.equal(unsupportedOff.disabled, true);
  assert.equal(unsupportedOff.blocking, false);

  const unsupportedOn = getVinaExpertToggleState(feature("unsupported", false), true);
  assert.equal(unsupportedOn.disabled, false);
  assert.equal(unsupportedOn.blocking, true);

  const unknownOn = getVinaExpertToggleState(feature("unknown", null), true);
  assert.equal(unknownOn.disabled, false);
  assert.equal(unknownOn.blocking, true);
});
