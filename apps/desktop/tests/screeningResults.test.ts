import assert from "node:assert/strict";
import test from "node:test";
import {
  buildScreeningRankMap,
  clampScreeningPage,
  filterScreeningItems,
  paginateScreeningItems,
  screeningItemDisplayName,
  selectTopScreeningItems,
  sortScreeningItems,
  type ScreeningResultItem,
} from "../src/utils/screeningResults.ts";

const items: ScreeningResultItem[] = [
  {
    item_id: "ligand_0003",
    order: 3,
    ligand_file: "screening/inputs/ligands/ligand_0003.pdbqt",
    source_file: "prepared/Zinc-3.pdbqt",
    status: "failed",
    attempt_count: 2,
    best_affinity_kcal_mol: null,
    last_error: "Vina 返回非零退出码",
  },
  {
    item_id: "ligand_0001",
    order: 1,
    ligand_file: "screening/inputs/ligands/ligand_0001.pdbqt",
    source_file: "prepared/Zinc-10.pdbqt",
    status: "succeeded",
    attempt_count: 1,
    best_affinity_kcal_mol: -7.5,
  },
  {
    item_id: "ligand_0002",
    order: 2,
    ligand_file: "screening/inputs/ligands/ligand_0002.pdbqt",
    source_file: "prepared/Zinc-2.pdbqt",
    status: "succeeded",
    attempt_count: 1,
    best_affinity_kcal_mol: -8.25,
  },
  {
    item_id: "ligand_0004",
    order: 4,
    ligand_file: "screening/inputs/ligands/ligand_0004.pdbqt",
    source_file: "prepared/queued.pdbqt",
    status: "pending",
    attempt_count: 0,
  },
  {
    item_id: "ligand_0005",
    order: 5,
    ligand_file: "screening/inputs/ligands/ligand_0005.pdbqt",
    source_file: "prepared/current.pdbqt",
    status: "running",
    attempt_count: 1,
  },
];

test("显示名优先使用导入标签，并兼容 Windows/Unix 路径与缺失来源", () => {
  assert.equal(
    screeningItemDisplayName(items[1], { "prepared/Zinc-10.pdbqt": "  原始配体 10.sdf " }),
    "原始配体 10.sdf",
  );
  assert.equal(screeningItemDisplayName(items[2]), "Zinc-2.pdbqt");
  assert.equal(screeningItemDisplayName({
    item_id: "ligand_0099",
    ligand_file: String.raw`screening\inputs\ligands\ligand_0099.pdbqt`,
    status: "pending",
  }), "ligand_0099.pdbqt");
  assert.equal(screeningItemDisplayName({ item_id: "ligand_0100", status: "pending" }), "ligand_0100");
});

test("状态筛选把等待、运行、中断和取消请求统一归入待处理", () => {
  const extended = [
    ...items,
    { item_id: "ligand_0006", status: "interrupted" },
    { item_id: "ligand_0007", status: "cancel_requested" },
  ];
  assert.deepEqual(
    filterScreeningItems(extended, { status: "pending" }).map((item) => item.item_id),
    ["ligand_0004", "ligand_0005", "ligand_0006", "ligand_0007"],
  );
  assert.deepEqual(
    filterScreeningItems(items, { status: "succeeded" }).map((item) => item.item_id),
    ["ligand_0001", "ligand_0002"],
  );
  assert.deepEqual(
    filterScreeningItems(items, { status: "failed" }).map((item) => item.item_id),
    ["ligand_0003"],
  );
});

test("文本搜索覆盖显示名、编号、路径和错误，并要求所有关键词命中", () => {
  assert.deepEqual(
    filterScreeningItems(items, {
      query: "原始 10",
      stagedLabels: { "prepared/Zinc-10.pdbqt": "原始配体 10.sdf" },
    }).map((item) => item.item_id),
    ["ligand_0001"],
  );
  assert.deepEqual(
    filterScreeningItems(items, { query: "0003 非零" }).map((item) => item.item_id),
    ["ligand_0003"],
  );
  assert.deepEqual(
    filterScreeningItems(items, { query: "zinc succeeded", status: "succeeded" }),
    [],
  );
});

test("评分排序按更低评分优先，缺失和非有限评分置后且不修改原数组", () => {
  const withInvalid = [
    ...items,
    {
      item_id: "ligand_0006",
      order: 6,
      status: "succeeded",
      best_affinity_kcal_mol: Number.NaN,
    },
  ];
  const original = withInvalid.map((item) => item.item_id);
  assert.deepEqual(
    sortScreeningItems(withInvalid, "score").map((item) => item.item_id),
    ["ligand_0002", "ligand_0001", "ligand_0003", "ligand_0004", "ligand_0005", "ligand_0006"],
  );
  assert.deepEqual(withInvalid.map((item) => item.item_id), original);
});

test("顺序、名称和状态排序均有稳定的次级排序", () => {
  const shuffled = [items[4], items[2], items[0], items[3], items[1]];
  assert.deepEqual(
    sortScreeningItems(shuffled, "order").map((item) => item.item_id),
    ["ligand_0001", "ligand_0002", "ligand_0003", "ligand_0004", "ligand_0005"],
  );
  assert.deepEqual(
    sortScreeningItems(items, "name").map((item) => item.item_id),
    ["ligand_0005", "ligand_0004", "ligand_0002", "ligand_0003", "ligand_0001"],
  );
  assert.deepEqual(
    sortScreeningItems(shuffled, "status").map((item) => item.item_id),
    ["ligand_0001", "ligand_0002", "ligand_0003", "ligand_0005", "ligand_0004"],
  );
});

test("Top N 仅接收成功且评分有限的条目，并据此生成完整排名映射", () => {
  const tied = [
    ...items,
    {
      item_id: "ligand_0006",
      order: 6,
      status: "succeeded",
      best_affinity_kcal_mol: -8.25,
    },
  ];
  assert.deepEqual(
    selectTopScreeningItems(tied, 2).map((item) => item.item_id),
    ["ligand_0002", "ligand_0006"],
  );
  assert.deepEqual(selectTopScreeningItems(tied, 0), []);

  const ranks = buildScreeningRankMap(tied);
  assert.equal(ranks.get("ligand_0002"), 1);
  assert.equal(ranks.get("ligand_0006"), 2);
  assert.equal(ranks.get("ligand_0001"), 3);
  assert.equal(ranks.has("ligand_0003"), false);
});

test("分页纠正越界页码并返回面向用户的一位起止序号", () => {
  assert.equal(clampScreeningPage(99, 21, 10), 3);
  assert.equal(clampScreeningPage(-4, 21, 10), 1);
  assert.equal(clampScreeningPage(Number.NaN, 21, 10), 1);

  const many = Array.from({ length: 21 }, (_, index): ScreeningResultItem => ({
    item_id: `ligand_${String(index + 1).padStart(4, "0")}`,
    order: index + 1,
    status: "succeeded",
  }));
  const lastPage = paginateScreeningItems(many, 99, 10);
  assert.equal(lastPage.page, 3);
  assert.equal(lastPage.pageCount, 3);
  assert.equal(lastPage.start, 21);
  assert.equal(lastPage.end, 21);
  assert.equal(lastPage.items[0].item_id, "ligand_0021");

  const emptyPage = paginateScreeningItems([], 4, 0);
  assert.deepEqual(emptyPage, {
    items: [],
    page: 1,
    pageSize: 1,
    pageCount: 1,
    total: 0,
    start: 0,
    end: 0,
  });
});
