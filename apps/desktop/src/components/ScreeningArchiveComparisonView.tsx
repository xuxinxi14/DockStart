import { useEffect, useId, useMemo, useState } from "react";
import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  CaretLeft,
  CaretRight,
  CheckCircle,
  MagnifyingGlass,
  Scales,
  WarningCircle,
} from "@phosphor-icons/react";

import {
  comparisonDeltaRange,
  comparisonItemDisplayName,
  filterScreeningArchiveComparisonRows,
  hasComparableScoreDelta,
  paginateScreeningArchiveComparisonRows,
  rowPrimaryDisplayName,
  sortScreeningArchiveComparisonRows,
  type ScreeningArchiveComparisonDirection,
  type ScreeningArchiveComparisonItem,
  type ScreeningArchiveComparisonMatchStatus,
  type ScreeningArchiveComparisonRange,
  type ScreeningArchiveComparisonRow,
  type ScreeningArchiveComparisonSort,
} from "../utils/screeningArchiveComparison";
import ActionButton from "./ActionButton";
import StatusBadge from "./StatusBadge";
import "../styles/screening-archive-comparison.css";

export type ScreeningArchiveComparisonSummary = {
  screening_id?: string;
  status?: string;
  created_at?: string | null;
  finished_at?: string | null;
  archived_at?: string | null;
  best_affinity_kcal_mol?: number | null;
  counts?: {
    total?: number;
    succeeded?: number;
    failed?: number;
    unfinished?: number;
  };
};

export type ScreeningArchiveComparisonArchive = ScreeningArchiveComparisonSummary & {
  archive_id: string;
  /**
   * Current backend responses keep summary fields flat. The nested alias is
   * accepted so saved QA fixtures from the earlier archive browser remain
   * renderable while flat fields always take precedence.
   */
  summary?: ScreeningArchiveComparisonSummary;
  input_integrity?: unknown;
  protocol_fingerprint?: string | Readonly<Record<string, unknown>>;
};

export type ScreeningArchiveProtocolDifference = {
  field: string;
  label?: string;
  baseline?: unknown;
  comparison?: unknown;
};

export type ScreeningArchiveComparability = {
  status: "comparable" | "protocol_mismatch" | string;
  direct_score_comparison: boolean;
  differences?: ScreeningArchiveProtocolDifference[];
  message?: string;
};

export type ScreeningArchiveComparisonCounts = {
  baseline_total?: number;
  comparison_total?: number;
  matched?: number;
  baseline_only?: number;
  comparison_only?: number;
  ambiguous?: number;
  rows?: number;
};

export type ScreeningArchiveComparison = {
  baseline_archive: ScreeningArchiveComparisonArchive;
  comparison_archive: ScreeningArchiveComparisonArchive;
  comparability: ScreeningArchiveComparability;
  counts: ScreeningArchiveComparisonCounts;
  rows: ScreeningArchiveComparisonRow[];
};

type ScreeningArchiveComparisonViewProps = {
  comparison: ScreeningArchiveComparison;
  onBack?: () => void;
  pageSize?: number;
};

type MatchFilter = "all" | ScreeningArchiveComparisonMatchStatus;

const matchStatusLabels: Readonly<Record<string, string>> = {
  matched: "已匹配",
  baseline_only: "仅基线",
  comparison_only: "仅对照",
  ambiguous: "输入重复",
};

const matchStatusTones: Readonly<Record<string, "ok" | "warning" | "info" | "muted">> = {
  matched: "ok",
  baseline_only: "muted",
  comparison_only: "info",
  ambiguous: "warning",
};

const protocolFieldLabels: Readonly<Record<string, string>> = {
  scoring: "评分函数",
  scoring_function: "评分函数",
  receptor_sha256: "受体",
  flexible_receptor_sha256: "柔性受体",
  box: "对接箱体",
  "box.center_x": "箱体中心 X",
  "box.center_y": "箱体中心 Y",
  "box.center_z": "箱体中心 Z",
  "box.size_x": "箱体尺寸 X",
  "box.size_y": "箱体尺寸 Y",
  "box.size_z": "箱体尺寸 Z",
  center: "箱体中心",
  size: "箱体尺寸",
  exhaustiveness: "搜索彻底程度",
  num_modes: "输出构象数量",
  energy_range: "能量范围",
  seed: "随机种子",
  cpu: "CPU",
  max_evals: "最大评估次数",
  min_rmsd: "构象最小间距",
  spacing: "网格间距",
  verbosity: "日志详细程度",
  no_refine: "关闭最终精修",
  force_even_voxels: "强制偶数体素",
  vina_version: "Vina 版本",
  vina_sha256: "Vina 可执行文件",
  vina_binary_sha256: "Vina 可执行文件",
};

const rangeLabels: Readonly<Record<ScreeningArchiveComparisonRange, string>> = {
  all: "全部差值",
  lower: "评分降低",
  unchanged: "近似不变",
  higher: "评分升高",
  unavailable: "无可比差值",
};

const sortLabels: Readonly<Record<ScreeningArchiveComparisonSort, string>> = {
  identity: "配体名称",
  baseline_score: "基线评分",
  comparison_score: "对照评分",
  score_delta: "评分差值",
  rank_delta: "排名差值",
};

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatCount(value: unknown): string {
  const number = finiteNumber(value);
  return number === null ? "—" : String(Math.max(0, Math.floor(number)));
}

function formatArchiveTime(value?: string | null): string {
  if (!value) return "归档时间未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function archiveTitle(archive: ScreeningArchiveComparisonArchive): string {
  return archive.screening_id?.trim()
    || archive.summary?.screening_id?.trim()
    || archive.archive_id;
}

function effectiveArchiveSummary(
  archive: ScreeningArchiveComparisonArchive,
): ScreeningArchiveComparisonSummary {
  return {
    ...archive.summary,
    screening_id: archive.screening_id ?? archive.summary?.screening_id,
    status: archive.status ?? archive.summary?.status,
    created_at: archive.created_at ?? archive.summary?.created_at,
    finished_at: archive.finished_at ?? archive.summary?.finished_at,
    archived_at: archive.archived_at ?? archive.summary?.archived_at,
    best_affinity_kcal_mol:
      archive.best_affinity_kcal_mol ?? archive.summary?.best_affinity_kcal_mol,
    counts: archive.counts ?? archive.summary?.counts,
  };
}

function formatProtocolValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "未记录";
  if (typeof value === "boolean") return value ? "开启" : "关闭";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "无效数值";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function protocolFieldLabel(difference: ScreeningArchiveProtocolDifference): string {
  return difference.label?.trim()
    || protocolFieldLabels[difference.field]
    || difference.field.replace(/_/g, " ");
}

function archiveStatusLabel(status?: string): string {
  if (status === "completed") return "已完成";
  if (status === "completed_with_failures") return "完成（含失败项）";
  if (status === "canceled") return "已取消";
  return status || "已归档";
}

function itemStatusLabel(status?: string): string {
  if (status === "succeeded") return "成功";
  if (status === "failed") return "失败";
  if (status === "canceled") return "已取消";
  if (status === "interrupted") return "已中断";
  if (status === "running") return "运行中";
  if (status === "pending") return "待处理";
  return status || "状态未记录";
}

function itemSummary(item: ScreeningArchiveComparisonItem | undefined, duplicateCount: number) {
  if (!item) return <span className="screening-comparison-empty-value">未收录</span>;
  const score = finiteNumber(item.best_affinity_kcal_mol);
  const rank = finiteNumber(item.rank);
  return (
    <div className="screening-comparison-item">
      <strong title={comparisonItemDisplayName(item)}>{comparisonItemDisplayName(item)}</strong>
      <small>{item.item_id}{duplicateCount > 1 ? ` · 相同 SHA256 ${duplicateCount} 项` : ""}</small>
      <span>
        {itemStatusLabel(item.status)}
        {" · "}
        {score === null ? "评分不可用" : `${score.toFixed(3)} kcal/mol`}
        {rank === null ? "" : ` · 第 ${rank} 名`}
      </span>
    </div>
  );
}

function formatSigned(value: number, digits: number): string {
  const normalized = Object.is(value, -0) ? 0 : value;
  return `${normalized > 0 ? "+" : ""}${normalized.toFixed(digits)}`;
}

function DeltaCell({
  row,
  kind,
  comparable,
}: {
  row: ScreeningArchiveComparisonRow;
  kind: "score" | "rank";
  comparable: boolean;
}) {
  const scoreAvailable = hasComparableScoreDelta(row, comparable);
  const value = finiteNumber(kind === "score" ? row.score_delta_kcal_mol : row.rank_delta);
  const available = kind === "score"
    ? scoreAvailable
    : comparable && row.match_status === "matched" && value !== null;

  if (!available || value === null) {
    const reason = !comparable
      ? "协议不可比"
      : row.match_status === "ambiguous"
        ? "身份不唯一"
        : row.match_status !== "matched"
          ? "仅一侧存在"
          : "未记录";
    return (
      <span className="screening-comparison-delta is-unavailable">
        <strong>—</strong>
        <small>{reason}</small>
      </span>
    );
  }

  const range = kind === "score" ? comparisonDeltaRange(row, comparable) : null;
  const direction = value < 0 ? "lower" : value > 0 ? "higher" : "unchanged";
  const tone = kind === "score" ? range : direction;
  const context = kind === "score"
    ? value < 0 ? "数值降低" : value > 0 ? "数值升高" : "近似不变"
    : value < 0 ? "排名前移" : value > 0 ? "排名后移" : "排名不变";

  return (
    <span className={`screening-comparison-delta is-${tone}`}>
      <strong>{formatSigned(value, kind === "score" ? 3 : 0)}</strong>
      <small>{context}</small>
    </span>
  );
}

export default function ScreeningArchiveComparisonView({
  comparison,
  onBack,
  pageSize = 10,
}: ScreeningArchiveComparisonViewProps) {
  const comparable = comparison.comparability.direct_score_comparison === true;
  const searchId = useId();
  const matchId = useId();
  const rangeId = useId();
  const sortId = useId();
  const [query, setQuery] = useState("");
  const [matchStatus, setMatchStatus] = useState<MatchFilter>("all");
  const [range, setRange] = useState<ScreeningArchiveComparisonRange>("all");
  const [sort, setSort] = useState<ScreeningArchiveComparisonSort>(
    comparable ? "score_delta" : "identity",
  );
  const [direction, setDirection] = useState<ScreeningArchiveComparisonDirection>("asc");
  const [pageNumber, setPageNumber] = useState(1);

  const protocolDifferences = comparison.comparability.differences ?? [];

  const filteredRows = useMemo(
    () => filterScreeningArchiveComparisonRows(comparison.rows, {
      query,
      matchStatus,
      range,
      deltasComparable: comparable,
    }),
    [comparable, comparison.rows, matchStatus, query, range],
  );
  const sortedRows = useMemo(
    () => sortScreeningArchiveComparisonRows(filteredRows, sort, direction, comparable),
    [comparable, direction, filteredRows, sort],
  );
  const page = useMemo(
    () => paginateScreeningArchiveComparisonRows(sortedRows, pageNumber, pageSize),
    [pageNumber, pageSize, sortedRows],
  );

  useEffect(() => {
    setPageNumber(1);
  }, [
    query,
    matchStatus,
    range,
    sort,
    direction,
    comparison.baseline_archive.archive_id,
    comparison.comparison_archive.archive_id,
  ]);

  useEffect(() => {
    if (!comparable && (sort === "score_delta" || sort === "rank_delta")) {
      setSort("identity");
    }
    if (!comparable && !["all", "unavailable"].includes(range)) {
      setRange("all");
    }
  }, [comparable, range, sort]);

  useEffect(() => {
    if (page.page !== pageNumber) setPageNumber(page.page);
  }, [page.page, pageNumber]);

  return (
    <section className="screening-archive-comparison" aria-labelledby="screening-comparison-title">
      <header className="screening-comparison-heading">
        <div className="screening-comparison-heading-main">
          {onBack ? (
            <button type="button" className="screening-comparison-back" onClick={onBack}>
              <ArrowLeft aria-hidden="true" size={15} />
              返回历史归档
            </button>
          ) : null}
          <span>SCREENING ARCHIVE COMPARISON</span>
          <h3 id="screening-comparison-title">批量筛选归档比较</h3>
          <p>以配体文件内容的 SHA256 匹配同一冻结配体输入，基线与对照的角色不会随排序改变。</p>
        </div>
        <Scales aria-hidden="true" size={30} />
      </header>

      <div className="screening-comparison-archives">
        {([
          ["基线", comparison.baseline_archive],
          ["对照", comparison.comparison_archive],
        ] as const).map(([role, archive]) => {
          const summary = effectiveArchiveSummary(archive);
          return (
            <article key={role} className={`screening-comparison-archive is-${role === "基线" ? "baseline" : "comparison"}`}>
              <div>
                <span>{role}</span>
                <StatusBadge tone={summary.status === "completed_with_failures" ? "warning" : "ok"}>
                  {archiveStatusLabel(summary.status)}
                </StatusBadge>
              </div>
              <strong title={archiveTitle(archive)}>{archiveTitle(archive)}</strong>
              <code title={archive.archive_id}>{archive.archive_id}</code>
              <small>{formatArchiveTime(summary.archived_at)}</small>
              <dl>
                <div><dt>配体</dt><dd>{formatCount(summary.counts?.total)}</dd></div>
                <div>
                  <dt>最佳评分</dt>
                  <dd>
                    {finiteNumber(summary.best_affinity_kcal_mol) === null
                      ? "—"
                      : `${summary.best_affinity_kcal_mol?.toFixed(3)} kcal/mol`}
                  </dd>
                </div>
              </dl>
            </article>
          );
        })}
      </div>

      <section
        className={`screening-comparison-comparability ${comparable ? "is-comparable" : "is-mismatch"}`}
        aria-labelledby="screening-comparability-title"
      >
        <div className="screening-comparison-comparability-summary">
          {comparable
            ? <CheckCircle aria-hidden="true" size={20} weight="fill" />
            : <WarningCircle aria-hidden="true" size={20} weight="fill" />}
          <div>
            <strong id="screening-comparability-title">
              {comparable ? "关键协议一致，可以计算直接差值" : "关键协议不同，不计算直接差值"}
            </strong>
            <p>
              {comparison.comparability.message
                || (comparable
                  ? "表中 Δ 为对照减基线；仅对身份唯一且两侧均有有效结果的配体计算。"
                  : "仍可并排核对两侧原始结果，但评分和排名差值会保持为空。")}
            </p>
          </div>
        </div>

        {protocolDifferences.length ? (
          <details className="screening-comparison-protocol-differences" open={!comparable}>
            <summary>协议差异（{protocolDifferences.length}）</summary>
            <div className="screening-comparison-difference-table" role="table" aria-label="基线与对照协议差异">
              <div role="row">
                <span role="columnheader">字段</span>
                <span role="columnheader">基线</span>
                <span role="columnheader">对照</span>
              </div>
              {protocolDifferences.map((difference, index) => (
                <div role="row" key={`${difference.field}-${index}`}>
                  <strong role="cell">{protocolFieldLabel(difference)}</strong>
                  <code role="cell" title={formatProtocolValue(difference.baseline)}>
                    {formatProtocolValue(difference.baseline)}
                  </code>
                  <code role="cell" title={formatProtocolValue(difference.comparison)}>
                    {formatProtocolValue(difference.comparison)}
                  </code>
                </div>
              ))}
            </div>
          </details>
        ) : (
          <span className="screening-comparison-no-differences">关键协议字段未发现差异</span>
        )}
      </section>

      <section className="screening-comparison-counts" aria-label="比较条目摘要">
        <div><span>基线配体</span><strong>{formatCount(comparison.counts.baseline_total)}</strong></div>
        <div><span>对照配体</span><strong>{formatCount(comparison.counts.comparison_total)}</strong></div>
        <div><span>唯一匹配</span><strong>{formatCount(comparison.counts.matched)}</strong></div>
        <div><span>仅基线</span><strong>{formatCount(comparison.counts.baseline_only)}</strong></div>
        <div><span>仅对照</span><strong>{formatCount(comparison.counts.comparison_only)}</strong></div>
        <div><span>输入重复</span><strong>{formatCount(comparison.counts.ambiguous)}</strong></div>
      </section>

      <section className="screening-comparison-results" aria-labelledby="screening-comparison-results-title">
        <header>
          <div>
            <span>IDENTITY-MATCHED RESULTS</span>
            <h4 id="screening-comparison-results-title">逐配体结果</h4>
          </div>
          <small aria-live="polite">{filteredRows.length} / {comparison.rows.length} 个冻结输入</small>
        </header>

        <div className="screening-comparison-controls">
          <label className="screening-comparison-search" htmlFor={searchId}>
            <MagnifyingGlass aria-hidden="true" size={15} />
            <input
              id={searchId}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索配体名称、编号或 SHA256"
            />
          </label>
          <label htmlFor={matchId}>
            <span>匹配范围</span>
            <select
              id={matchId}
              value={matchStatus}
              onChange={(event) => setMatchStatus(event.target.value as MatchFilter)}
            >
              <option value="all">全部冻结输入</option>
              <option value="matched">唯一匹配</option>
              <option value="baseline_only">仅基线</option>
              <option value="comparison_only">仅对照</option>
              <option value="ambiguous">输入重复</option>
            </select>
          </label>
          <label htmlFor={rangeId}>
            <span>差值范围</span>
            <select
              id={rangeId}
              value={range}
              onChange={(event) => setRange(event.target.value as ScreeningArchiveComparisonRange)}
            >
              {(Object.entries(rangeLabels) as Array<[ScreeningArchiveComparisonRange, string]>)
                .map(([value, label]) => (
                  <option
                    value={value}
                    key={value}
                    disabled={!comparable && ["lower", "unchanged", "higher"].includes(value)}
                  >
                    {label}
                  </option>
                ))}
            </select>
          </label>
          <label htmlFor={sortId}>
            <span>排序</span>
            <select
              id={sortId}
              value={sort}
              onChange={(event) => setSort(event.target.value as ScreeningArchiveComparisonSort)}
            >
              {(Object.entries(sortLabels) as Array<[ScreeningArchiveComparisonSort, string]>)
                .map(([value, label]) => (
                  <option
                    value={value}
                    key={value}
                    disabled={!comparable && ["score_delta", "rank_delta"].includes(value)}
                  >
                    {label}
                  </option>
                ))}
            </select>
          </label>
          <button
            type="button"
            className="screening-comparison-direction"
            aria-label={direction === "asc" ? "当前升序，点击切换为降序" : "当前降序，点击切换为升序"}
            title={direction === "asc" ? "升序" : "降序"}
            onClick={() => setDirection((current) => current === "asc" ? "desc" : "asc")}
          >
            {direction === "asc"
              ? <ArrowUp aria-hidden="true" size={16} />
              : <ArrowDown aria-hidden="true" size={16} />}
          </button>
        </div>

        <div
          className="screening-comparison-table-wrap"
          role="region"
          aria-label="批量筛选归档比较结果表"
          tabIndex={0}
        >
          <table>
            <caption>
              Δ评分和 Δ排名均为对照减基线；负的排名差值表示对照归档中的名次前移。
            </caption>
            <thead>
              <tr>
                <th scope="col">冻结配体输入</th>
                <th scope="col">基线</th>
                <th scope="col">对照</th>
                <th scope="col">Δ评分</th>
                <th scope="col">Δ排名</th>
                <th scope="col">匹配</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((row) => (
                <tr key={`${row.identity_sha256}-${row.match_status}`}>
                  <td>
                    <strong title={rowPrimaryDisplayName(row)}>{rowPrimaryDisplayName(row)}</strong>
                    <code title={row.identity_sha256}>{row.identity_sha256.slice(0, 12)}…</code>
                  </td>
                  <td>{itemSummary(row.baseline_items[0], row.baseline_items.length)}</td>
                  <td>{itemSummary(row.comparison_items[0], row.comparison_items.length)}</td>
                  <td><DeltaCell row={row} kind="score" comparable={comparable} /></td>
                  <td><DeltaCell row={row} kind="rank" comparable={comparable} /></td>
                  <td>
                    <StatusBadge tone={matchStatusTones[row.match_status] ?? "muted"}>
                      {matchStatusLabels[row.match_status] ?? row.match_status}
                    </StatusBadge>
                  </td>
                </tr>
              ))}
              {!page.items.length ? (
                <tr className="screening-comparison-empty-row">
                  <td colSpan={6}>
                    <strong>没有符合当前条件的冻结配体输入</strong>
                    <span>可清除搜索词，或切换匹配与差值范围。</span>
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>

        <footer className="screening-comparison-pagination">
          <span>{page.total ? `${page.start}–${page.end} / ${page.total}` : "0 项"}</span>
          <div>
            <button
              type="button"
              aria-label="上一页"
              disabled={page.page <= 1}
              onClick={() => setPageNumber((current) => Math.max(1, current - 1))}
            >
              <CaretLeft aria-hidden="true" size={15} />
            </button>
            <strong>{page.page} / {page.pageCount}</strong>
            <button
              type="button"
              aria-label="下一页"
              disabled={page.page >= page.pageCount}
              onClick={() => setPageNumber((current) => Math.min(page.pageCount, current + 1))}
            >
              <CaretRight aria-hidden="true" size={15} />
            </button>
          </div>
        </footer>
      </section>

      <aside className="screening-comparison-science-note">
        <strong>解释边界</strong>
        <p>
          评分差值只反映两次归档在相同协议下的 Vina 数值变化。更低的 docking score
          不等同于真实结合更强，也不能证明药效、安全性或临床价值；结果仍需结合结构检查和实验验证。
        </p>
      </aside>

      {onBack ? (
        <div className="screening-comparison-footer-action">
          <ActionButton variant="secondary" onClick={onBack}>
            <ArrowLeft aria-hidden="true" size={15} />
            返回归档列表
          </ActionButton>
        </div>
      ) : null}
    </section>
  );
}
