export type ScreeningResultStatusFilter = "all" | "succeeded" | "failed" | "pending";
export type ScreeningResultSort = "score" | "order" | "name" | "status";

/**
 * The result helpers deliberately depend on only the fields rendered by the
 * workspace.  Backend records may contain additional provenance fields and
 * remain structurally compatible with this type.
 */
export type ScreeningResultItem = {
  item_id: string;
  order?: number;
  ligand_file?: string;
  source_file?: string;
  status: string;
  attempt_count?: number;
  best_affinity_kcal_mol?: number | null;
  last_error?: string;
};

export type ScreeningResultFilterOptions = {
  query?: string;
  status?: ScreeningResultStatusFilter;
  stagedLabels?: Readonly<Record<string, string>>;
};

export type ScreeningResultPage<T extends ScreeningResultItem> = {
  items: T[];
  page: number;
  pageSize: number;
  pageCount: number;
  total: number;
  start: number;
  end: number;
};

const pendingStatuses = new Set(["pending", "running", "interrupted", "cancel_requested"]);
const statusSortOrder: Readonly<Record<string, number>> = {
  succeeded: 0,
  failed: 1,
  running: 2,
  interrupted: 3,
  cancel_requested: 4,
  pending: 5,
};

function basename(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : "";
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function compareItemOrder(left: ScreeningResultItem, right: ScreeningResultItem): number {
  const leftOrder = finiteNumber(left.order);
  const rightOrder = finiteNumber(right.order);
  if (leftOrder === null && rightOrder !== null) return 1;
  if (leftOrder !== null && rightOrder === null) return -1;
  if (leftOrder === null || rightOrder === null) return 0;
  return leftOrder - rightOrder;
}

function stableItemId(left: ScreeningResultItem, right: ScreeningResultItem): number {
  return left.item_id.localeCompare(right.item_id, "en", {
    numeric: true,
    sensitivity: "case",
  });
}

function normalizeSearchText(value: string): string {
  return value.trim().toLocaleLowerCase("zh-CN").replace(/\s+/g, " ");
}

export function screeningItemDisplayName(
  item: ScreeningResultItem,
  stagedLabels: Readonly<Record<string, string>> = {},
): string {
  const sourceFile = item.source_file?.trim() ?? "";
  const stagedLabel = sourceFile ? stagedLabels[sourceFile]?.trim() : "";
  if (stagedLabel) return stagedLabel;

  const sourceName = basename(sourceFile);
  if (sourceName) return sourceName;

  const ligandName = basename(item.ligand_file?.trim() ?? "");
  if (ligandName) return ligandName;

  return item.item_id.trim() || "未命名配体";
}

export function isScreeningItemInStatusFilter(
  item: ScreeningResultItem,
  status: ScreeningResultStatusFilter,
): boolean {
  if (status === "all") return true;
  if (status === "pending") return pendingStatuses.has(item.status);
  return item.status === status;
}

export function filterScreeningItems<T extends ScreeningResultItem>(
  items: readonly T[],
  options: ScreeningResultFilterOptions = {},
): T[] {
  const status = options.status ?? "all";
  const terms = normalizeSearchText(options.query ?? "").split(" ").filter(Boolean);

  return items.filter((item) => {
    if (!isScreeningItemInStatusFilter(item, status)) return false;
    if (!terms.length) return true;

    const searchable = normalizeSearchText([
      screeningItemDisplayName(item, options.stagedLabels),
      item.item_id,
      item.source_file ?? "",
      item.ligand_file ?? "",
      item.last_error ?? "",
    ].join(" "));
    return terms.every((term) => searchable.includes(term));
  });
}

export function sortScreeningItems<T extends ScreeningResultItem>(
  items: readonly T[],
  sort: ScreeningResultSort,
  stagedLabels: Readonly<Record<string, string>> = {},
): T[] {
  return items
    .map((item, index) => ({ item, index }))
    .sort((leftEntry, rightEntry) => {
      const left = leftEntry.item;
      const right = rightEntry.item;
      let comparison = 0;

      if (sort === "score") {
        const leftScore = finiteNumber(left.best_affinity_kcal_mol);
        const rightScore = finiteNumber(right.best_affinity_kcal_mol);
        if (leftScore === null && rightScore !== null) comparison = 1;
        else if (leftScore !== null && rightScore === null) comparison = -1;
        else if (leftScore !== null && rightScore !== null) comparison = leftScore - rightScore;
      } else if (sort === "order") {
        comparison = compareItemOrder(left, right);
      } else if (sort === "name") {
        comparison = screeningItemDisplayName(left, stagedLabels).localeCompare(
          screeningItemDisplayName(right, stagedLabels),
          "zh-CN",
          { numeric: true, sensitivity: "base" },
        );
      } else {
        comparison = (statusSortOrder[left.status] ?? Number.MAX_SAFE_INTEGER)
          - (statusSortOrder[right.status] ?? Number.MAX_SAFE_INTEGER);
        if (comparison === 0 && left.status !== right.status) {
          comparison = left.status.localeCompare(right.status, "en");
        }
      }

      if (comparison !== 0) return comparison;
      const orderComparison = compareItemOrder(left, right);
      if (orderComparison !== 0) return orderComparison;
      const idComparison = stableItemId(left, right);
      return idComparison !== 0 ? idComparison : leftEntry.index - rightEntry.index;
    })
    .map(({ item }) => item);
}

export function selectTopScreeningItems<T extends ScreeningResultItem>(
  items: readonly T[],
  topN: number,
): T[] {
  const limit = Number.isFinite(topN) ? Math.max(0, Math.floor(topN)) : 0;
  if (limit === 0) return [];
  return sortScreeningItems(
    items.filter((item) => (
      item.status === "succeeded"
      && finiteNumber(item.best_affinity_kcal_mol) !== null
    )),
    "score",
  ).slice(0, limit);
}

export function buildScreeningRankMap(
  items: readonly ScreeningResultItem[],
): Map<string, number> {
  return new Map(
    selectTopScreeningItems(items, items.length)
      .map((item, index) => [item.item_id, index + 1]),
  );
}

export function clampScreeningPage(
  page: number,
  totalItems: number,
  pageSize: number,
): number {
  const safeTotal = Number.isFinite(totalItems) ? Math.max(0, Math.floor(totalItems)) : 0;
  const safePageSize = Number.isFinite(pageSize) ? Math.max(1, Math.floor(pageSize)) : 1;
  const pageCount = Math.max(1, Math.ceil(safeTotal / safePageSize));
  const requestedPage = Number.isFinite(page) ? Math.floor(page) : 1;
  return Math.min(pageCount, Math.max(1, requestedPage));
}

export function paginateScreeningItems<T extends ScreeningResultItem>(
  items: readonly T[],
  page: number,
  pageSize: number,
): ScreeningResultPage<T> {
  const safePageSize = Number.isFinite(pageSize) ? Math.max(1, Math.floor(pageSize)) : 1;
  const safePage = clampScreeningPage(page, items.length, safePageSize);
  const offset = (safePage - 1) * safePageSize;
  const pageItems = items.slice(offset, offset + safePageSize);

  return {
    items: pageItems,
    page: safePage,
    pageSize: safePageSize,
    pageCount: Math.max(1, Math.ceil(items.length / safePageSize)),
    total: items.length,
    start: pageItems.length ? offset + 1 : 0,
    end: pageItems.length ? offset + pageItems.length : 0,
  };
}
