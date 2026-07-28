import assert from "node:assert/strict";
import test from "node:test";
import type { VinaSettings } from "../src/types.ts";
import {
  SCREENING_ADVANCED_VINA_KEYS,
  buildScreeningAdvancedVinaSummary,
  buildScreeningVinaSettings,
  hasCompleteScreeningAdvancedVinaSettings,
  serializeScreeningVinaSettings,
} from "../src/utils/screeningVina.ts";

const vina: VinaSettings = {
  scoring: "vinardo",
  exhaustiveness: 16,
  max_evals: 25_000,
  num_modes: 12,
  min_rmsd: 1.5,
  energy_range: 5,
  spacing: 0.5,
  verbosity: 2,
  no_refine: true,
  force_even_voxels: true,
  unbound_energy: 12.5,
  cpu: 8,
  seed: 42,
};

test("批量筛选序列化继承适用参数、覆盖单任务 CPU 并排除未结合体系能量", () => {
  const snapshot = buildScreeningVinaSettings(vina, 3);
  assert.deepEqual(snapshot, {
    scoring: "vinardo",
    exhaustiveness: 16,
    max_evals: 25_000,
    num_modes: 12,
    min_rmsd: 1.5,
    energy_range: 5,
    spacing: 0.5,
    verbosity: 2,
    no_refine: true,
    force_even_voxels: true,
    cpu: 3,
    seed: 42,
  });
  assert.equal("unbound_energy" in snapshot, false);

  const serialized = JSON.parse(serializeScreeningVinaSettings(vina, 3)) as Record<string, unknown>;
  assert.deepEqual(serialized, snapshot);
  assert.equal(Object.hasOwn(serialized, "unbound_energy"), false);
});

test("冻结摘要覆盖六项批量高级参数且不宣称未结合体系能量", () => {
  const summary = buildScreeningAdvancedVinaSummary(vina);
  assert.deepEqual(summary.map((item) => item.key), SCREENING_ADVANCED_VINA_KEYS);
  assert.deepEqual(summary.map((item) => `${item.label} ${item.value}`), [
    "评估上限 25000",
    "构象最小间距 1.5 Å",
    "网格间距 0.5 Å",
    "日志 详细（2）",
    "最终精修 关闭",
    "偶数体素 强制",
  ]);
  assert.equal(summary.some((item) => item.label.includes("未结合")), false);
});

test("旧项目缺少高级字段时摘要使用 Vina 标准默认值", () => {
  const summary = buildScreeningAdvancedVinaSummary({});
  assert.deepEqual(summary.map((item) => item.value), [
    "Vina 自动",
    "1 Å",
    "0.375 Å",
    "标准（1）",
    "保留",
    "不强制",
  ]);
});

test("旧项目缺少高级字段时创建快照会补齐兼容默认值", () => {
  const legacy = { ...vina } as Partial<VinaSettings>;
  delete legacy.max_evals;
  delete legacy.min_rmsd;
  delete legacy.spacing;
  delete legacy.verbosity;
  delete legacy.no_refine;
  delete legacy.force_even_voxels;

  const snapshot = buildScreeningVinaSettings(legacy as VinaSettings, 2);
  assert.deepEqual(
    {
      max_evals: snapshot.max_evals,
      min_rmsd: snapshot.min_rmsd,
      spacing: snapshot.spacing,
      verbosity: snapshot.verbosity,
      no_refine: snapshot.no_refine,
      force_even_voxels: snapshot.force_even_voxels,
    },
    {
      max_evals: 0,
      min_rmsd: 1,
      spacing: 0.375,
      verbosity: 1,
      no_refine: false,
      force_even_voxels: false,
    },
  );
});

test("只有完整记录六项高级字段时才视为现代冻结快照", () => {
  assert.equal(hasCompleteScreeningAdvancedVinaSettings(vina), true);
  assert.equal(
    hasCompleteScreeningAdvancedVinaSettings(vina, ["spacing"]),
    false,
  );
  assert.equal(
    hasCompleteScreeningAdvancedVinaSettings({
      max_evals: 0,
      min_rmsd: 1,
      spacing: 0.375,
      verbosity: 1,
      no_refine: false,
    }),
    false,
  );
  assert.equal(hasCompleteScreeningAdvancedVinaSettings(undefined), false);
});
