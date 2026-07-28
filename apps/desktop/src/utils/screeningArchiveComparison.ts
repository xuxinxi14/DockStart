export type ScreeningArchiveComparisonMatchStatus =
  | "matched"
  | "baseline_only"
  | "comparison_only"
  | "ambiguous"
  | string;

export type ScreeningArchiveComparisonRange =
  | "all"
  | "lower"
  | "unchanged"
  | "higher"
  | "unavailable";

export type ScreeningArchiveComparisonSort =
  | "identity"
  | "baseline_score"
  | "comparison_score"
  | "score_delta"
  | "rank_delta";

export type ScreeningArchiveComparisonDirection = "asc" | "desc";

export type ScreeningArchiveComparisonItem = {
  item_id: string;
  display_label?: string;
  source_file?: string;
  ligand_file?: string;
  status?: string;
  best_affinity_kcal_mol?: number | null;
  rank?: number | null;
};

export type ScreeningArchiveComparisonRow = {
  identity_sha256: string;
  match_status: ScreeningArchiveComparisonMatchStatus;
  baseline_items: ScreeningArchiveComparisonItem[];
  comparison_items: ScreeningArchiveComparisonItem[];
  score_delta_kcal_mol?: number | null;
  rank_delta?: number | null;
};

export type ScreeningArchiveComparisonFilterOptions = {
  query?: string;
  matchStatus?: "all" | ScreeningArchiveComparisonMatchStatus;
  range?: ScreeningArchiveComparisonRange;
  deltasComparable?: boolean;
};

export type ScreeningArchiveComparisonPage<T extends ScreeningArchiveComparisonRow> = {
  items: T[];
  page: number;
  pageSize: number;
  pageCount: number;
  total: number;
  start: number;
  end: number;
};

/**
 * Vina scores are normally shown to three decimal places. Values that would
 * render as zero at that precision are grouped as unchanged for filtering.
 */
export const SCREENING_SCORE_DELTA_EPSILON = 0.0005;

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function basename(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : "";
}

function normalizeSearchText(value: string): string {
  return value.trim().toLocaleLowerCase("zh-CN").replace(/\s+/g, " ");
}

export function comparisonItemDisplayName(item: ScreeningArchiveComparisonItem): string {
  const displayLabel = item.display_label?.trim();
  if (displayLabel) return displayLabel;

  const sourceName = basename(item.source_file?.trim() ?? "");
  if (sourceName) return sourceName;

  const ligandName = basename(item.ligand_file?.trim() ?? "");
  if (ligandName) return ligandName;

  return item.item_id.trim() || "未命名配体";
}

export function rowPrimaryDisplayName(row: ScreeningArchiveComparisonRow): string {
  const primary = row.baseline_items[0] ?? row.comparison_items[0];
  return primary ? comparisonItemDisplayName(primary) : row.identity_sha256;
}

export function hasComparableScoreDelta(
  row: ScreeningArchiveComparisonRow,
  deltasComparable = true,
): boolean {
  return deltasComparable
    && row.match_status === "matched"
    && finiteNumber(row.score_delta_kcal_mol) !== null;
}

export function comparisonDeltaRange(
  row: ScreeningArchiveComparisonRow,
  deltasComparable = true,
): Exclude<ScreeningArchiveComparisonRange, "all"> {
  if (!hasComparableScoreDelta(row, deltasComparable)) return "unavailable";
  const delta = row.score_delta_kcal_mol as number;
  if (Math.abs(delta) < SCREENING_SCORE_DELTA_EPSILON) return "unchanged";
  return delta < 0 ? "lower" : "higher";
}

function searchableItemText(item: ScreeningArchiveComparisonItem): string {
  return [
    comparisonItemDisplayName(item),
    item.item_id,
    item.display_label ?? "",
    item.source_file ?? "",
    item.ligand_file ?? "",
    item.status ?? "",
  ].join(" ");
}

export function filterScreeningArchiveComparisonRows<T extends ScreeningArchiveComparisonRow>(
  rows: readonly T[],
  options: ScreeningArchiveComparisonFilterOptions = {},
): T[] {
  const matchStatus = options.matchStatus ?? "all";
  const range = options.range ?? "all";
  const deltasComparable = options.deltasComparable ?? true;
  const terms = normalizeSearchText(options.query ?? "").split(" ").filter(Boolean);

  return rows.filter((row) => {
    if (matchStatus !== "all" && row.match_status !== matchStatus) return false;
    if (range !== "all" && comparisonDeltaRange(row, deltasComparable) !== range) return false;
    if (!terms.length) return true;

    const searchable = normalizeSearchText([
      row.identity_sha256,
      row.match_status,
      ...row.baseline_items.map(searchableItemText),
      ...row.comparison_items.map(searchableItemText),
    ].join(" "));
    return terms.every((term) => searchable.includes(term));
  });
}

function representativeNumber(
  row: ScreeningArchiveComparisonRow,
  sort: ScreeningArchiveComparisonSort,
  deltasComparable: boolean,
): number | null {
  if (sort === "baseline_score") {
    return finiteNumber(row.baseline_items[0]?.best_affinity_kcal_mol);
  }
  if (sort === "comparison_score") {
    return finiteNumber(row.comparison_items[0]?.best_affinity_kcal_mol);
  }
  if (sort === "score_delta") {
    return hasComparableScoreDelta(row, deltasComparable)
      ? finiteNumber(row.score_delta_kcal_mol)
      : null;
  }
  if (sort === "rank_delta") {
    return deltasComparable && row.match_status === "matched"
      ? finiteNumber(row.rank_delta)
      : null;
  }
  return null;
}

function stableIdentityOrder(
  left: ScreeningArchiveComparisonRow,
  right: ScreeningArchiveComparisonRow,
): number {
  const nameOrder = rowPrimaryDisplayName(left).localeCompare(
    rowPrimaryDisplayName(right),
    "zh-CN",
    { numeric: true, sensitivity: "base" },
  );
  if (nameOrder !== 0) return nameOrder;
  return left.identity_sha256.localeCompare(right.identity_sha256, "en");
}

export function sortScreeningArchiveComparisonRows<T extends ScreeningArchiveComparisonRow>(
  rows: readonly T[],
  sort: ScreeningArchiveComparisonSort,
  direction: ScreeningArchiveComparisonDirection = "asc",
  deltasComparable = true,
): T[] {
  const multiplier = direction === "desc" ? -1 : 1;

  return rows
    .map((row, index) => ({ row, index }))
    .sort((leftEntry, rightEntry) => {
      const left = leftEntry.row;
      const right = rightEntry.row;
      let comparison = 0;

      if (sort === "identity") {
        comparison = stableIdentityOrder(left, right) * multiplier;
      } else {
        const leftValue = representativeNumber(left, sort, deltasComparable);
        const rightValue = representativeNumber(right, sort, deltasComparable);
        // Missing or scientifically unavailable values remain at the end in
        // either direction; changing sort direction must not promote them.
        if (leftValue === null && rightValue !== null) return 1;
        if (leftValue !== null && rightValue === null) return -1;
        if (leftValue !== null && rightValue !== null) {
          comparison = (leftValue - rightValue) * multiplier;
        }
      }

      if (comparison !== 0) return comparison;
      const identityOrder = stableIdentityOrder(left, right);
      if (identityOrder !== 0) {
        return identityOrder;
      }
      return leftEntry.index - rightEntry.index;
    })
    .map(({ row }) => row);
}

export function clampScreeningArchiveComparisonPage(
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

export function paginateScreeningArchiveComparisonRows<T extends ScreeningArchiveComparisonRow>(
  rows: readonly T[],
  page: number,
  pageSize: number,
): ScreeningArchiveComparisonPage<T> {
  const safePageSize = Number.isFinite(pageSize) ? Math.max(1, Math.floor(pageSize)) : 1;
  const safePage = clampScreeningArchiveComparisonPage(page, rows.length, safePageSize);
  const offset = (safePage - 1) * safePageSize;
  const items = rows.slice(offset, offset + safePageSize);

  return {
    items,
    page: safePage,
    pageSize: safePageSize,
    pageCount: Math.max(1, Math.ceil(rows.length / safePageSize)),
    total: rows.length,
    start: items.length ? offset + 1 : 0,
    end: items.length ? offset + items.length : 0,
  };
}
