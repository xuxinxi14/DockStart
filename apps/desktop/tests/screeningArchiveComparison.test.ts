import assert from "node:assert/strict";
import test from "node:test";
import {
  comparisonDeltaRange,
  comparisonItemDisplayName,
  filterScreeningArchiveComparisonRows,
  hasComparableScoreDelta,
  paginateScreeningArchiveComparisonRows,
  sortScreeningArchiveComparisonRows,
  type ScreeningArchiveComparisonRow,
} from "../src/utils/screeningArchiveComparison.ts";

const rows: ScreeningArchiveComparisonRow[] = [
  {
    identity_sha256: "aaa111",
    match_status: "matched",
    baseline_items: [{
      item_id: "ligand_0001",
      display_label: "候选 A.sdf",
      best_affinity_kcal_mol: -7.5,
      rank: 3,
    }],
    comparison_items: [{
      item_id: "ligand_0004",
      display_label: "候选 A.sdf",
      best_affinity_kcal_mol: -8.2,
      rank: 1,
    }],
    score_delta_kcal_mol: -0.7,
    rank_delta: -2,
  },
  {
    identity_sha256: "bbb222",
    match_status: "matched",
    baseline_items: [{
      item_id: "ligand_0002",
      source_file: String.raw`raw\候选 B.sdf`,
      best_affinity_kcal_mol: -8,
      rank: 1,
    }],
    comparison_items: [{
      item_id: "ligand_0005",
      source_file: "raw/候选 B.sdf",
      best_affinity_kcal_mol: -7.75,
      rank: 2,
    }],
    score_delta_kcal_mol: 0.25,
    rank_delta: 1,
  },
  {
    identity_sha256: "ccc333",
    match_status: "matched",
    baseline_items: [{
      item_id: "ligand_0003",
      ligand_file: "prepared/候选 C.pdbqt",
      best_affinity_kcal_mol: -6.1,
      rank: 4,
    }],
    comparison_items: [{
      item_id: "ligand_0006",
      ligand_file: "prepared/候选 C.pdbqt",
      best_affinity_kcal_mol: -6.1,
      rank: 4,
    }],
    score_delta_kcal_mol: 0.0001,
    rank_delta: 0,
  },
  {
    identity_sha256: "ddd444",
    match_status: "baseline_only",
    baseline_items: [{
      item_id: "ligand_0007",
      display_label: "仅基线.sdf",
      best_affinity_kcal_mol: -9,
      rank: 2,
    }],
    comparison_items: [],
  },
  {
    identity_sha256: "eee555",
    match_status: "ambiguous",
    baseline_items: [
      { item_id: "ligand_0008", display_label: "重复身份.sdf", best_affinity_kcal_mol: -7 },
      { item_id: "ligand_0009", display_label: "重复身份副本.sdf", best_affinity_kcal_mol: -7 },
    ],
    comparison_items: [{ item_id: "ligand_0010", display_label: "重复身份.sdf", best_affinity_kcal_mol: -7.2 }],
  },
];

test("显示名称按标签、来源文件、配体文件和编号逐级回退", () => {
  assert.equal(comparisonItemDisplayName(rows[0].baseline_items[0]), "候选 A.sdf");
  assert.equal(comparisonItemDisplayName(rows[1].baseline_items[0]), "候选 B.sdf");
  assert.equal(comparisonItemDisplayName(rows[2].baseline_items[0]), "候选 C.pdbqt");
  assert.equal(comparisonItemDisplayName({ item_id: "ligand_0099" }), "ligand_0099");
});

test("搜索覆盖两侧名称、编号、路径和身份哈希，并要求全部关键词命中", () => {
  assert.deepEqual(
    filterScreeningArchiveComparisonRows(rows, { query: "候选 A 0004" })
      .map((row) => row.identity_sha256),
    ["aaa111"],
  );
  assert.deepEqual(
    filterScreeningArchiveComparisonRows(rows, { query: "bbb222 raw" })
      .map((row) => row.identity_sha256),
    ["bbb222"],
  );
  assert.deepEqual(filterScreeningArchiveComparisonRows(rows, { query: "不存在" }), []);
});

test("匹配范围与评分差值范围可以组合筛选", () => {
  assert.deepEqual(
    filterScreeningArchiveComparisonRows(rows, { matchStatus: "matched", range: "lower" })
      .map((row) => row.identity_sha256),
    ["aaa111"],
  );
  assert.deepEqual(
    filterScreeningArchiveComparisonRows(rows, { range: "higher" })
      .map((row) => row.identity_sha256),
    ["bbb222"],
  );
  assert.deepEqual(
    filterScreeningArchiveComparisonRows(rows, { range: "unchanged" })
      .map((row) => row.identity_sha256),
    ["ccc333"],
  );
  assert.deepEqual(
    filterScreeningArchiveComparisonRows(rows, { range: "unavailable" })
      .map((row) => row.identity_sha256),
    ["ddd444", "eee555"],
  );
});

test("协议不可比时全部差值均视为不可用", () => {
  assert.equal(hasComparableScoreDelta(rows[0], false), false);
  assert.equal(comparisonDeltaRange(rows[0], false), "unavailable");
  assert.deepEqual(
    filterScreeningArchiveComparisonRows(rows, {
      range: "unavailable",
      deltasComparable: false,
    }).map((row) => row.identity_sha256),
    rows.map((row) => row.identity_sha256),
  );
  assert.deepEqual(
    filterScreeningArchiveComparisonRows(rows, {
      range: "lower",
      deltasComparable: false,
    }),
    [],
  );
});

test("评分和排名差值排序稳定，缺失或不可用的差值始终置后", () => {
  assert.deepEqual(
    sortScreeningArchiveComparisonRows(rows, "identity", "desc")
      .map((row) => row.identity_sha256),
    ["eee555", "ddd444", "ccc333", "bbb222", "aaa111"],
  );
  assert.deepEqual(
    sortScreeningArchiveComparisonRows(rows, "score_delta", "asc")
      .map((row) => row.identity_sha256),
    ["aaa111", "ccc333", "bbb222", "ddd444", "eee555"],
  );
  assert.deepEqual(
    sortScreeningArchiveComparisonRows(rows, "score_delta", "desc")
      .map((row) => row.identity_sha256),
    ["bbb222", "ccc333", "aaa111", "ddd444", "eee555"],
  );
  assert.deepEqual(
    sortScreeningArchiveComparisonRows(rows, "rank_delta", "asc")
      .map((row) => row.identity_sha256),
    ["aaa111", "ccc333", "bbb222", "ddd444", "eee555"],
  );
  assert.deepEqual(
    sortScreeningArchiveComparisonRows(rows, "score_delta", "asc", false)
      .slice(-2)
      .map((row) => row.identity_sha256),
    ["ddd444", "eee555"],
  );
});

test("分页纠正越界页码且使用面向用户的一位起止序号", () => {
  const page = paginateScreeningArchiveComparisonRows(rows, 99, 2);
  assert.equal(page.page, 3);
  assert.equal(page.pageCount, 3);
  assert.equal(page.start, 5);
  assert.equal(page.end, 5);
  assert.equal(page.items[0].identity_sha256, "eee555");

  assert.deepEqual(paginateScreeningArchiveComparisonRows([], -2, 0), {
    items: [],
    page: 1,
    pageSize: 1,
    pageCount: 1,
    total: 0,
    start: 0,
    end: 0,
  });
});
